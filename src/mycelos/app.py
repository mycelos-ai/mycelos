"""Application container — wires all services together."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mycelos.audit import SQLiteAuditLogger
from mycelos.config.generations import ConfigGenerationManager
from mycelos.llm.broker import LiteLLMBroker
from mycelos.llm.model_registry import ModelRegistry
from mycelos.memory.service import SQLiteMemoryService
from mycelos.security.capabilities import CapabilityTokenManager
from mycelos.security.credentials import EncryptedCredentialProxy
from mycelos.security.policies import PolicyEngine
from mycelos.config.blueprint import BlueprintManager
from mycelos.orchestrator import ChatOrchestrator
from mycelos.sessions.store import SessionStore
from mycelos.tasks.manager import TaskManager
from mycelos.workflows.parser import WorkflowParser
from mycelos.config.state_manager import StateManager
from mycelos.scheduler.schedule_manager import ScheduleManager
from mycelos.connectors.connector_registry import ConnectorRegistry
from mycelos.workflows.run_manager import WorkflowRunManager
from mycelos.workflows.workflow_registry import WorkflowRegistry
from mycelos.agents.auditor import AuditorAgent
from mycelos.agents.evaluator import EvaluatorAgent
from mycelos.agents.planner import PlannerAgent
from mycelos.agents.registry import AgentRegistry
from mycelos.storage.database import SQLiteStorage


class App:
    """Central application container.

    Creates and holds all service instances.
    Services are lazily initialized.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._storage: SQLiteStorage | None = None
        self._config_mgr: ConfigGenerationManager | None = None
        self._audit: SQLiteAuditLogger | None = None
        self._llm: LiteLLMBroker | None = None
        self._memory: SQLiteMemoryService | None = None
        self._credentials: EncryptedCredentialProxy | None = None
        self._capabilities: CapabilityTokenManager | None = None
        self._policy_engine: PolicyEngine | None = None
        self._agent_registry: AgentRegistry | None = None
        self._evaluator: EvaluatorAgent | None = None
        self._auditor: AuditorAgent | None = None
        self._planner: PlannerAgent | None = None
        self._orchestrator: ChatOrchestrator | None = None
        self._task_manager: TaskManager | None = None
        self._session_store: SessionStore | None = None
        self._workflow_parser: WorkflowParser | None = None
        self._blueprint: BlueprintManager | None = None
        self._workflow_run_manager: WorkflowRunManager | None = None
        self._connector_registry: ConnectorRegistry | None = None
        self._state_manager: StateManager | None = None
        self._model_registry: ModelRegistry | None = None
        self._workflow_registry: WorkflowRegistry | None = None
        self._schedule_manager: ScheduleManager | None = None
        self._mcp_manager: Any | None = None
        self._task_runner: Any | None = None
        self._proxy_client: Any | None = None
        self._knowledge_base: Any | None = None
        self._knowledge_organizer: Any | None = None
        self._model_updater: Any | None = None
        self._config_notifier: Any | None = None
        self._mount_registry: Any | None = None

    @property
    def storage(self) -> SQLiteStorage:
        if self._storage is None:
            self._storage = SQLiteStorage(self.data_dir / "mycelos.db")
        return self._storage

    @property
    def config(self) -> ConfigGenerationManager:
        if self._config_mgr is None:
            self._config_mgr = ConfigGenerationManager(self.storage, audit=self.audit)
        return self._config_mgr

    @property
    def audit(self) -> SQLiteAuditLogger:
        if self._audit is None:
            self._audit = SQLiteAuditLogger(self.storage)
        return self._audit

    @property
    def config_notifier(self):
        """Minimal notifier for registries to trigger config generations."""
        if self._config_notifier is None:
            from mycelos.config.notifier import ConfigNotifier
            self._config_notifier = ConfigNotifier(self.config, self.state_manager, self.audit)
        return self._config_notifier

    @property
    def llm(self) -> LiteLLMBroker:
        if self._llm is None:
            config = self.config.get_active_config() or {}
            default_model = config.get("default_model", "anthropic/claude-sonnet-4-6")
            # Pass credential proxy so keys are scoped per-call, not global
            try:
                proxy = self.credentials  # Triggers lazy init of EncryptedCredentialProxy
            except RuntimeError:
                proxy = None  # No master key — credentials unavailable
            # Build fallback chain from system defaults
            fallbacks = []
            try:
                chain = self.model_registry.resolve_models(None, "execution")
                fallbacks = [m for m in chain if m != default_model]
            except Exception:
                pass
            self._llm = LiteLLMBroker(
                default_model=default_model,
                credential_proxy=proxy,
                storage=self.storage,
                proxy_client=self._proxy_client,
                fallback_models=fallbacks,
            )
        return self._llm

    @property
    def model_registry(self) -> ModelRegistry:
        if self._model_registry is None:
            self._model_registry = ModelRegistry(self.storage, notifier=self.config_notifier)
        return self._model_registry

    def resolve_cheapest_model(self) -> str | None:
        """Resolve the cheapest available model for background tasks.

        Tries: classification models > workflow-agent > system default.
        Returns None if no models configured.
        """
        try:
            # Classification models are explicitly the cheapest
            models = self.model_registry.resolve_models(None, "classification")
            if models:
                return models[0]
            # Workflow-agent default
            models = self.model_registry.resolve_models("workflow-agent", "execution")
            if models:
                return models[0]
            # System default
            models = self.model_registry.resolve_models(None, "execution")
            if models:
                return models[0]
        except Exception:
            pass
        return None

    def resolve_strongest_model(self) -> str | None:
        """Resolve the strongest available model for complex reasoning.

        Tries: builder (opus) > auditor (opus) > system default.
        Returns None if no models configured.
        """
        try:
            models = self.model_registry.resolve_models("builder", "execution")
            if models:
                return models[0]
            models = self.model_registry.resolve_models("auditor-agent", "execution")
            if models:
                return models[0]
            models = self.model_registry.resolve_models(None, "execution")
            if models:
                return models[0]
        except Exception:
            pass
        return None

    @property
    def memory(self) -> SQLiteMemoryService:
        if self._memory is None:
            self._memory = SQLiteMemoryService(self.storage)
        return self._memory

    @property
    def proxy_client(self):
        """SecurityProxyClient for delegating HTTP/LLM/MCP calls through the proxy process.

        Only set in Gateway mode. CLI mode uses direct credential access instead.
        """
        return self._proxy_client

    def set_proxy_client(self, client) -> None:
        """Inject a SecurityProxyClient (called by gateway/server.py after proxy start)."""
        self._proxy_client = client
        # Update LLM broker if already initialized
        if self._llm is not None:
            self._llm._proxy_client = client

    @property
    def credentials(self):
        if self._credentials is None:
            proxy_url = os.environ.get("MYCELOS_PROXY_URL", "").strip()
            if proxy_url and self._proxy_client is not None:
                from mycelos.security.credentials import DelegatingCredentialProxy
                self._credentials = DelegatingCredentialProxy(
                    storage=self.storage,
                    proxy_client=self._proxy_client,
                )
            else:
                # Single-container mode (CLI, tests, or proxy client not yet wired)
                master_key = os.environ.get("MYCELOS_MASTER_KEY")
                if not master_key:
                    raise RuntimeError(
                        "MYCELOS_MASTER_KEY environment variable is not set. "
                        "This is required for credential encryption. "
                        "Set it before running Mycelos."
                    )
                self._credentials = EncryptedCredentialProxy(self.storage, master_key, notifier=self.config_notifier)
        return self._credentials

    @property
    def capabilities(self) -> CapabilityTokenManager:
        if self._capabilities is None:
            self._capabilities = CapabilityTokenManager(self.storage)
        return self._capabilities

    @property
    def policy_engine(self) -> PolicyEngine:
        if self._policy_engine is None:
            self._policy_engine = PolicyEngine(self.storage, notifier=self.config_notifier)
        return self._policy_engine

    @property
    def agent_registry(self) -> AgentRegistry:
        if self._agent_registry is None:
            self._agent_registry = AgentRegistry(self.storage, notifier=self.config_notifier)
        return self._agent_registry

    @property
    def evaluator(self) -> EvaluatorAgent:
        if self._evaluator is None:
            self._evaluator = EvaluatorAgent(llm=self.llm)
        return self._evaluator

    @property
    def auditor(self) -> AuditorAgent:
        if self._auditor is None:
            self._auditor = AuditorAgent(llm=self.llm)
        return self._auditor

    @property
    def planner(self) -> PlannerAgent:
        if self._planner is None:
            self._planner = PlannerAgent(llm=self.llm)
        return self._planner

    @property
    def task_manager(self) -> TaskManager:
        if self._task_manager is None:
            self._task_manager = TaskManager(self.storage)
        return self._task_manager

    @property
    def session_store(self) -> SessionStore:
        if self._session_store is None:
            self._session_store = SessionStore(self.data_dir / "conversations")
        return self._session_store

    @property
    def orchestrator(self) -> ChatOrchestrator:
        if self._orchestrator is None:
            self._orchestrator = ChatOrchestrator(
                llm=self.llm,
                classifier_model=self.resolve_cheapest_model(),
            )
            self._orchestrator.set_services(
                task_manager=self.task_manager,
                planner=self.planner,
                app=self,
            )
        return self._orchestrator

    @property
    def workflow_registry(self) -> WorkflowRegistry:
        if self._workflow_registry is None:
            self._workflow_registry = WorkflowRegistry(self.storage, notifier=self.config_notifier)
        return self._workflow_registry

    @property
    def workflow_parser(self) -> WorkflowParser:
        if self._workflow_parser is None:
            self._workflow_parser = WorkflowParser()
        return self._workflow_parser

    @property
    def blueprint(self) -> BlueprintManager:
        if self._blueprint is None:
            self._blueprint = BlueprintManager(self.config)
        return self._blueprint

    @property
    def connector_registry(self) -> ConnectorRegistry:
        if self._connector_registry is None:
            self._connector_registry = ConnectorRegistry(self.storage, notifier=self.config_notifier)
        return self._connector_registry

    @property
    def state_manager(self) -> StateManager:
        if self._state_manager is None:
            self._state_manager = StateManager(self.storage)
        return self._state_manager

    @property
    def schedule_manager(self) -> ScheduleManager:
        if self._schedule_manager is None:
            self._schedule_manager = ScheduleManager(self.storage, notifier=self.config_notifier)
        return self._schedule_manager

    @property
    def mount_registry(self):
        """MountRegistry with notifier wired for config generation."""
        if self._mount_registry is None:
            from mycelos.security.mounts import MountRegistry
            self._mount_registry = MountRegistry(self.storage, notifier=self.config_notifier)
        return self._mount_registry

    @property
    def mcp_manager(self):
        """MCP Connector Manager — lazily initialized.

        Only for CLI mode — Gateway uses proxy_client.mcp_* methods.
        """
        if self._mcp_manager is None:
            from mycelos.connectors.mcp_manager import MCPConnectorManager
            self._mcp_manager = MCPConnectorManager(
                credential_proxy=self.credentials,
                connector_registry=self.connector_registry,
            )
        return self._mcp_manager

    @property
    def task_runner(self):
        """Background task runner for async operations."""
        if self._task_runner is None:
            from mycelos.tasks.background_runner import BackgroundTaskRunner
            self._task_runner = BackgroundTaskRunner(self)
        return self._task_runner

    @property
    def workflow_run_manager(self) -> WorkflowRunManager:
        if self._workflow_run_manager is None:
            self._workflow_run_manager = WorkflowRunManager(self.storage)
        return self._workflow_run_manager

    @property
    def knowledge_base(self):
        """KnowledgeBase service — lazily initialized."""
        if self._knowledge_base is None:
            from mycelos.knowledge.service import KnowledgeBase
            self._knowledge_base = KnowledgeBase(self)
        return self._knowledge_base

    @property
    def knowledge_organizer(self):
        """Knowledge Organizer system handler — lazily initialized."""
        if self._knowledge_organizer is None:
            from mycelos.agents.handlers.knowledge_organizer_handler import KnowledgeOrganizerHandler
            self._knowledge_organizer = KnowledgeOrganizerHandler(self)
        return self._knowledge_organizer

    @property
    def model_updater(self):
        """Model Updater system handler — lazily initialized, no LLM."""
        if self._model_updater is None:
            from mycelos.agents.handlers.model_updater_handler import ModelUpdaterHandler
            self._model_updater = ModelUpdaterHandler(self)
        return self._model_updater

    def get_agent_handlers(self) -> dict:
        """Get all registered agent handlers, including persona agents from DB."""
        from mycelos.agents.handlers.mycelos_handler import MycelosHandler
        from mycelos.agents.handlers.builder_handler import BuilderHandler
        from mycelos.agents.handlers.persona_handler import load_persona_handlers

        handlers = {
            "mycelos": MycelosHandler(self),
            "builder": BuilderHandler(self),
        }
        # Load user-created persona agents from DB
        handlers.update(load_persona_handlers(self))
        return handlers

    def initialize(self) -> None:
        """Initialize database and apply first config generation."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.storage.initialize()

        if self.config.get_active_generation_id() is None:
            self.config.apply(
                {"version": "0.1.0", "default_model": "anthropic/claude-sonnet-4-6"},
                description="initial config",
                trigger="init",
            )
            self.audit.log("system.initialized")

        # Seed built-in workflows
        from mycelos.workflows.templates import seed_builtin_workflows
        seed_builtin_workflows(self)

    def initialize_with_config(
        self,
        default_model: str,
        provider: str,
    ) -> None:
        """Initialize with specific model and provider configuration."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.storage.initialize()

        config = {
            "version": "0.1.0",
            "default_model": default_model,
            "provider": provider,
        }
        self.config.apply(config, description="initial config", trigger="init")
        self.audit.log("system.initialized", details=config)
