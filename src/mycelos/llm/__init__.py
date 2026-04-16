def __getattr__(name: str) -> object:
    if name == "LiteLLMBroker":
        from mycelos.llm.broker import LiteLLMBroker
        return LiteLLMBroker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["LiteLLMBroker"]
