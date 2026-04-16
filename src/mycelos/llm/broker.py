"""LLM Broker / Conversation Service backed by LiteLLM.

Credentials are injected per-call via a scoped context manager,
never loaded globally into os.environ. Only the key needed for
the specific provider is set, and it is cleared immediately after.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mycelos.llm.cassette import CassetteRecorder

logger = logging.getLogger("mycelos.llm")


@dataclass
class LLMResponse:
    """Structured response from LLM."""
    content: str
    total_tokens: int
    model: str = ""
    tool_calls: list[dict] | None = None
    cost: float = 0.0


# Map provider prefixes to their environment variable names
_PROVIDER_ENV_VARS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def _resolve_api_key(
    model: str, credential_proxy: Any | None
) -> str | None:
    """Resolve the API key for a model from the credential proxy.

    Returns the key directly — never touches os.environ.
    Thread-safe: no global state mutation.
    """
    if credential_proxy is None:
        return None

    provider = model.split("/")[0] if "/" in model else _guess_provider(model)

    # Check if already in env (user set manually)
    env_var = _PROVIDER_ENV_VARS.get(provider)
    if env_var and os.environ.get(env_var):
        return None  # Already available, LiteLLM will find it

    try:
        cred = credential_proxy.get_credential(provider)
        if cred and "api_key" in cred:
            return cred["api_key"]
    except Exception as exc:
        logger.debug("credential lookup failed for provider=%s: %s", provider, exc)

    return None


def _guess_provider(model: str) -> str:
    """Guess provider from model name when no prefix is given."""
    lower = model.lower()
    if "claude" in lower:
        return "anthropic"
    if "gpt" in lower or lower.startswith("o1") or lower.startswith("o3") or lower.startswith("o4"):
        return "openai"
    if "gemini" in lower:
        return "gemini"
    return ""


class LiteLLMBroker:
    """LLM Broker using LiteLLM for multi-provider access.

    Provides: completion, token counting, cost tracking.
    All LLM access in the system goes through this broker.

    Credentials are scoped per-call: only the needed provider key
    is set in the environment for the duration of the API call,
    then immediately cleared. No global env pollution.
    """

    _RETRIABLE_PATTERNS = ("limit", "quota", "budget", "429", "401", "rate")

    def __init__(
        self,
        default_model: str = "anthropic/claude-sonnet-4-6",
        credential_proxy: Any | None = None,
        storage: Any | None = None,
        proxy_client: Any | None = None,
        fallback_models: list[str] | None = None,
        recorder: "CassetteRecorder | None" = None,
    ):
        self.default_model = default_model
        self.fallback_models = fallback_models or []
        self.total_cost: float = 0.0
        self.total_tokens: int = 0
        self._credential_proxy = credential_proxy
        self._storage = storage
        self._proxy_client = proxy_client
        self._current_purpose: str = "chat"  # Set by callers for tracking
        self._recorder = recorder

    def _ensure_prefix(self, model: str) -> str:
        """Ensure model ID has provider prefix (e.g. 'anthropic/claude-...')."""
        if "/" not in model:
            provider = _guess_provider(model)
            if provider:
                return f"{provider}/{model}"
        return model

    def _is_retriable(self, error: Exception) -> bool:
        """Check if an error should trigger fallback to next model."""
        err_str = str(error).lower()
        return any(p in err_str for p in self._RETRIABLE_PATTERNS)

    def complete(
        self,
        messages: list[dict],
        model: str | None = None,
        tools: list[dict] | None = None,
        stream: bool = False,
    ) -> LLMResponse:
        """Send completion request through LiteLLM or SecurityProxy.

        On retriable errors (rate limit, quota), automatically falls back
        to the next model in fallback_models.
        """
        chosen_model = self._ensure_prefix(model or self.default_model)
        models_to_try = [chosen_model] + [
            self._ensure_prefix(m) for m in self.fallback_models
            if self._ensure_prefix(m) != chosen_model
        ]
        last_error: Exception | None = None

        for attempt_model in models_to_try:
            try:
                if self._recorder is not None:
                    return self._recorder.intercept(
                        model=attempt_model,
                        messages=messages,
                        tools=tools,
                        real_call=lambda m=attempt_model: self._complete_single(
                            m, messages, tools, stream
                        ),
                    )
                return self._complete_single(attempt_model, messages, tools, stream)
            except Exception as e:
                from mycelos.llm.cassette import CassetteMissError
                if isinstance(e, CassetteMissError):
                    raise
                if self._is_retriable(e) and attempt_model != models_to_try[-1]:
                    import logging
                    logging.getLogger("mycelos.llm").warning(
                        "Model %s unavailable (%s), falling back...", attempt_model, type(e).__name__
                    )
                    last_error = e
                    continue
                raise

        raise last_error or RuntimeError("No models available")

    def _complete_single(
        self,
        chosen_model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream: bool = False,
    ) -> LLMResponse:
        """Single-model completion (no fallback)."""

        if self._proxy_client is not None:
            result = self._proxy_client.llm_complete(
                model=chosen_model,
                messages=messages,
                tools=[t for t in (tools or [])],
                stream=False,
            )
            # Handle proxy errors — raise retriable ones for failover
            if "error" in result and "content" not in result:
                error_msg = result["error"]
                if self._is_retriable(RuntimeError(error_msg)):
                    raise RuntimeError(f"LLM proxy error: {error_msg}")
                return LLMResponse(
                    content=f"[Error: {error_msg}]",
                    total_tokens=0,
                    model=chosen_model,
                )
            content = result.get("content", "")
            usage = result.get("usage", {})
            input_tokens = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
            total = usage.get("total_tokens", input_tokens + output_tokens)

            self.total_tokens += total

            if self._storage is not None:
                try:
                    self._storage.execute(
                        """INSERT INTO llm_usage
                           (model, input_tokens, output_tokens, total_tokens, cost, purpose)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (chosen_model, input_tokens, output_tokens, total, result.get("cost", 0), self._current_purpose),
                    )
                except Exception as exc:
                    logger.warning("failed to persist llm_usage row: %s", exc)

            # Calculate cost from litellm's cost database
            proxy_cost = 0.0
            try:
                import litellm as _lt
                cost_key = chosen_model
                cost_info = _lt.model_cost.get(cost_key, {})
                if not cost_info and "/" in cost_key:
                    cost_info = _lt.model_cost.get(cost_key.split("/", 1)[1], {})
                ic = cost_info.get("input_cost_per_token", 0) or 0
                oc = cost_info.get("output_cost_per_token", 0) or 0
                proxy_cost = (input_tokens * ic) + (output_tokens * oc)
                self.total_cost += proxy_cost
            except Exception as exc:
                logger.debug("cost calculation failed: %s", exc)

            return LLMResponse(
                content=content,
                total_tokens=total,
                model=result.get("model", chosen_model),
                tool_calls=result.get("tool_calls"),
                cost=proxy_cost,
            )

        import litellm

        kwargs: dict[str, Any] = {
            "model": chosen_model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        if stream:
            kwargs["stream"] = True

        # Pass API key directly — thread-safe, no os.environ mutation
        api_key = _resolve_api_key(chosen_model, self._credential_proxy)
        if api_key:
            kwargs["api_key"] = api_key

        response = litellm.completion(**kwargs)

        if stream:
            return response  # type: ignore — caller handles streaming

        choice = response.choices[0]
        usage = response.usage

        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        total = usage.total_tokens

        self.total_tokens += total

        # Calculate cost from litellm's cost database
        cost = 0.0
        try:
            # litellm.model_cost uses model name without provider prefix
            cost_key = chosen_model
            cost_info = litellm.model_cost.get(cost_key, {})
            if not cost_info and "/" in cost_key:
                # Try without provider prefix: "anthropic/claude-sonnet-4-6" → "claude-sonnet-4-6"
                cost_info = litellm.model_cost.get(cost_key.split("/", 1)[1], {})
            input_cost = cost_info.get("input_cost_per_token", 0) or 0
            output_cost = cost_info.get("output_cost_per_token", 0) or 0
            cost = (input_tokens * input_cost) + (output_tokens * output_cost)
            self.total_cost += cost
        except Exception:
            pass

        # Persist usage if storage available
        if self._storage is not None:
            try:
                self._storage.execute(
                    """INSERT INTO llm_usage
                       (model, input_tokens, output_tokens, total_tokens, cost, purpose)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (chosen_model, input_tokens, output_tokens, total, cost, self._current_purpose),
                )
            except Exception as exc:
                logger.debug("cost calculation failed: %s", exc)  # Don't fail on tracking errors

        tool_calls = None
        if hasattr(choice.message, "tool_calls") and choice.message.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in choice.message.tool_calls
            ]

        return LLMResponse(
            content=choice.message.content or "",
            total_tokens=usage.total_tokens,
            model=chosen_model,
            tool_calls=tool_calls,
            cost=cost,
        )

    def complete_stream(
        self,
        messages: list[dict],
        model: str | None = None,
        tools: list[dict] | None = None,
    ):
        """Stream completion — yields text chunks as they arrive.

        Yields:
            str chunks of the response text.

        After iteration, self._last_stream_tokens and self._last_stream_model
        are set for the caller to read.
        """
        chosen_model = self._ensure_prefix(model or self.default_model)

        if self._proxy_client is not None:
            self._last_stream_model = chosen_model
            self._last_stream_tokens = 0
            self._last_stream_tool_calls = None

            result = self._proxy_client.llm_complete(
                model=chosen_model,
                messages=messages,
                tools=[t for t in (tools or [])],
                stream=True,
            )
            # result is an iterator of SSE chunks when stream=True
            # Parse and yield content chunks
            for chunk_data in result:
                if isinstance(chunk_data, dict):
                    content = chunk_data.get("content", "")
                    if content:
                        yield content
                elif isinstance(chunk_data, str):
                    yield chunk_data
            return

        import litellm

        # chosen_model already set with prefix on line 296 — don't re-assign without prefix

        kwargs: dict[str, Any] = {
            "model": chosen_model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools

        self._last_stream_tokens = 0
        self._last_stream_model = chosen_model
        self._last_stream_tool_calls: list[dict] | None = None

        full_content = ""
        # Pass API key directly — thread-safe, no os.environ mutation
        api_key = _resolve_api_key(chosen_model, self._credential_proxy)
        if api_key:
            kwargs["api_key"] = api_key

        response = litellm.completion(**kwargs)
        for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if hasattr(delta, "content") and delta.content:
                full_content += delta.content
                yield delta.content

            # Track tool calls in stream
            if hasattr(delta, "tool_calls") and delta.tool_calls:
                if self._last_stream_tool_calls is None:
                    self._last_stream_tool_calls = []
                for tc in delta.tool_calls:
                    if hasattr(tc, "id") and tc.id:
                        self._last_stream_tool_calls.append({
                            "id": tc.id,
                            "function": {
                                "name": getattr(tc.function, "name", ""),
                                "arguments": getattr(tc.function, "arguments", ""),
                            },
                        })

            # Track usage from final chunk
            if hasattr(chunk, "usage") and chunk.usage:
                self._last_stream_tokens = chunk.usage.total_tokens

        # Track cost
        if self._last_stream_tokens and self._storage:
            try:
                cost_key = chosen_model
                cost_info = litellm.model_cost.get(cost_key, {})
                if not cost_info and "/" in cost_key:
                    cost_info = litellm.model_cost.get(cost_key.split("/", 1)[1], {})
                input_cost = cost_info.get("input_cost_per_token", 0) or 0
                output_cost = cost_info.get("output_cost_per_token", 0) or 0
                cost = self._last_stream_tokens * ((input_cost + output_cost) / 2)
                self._storage.execute(
                    """INSERT INTO llm_usage
                       (model, input_tokens, output_tokens, total_tokens, cost, purpose)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (chosen_model, 0, 0, self._last_stream_tokens, cost, self._current_purpose),
                )
            except Exception as exc:
                logger.debug("cost calculation failed: %s", exc)

    def count_tokens(
        self,
        messages: list[dict],
        model: str | None = None,
    ) -> int:
        """Count tokens for messages without making an API call."""
        import litellm

        chosen_model = self._ensure_prefix(model or self.default_model)
        return litellm.token_counter(model=chosen_model, messages=messages)


# Alias for backward compatibility and test convenience
LLMBroker = LiteLLMBroker
