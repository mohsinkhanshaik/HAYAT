from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any

from app.agent.core import Agent
from app.assistant import AssistantService
from app.agent.conversation import ConversationService
from app.agent.executor import Executor
from app.agent.planner import Planner
from app.agent.verifier import Verifier
from app.agents.coder import CodingAgent
from app.agents.developer import DeveloperAgent
from app.development.code_monitor import CodeMonitor, register_code_monitor_tools
from app.agents.computer_agent import ComputerAgent
from app.agents.manager import ManagerAgent
from app.agents.repair import RepairAgent
from app.learning.continuous import ContinuousLearner, LearningBudget
from app.agents.researcher import ResearchAgent
from app.agents.reviewer import ReviewerAgent
from app.automation.jobs import JobRepository
from app.automation.scheduler import AgentScheduler
from app.automation.event_engine import EventTriggerEngine, register_event_trigger_tools
from app.config import PROJECT_ROOT, ensure_data_directories, load_settings
from app.self_change import SelfChangeTracker
from app.supervisor import OllamaService, RuntimeSupervisor
from app.apprenticeship import Apprenticeship
from app.attachments import AttachmentReader
from app.video import VideoAnalyzer, register_video_tools
from app.memory.compaction import MemoryCompactor
from app.context_budget import ContextBudget
from app.models.council import Council
from app.models.escalation import Escalation, load_escalation
from app.recovery import CrashRecovery
from app.selfimprove import SelfImprovement
from app.thoughts import ThoughtStream
from app.turns import TurnLog
from app.safety.authority import AuthorityManager
from app.safety.authority_store import AuthorityStore
from app.safety.approval_records import ApprovalLifecycle
from app.failures import FailureLog
from app.memory.database import MemoryDatabase
from app.intent.router import IntentRouter
from app.models.base import ModelProvider
from app.models.router import ModelRouter
from app.perception.vision import VisionAnalyzer
from app.research.manager import ResearchManager
from app.research.documents import PublicDocumentLearner
from app.session import SessionState
from app.safety.approvals import ApprovalCallback, console_approval, deny_unattended
from app.safety.permissions import PermissionManager
from app.skills.registry import SkillRegistry
from app.skills.promoter import SkillPromoter
from app.tools.browser import BrowserTools, register_browser_tools
from app.tools.applications import register_application_tools
from app.tools.computer import ComputerTools, register_computer_tools
from app.tools.filesystem import register_filesystem_tools
from app.tools.powershell import register_powershell_tools
from app.tools.python_runner import register_python_tools
from app.tools.research import register_research_tools
from app.tools.registry import ToolRegistry
from app.tools.understanding import ToolUnderstanding
from app.tools.introspection import register_introspection_tools, tool_reference
from app.tools.memory import register_memory_tools
from app.metacognition import AdaptiveControlPlane
from app.tools.metacognition import register_metacognition_tools
from app.perception.camera.session import CameraSessions
from app.tools.images import register_image_tools
from app.tools.packages import register_package_tools
from app.tools.system import register_system_tools
from app.tools.terminal import register_terminal_tools
from app.tools.vision import register_vision_tools
from app.voice.manager import VoiceManager
from app.storage.manager import StorageManager
from app.modes import ModeManager
from app.projects import ProjectManager
from app.state import AgentStateService
from app.tools.self_state import register_self_state_tool
from app.learning.manager import LearningManager
from app.training.pipeline import TrainingDataPipeline
from app.training.lifecycle import PrivateFineTuneLifecycle
from app.models.hayat import HayatModelManager
from app.memory.embeddings import EmbeddingRegistry
from app.memory.retrieval import RetrievalIndex
from app.models.capabilities import ModelCapabilityRegistry
from app.models.intelligent_router import IntelligentModelRouter
from app.evaluation.manager import EvaluationManager
from app.policy.cost import CostPolicy
from app.downloads.manager import DependencyManager, DownloadManager, register_download_tools
from app.perception.camera import CameraManager, register_camera_tools
from app.perception.displays import DisplayManager, register_display_tools
from app.perception.clipboard import ClipboardManager, register_clipboard_tools
from app.observability import ObservabilityManager
from app.observability.tool_benchmark import ToolBenchmark
from app.models.resources import ModelResourceManager
from app.incidents import IncidentManager, register_incident_tools
from app.events import EventBus
from app.security import DefensiveSecurityAuditor
from app.learning.labs import LabManager, register_lab_tools
from app.backups import BackupManager, register_backup_tools
from app.integrations.secrets import SecretBroker, register_secret_tools
from app.integrations.connectors import ConnectorGateway, ConnectorRegistry, register_connector_tools
from app.integrations.mcp_gateway import MCPGateway, register_builtin_web_research, register_mcp_tools
from app.integrations.browser_bridge import BrowserExtensionBridge, register_browser_bridge_tools
from app.brain_mesh import BrainMesh
from app.tools.brain_mesh import register_brain_mesh_tools
from app.logging import log_swallowed

_log = logging.getLogger(__name__)


@dataclass
class Runtime:
    agent: Agent
    registry: ToolRegistry
    memory: MemoryDatabase
    router: ModelRouter
    model: ModelProvider | None
    browser: BrowserTools
    computer: ComputerTools
    skills: SkillRegistry
    scheduler: AgentScheduler
    manager: ManagerAgent
    conversation: ConversationService
    vision: VisionAnalyzer | None
    promoter: SkillPromoter
    research: ResearchManager
    storage: StorageManager
    session: SessionState
    intent_router: IntentRouter
    modes: ModeManager
    projects: ProjectManager
    state: AgentStateService
    learning: LearningManager
    training: TrainingDataPipeline
    model_capabilities: ModelCapabilityRegistry
    intelligent_router: IntelligentModelRouter
    embeddings: EmbeddingRegistry
    retrieval: RetrievalIndex
    evaluation: EvaluationManager
    cost_policy: CostPolicy
    downloads: DownloadManager
    dependencies: DependencyManager
    camera: CameraManager
    displays: DisplayManager
    clipboard: ClipboardManager
    observability: ObservabilityManager
    model_resources: ModelResourceManager
    incidents: IncidentManager
    events: EventBus
    security: DefensiveSecurityAuditor
    labs: LabManager
    backups: BackupManager
    proactive_mode: str
    turns: TurnLog
    authority: AuthorityManager
    failures: FailureLog
    secret_broker: Any = None
    connectors: Any = None
    connector_gateway: Any = None
    mcp_gateway: Any = None
    reverse_engineer: Any = None
    browser_extension: Any = None
    event_triggers: Any = None
    code_monitor: Any = None
    hayat_model: Any = None
    training_lifecycle: Any = None
    learning_architecture: Any = None
    public_documents: Any = None
    tool_benchmarks: Any = None
    assistant: AssistantService | None = None
    voice: VoiceManager | None = None
    supervisor: Any = None
    learner: Any = None
    camera_sessions: Any = None
    thoughts: Any = None
    #: Set by HayatGUI when a desktop window owns this session's output,
    #: so console-only advice like "press Ctrl+C" is not shown in a window
    #: that has no Ctrl+C.
    gui: Any = None
    apprenticeship: Any = None
    #: app/intelligence/discovery.py SourceScout.
    discovery: Any = None
    #: app/experience.py ExperienceMemory and app/situation.py SituationModel.
    experience: Any = None
    situation: Any = None
    improvement: Any = None
    packages: Any = None
    tool_understanding: Any = None
    context_budget: Any = None
    attachments: Any = None
    compaction: Any = None
    #: Knows whether the last run ended or was cut off, and what it left
    #: half-finished. Its marker is cleared only after successful cleanup.
    recovery: Any = None
    #: Local business books (app/tools/business.py) and the digital-product store.
    business: Any = None
    products: Any = None
    #: Starting a business: app/tools/launch.py Launchpad.
    launch: Any = None
    #: Owner-confirmed outward actions (app/safety/confirmations.py) and website publishing.
    confirmations: Any = None
    publisher: Any = None
    #: Today's business brief (app/tools/brief.py).
    brief: Any = None
    #: The owner's own sending account (app/messaging.py EmailSender).
    email: Any = None
    intelligence: Any = None
    #: Read-only crypto markets and blockchain state (app/markets.py).
    markets: Any = None
    #: Price alerts checked in the background (app/market_alerts.py).
    price_alerts: Any = None
    #: Job search, matching and applications (app/jobs/), and its automatic round.
    jobs: Any = None
    job_watcher: Any = None
    #: The owner's connected accounts (app/accounts.py).
    accounts: Any = None
    #: Read-only chain data and explorer (app/web3.py); the agent-web inspector (app/web4.py).
    web3: Any = None
    agent_web: Any = None
    #: The owner's holdings elsewhere, valued in USD (app/portfolio.py); holds and trades nothing.
    portfolio: Any = None
    #: Pots, bills, rules and money decisions on the business wallet (app/money_desk.py); the owner approves each.
    money_desk: Any = None
    executive: Any = None
    dual_brain: Any = None
    inquiry: Any = None
    invention: Any = None
    _closed: bool = field(default=False, init=False, repr=False)
    shutdown_errors: list[dict[str, str]] = field(default_factory=list, init=False)
    safe_mode: bool = field(default=False, init=False)

    def status(self) -> dict:
        storage = self.storage.status()
        operational = self.state.snapshot()
        knowledge_stats = self.research.knowledge.stats()
        queue_counts = self.learner.queue_status(1).get("counts", {}) if self.learner else {}
        voice_ready = False
        if self.voice:
            try:
                hardware = Path("data/benchmarks/voice/hardware.json")
                verified = json.loads(hardware.read_text(encoding="utf-8")) if hardware.is_file() else {}
                voice_ready = bool(self.voice.devices.list_devices().get("microphones")) and verified.get("passed") is True
            except Exception:
                voice_ready = False
        return {
            "mode": " + ".join(mode.value for mode in self.modes.configured),
            "cost_mode": self.cost_policy.mode,
            "provider": getattr(self.model, "name", None) or "None",
            "model": getattr(self.model, "model", None) or "None",
            "conversation": "Ready" if self.model else "Limited (no model)",
            "reasoning": "Ready" if self.agent.planner.model else "Deterministic only",
            "router": "Ready",
            "memory": (f"Hybrid lexical + neural ({self.embeddings.active.model})" if self.embeddings.active and self.embeddings.active.available else "Hybrid lexical + hashed semantic"),
            "tools": len(self.registry.names()),
            "voice": "Ready" if voice_ready else "Configured, hardware unverified",
            "vision": f"Ready ({getattr(getattr(self.vision, 'provider', None), 'model', 'configured')})" if self.vision else "Disabled",
            "camera": "Ready" if self.camera.status()["devices_detected"] else "Unavailable",
            "proactive": self.proactive_mode,
            "research": "Ready" if self.research.knowledge.path.is_file() else "Error",
            "learning": (
                f"Ready ({knowledge_stats.get('knowledge', 0)} summaries, "
                f"{knowledge_stats.get('claims', 0)} claims, {queue_counts.get('pending', 0)} queued; "
                f"{self.storage.human(storage.get('learning_allocation_bytes') or 0)} allocation)"
                if self.learning.path.is_file() else "Error"
            ),
            "evaluation": "Ready",
            "prefrontal_cortex": "Ready" if self.executive is not None else "Unavailable",
            "dual_brain": ("Disabled" if not (self.dual_brain and self.dual_brain.enabled) else
                           "Enabled for Deep modes and hard questions" if self.dual_brain.deep_turns else
                           "Enabled for substantive Deep turns"),
            "self_inquiry": ("Disabled" if not (self.inquiry and self.inquiry.enabled) else
                             "Questions itself before hard answers" if self.inquiry.scope == "deep_turns" else
                             "Questions itself in Deep modes"),
            "invention": ("Ready" if self.model else "Ready for fitting and simulation; a local model adds proposals")
            if self.invention else "Unavailable",
            "training": "Dataset pipeline ready; fine-tuning disabled",
            "storage": f"{self.storage.human(storage['used_bytes'])} / {self.storage.human(storage['maximum_total_bytes'])}",
            "active_task": operational["active_task"] or operational["activity"],
            "active_project": (operational["active_project"] or {}).get("root") or "None",
            "internet": "Available" if operational["internet"]["available"] else "Unavailable",
            "connectors": len(self.connectors.list()) if self.connectors else 0,
            "mcp_servers": len(self.mcp_gateway.list()) if self.mcp_gateway else 0,
            "browser_extension": self.browser_extension.status() if self.browser_extension else {"running": False},
            "proactive_rules": len(self.event_triggers.list()) if self.event_triggers else 0,
            "code_monitor": (f"Running ({len(self.projects.projects)} registered projects)"
                             if self.code_monitor and self.code_monitor.running else "Available on demand"),
        }

    def status_text(self) -> str:
        values = self.status()
        ordered = ["mode", "cost_mode", "router", "provider", "model", "conversation", "reasoning", "memory", "tools", "voice", "vision", "camera", "research", "learning", "evaluation", "training", "code_monitor", "active_task", "active_project", "internet", "storage"]
        return "AI Ready\n" + "\n".join(f"{name.replace('_', ' ').title()}: {values[name]}" for name in ordered)

    def close(self) -> None:
        """Attempt every cleanup; preserve crash evidence if any service fails."""
        if self._closed:
            return
        self._closed = True
        from app.logging import redact

        def cleanup(component, operation):
            try:
                if operation() is False:
                    raise RuntimeError("Service did not finish stopping within its deadline")
            except Exception as exc:
                error = {"component": component, "error": redact(f"{type(exc).__name__}: {exc}")}
                self.shutdown_errors.append(error)
                try:
                    self.events.publish("SERVICE_DEGRADED", "shutdown", error)
                except Exception:
                    log_swallowed(_log, "publishing a shutdown degradation")

        teams = getattr(self, 'teams', None)
        if teams is not None:
            cleanup('worker teams', teams.cancel_all)
        for name in ("colibri_service", "screen_reader", "voice", "event_triggers", "browser_extension", "code_monitor", "price_alerts", "job_watcher"):
            service = getattr(self, name, None)
            if service is not None:
                cleanup(name, service.stop)
        if self.reverse_engineer is not None:
            cleanup('reverse engineer', self.reverse_engineer.close)
        if self.intelligence is not None:
            cleanup('intelligence', self.intelligence.close)
        if self.invention is not None:
            cleanup('invention', self.invention.close)
        # A runtime assembled in parts (tests, safe mode) may have no registry.
        video_workflow = getattr(getattr(self, "registry", None), "video_workflow", None)
        if video_workflow is not None:
            cleanup('video result links', video_workflow.close)
        sensing = getattr(getattr(self, "registry", None), "sensing", None)
        if sensing is not None:
            cleanup('wifi sensing monitor', sensing.stop)
        csi = getattr(getattr(self, "registry", None), "csi", None)
        if csi is not None:
            cleanup('csi connections', csi.close_all)
        masks = getattr(getattr(self, "registry", None), "privacy_masks", None)
        if masks is not None:
            # Tor stops with HAYAT; the owner's choice to keep HAYAT masked is remembered for the next launch.
            from app.privacy_masks import ROUTE
            if getattr(ROUTE, "_starter", None) == masks.tor.ensure_started:
                ROUTE.set_starter(None)  # no on-demand restart while (or after) HAYAT closes
            cleanup('tor mask', lambda: masks.tor.stop(remember=False) and None)
        if self.learner is not None:
            cleanup("learner.request_stop", lambda: self.learner.request_stop("runtime closing"))
            cleanup("learner", lambda: self.learner.stop(timeout=5))
        for name, method in (("camera", "close"), ("scheduler", "stop"),
                             ("browser", "close"), ("research", "close")):
            service = getattr(self, name, None)
            if service is not None:
                cleanup(name, getattr(service, method))
        tutor_voice = getattr(getattr(getattr(self, "learning", None), "language", None), "voice", None)
        if tutor_voice is not None and hasattr(tutor_voice, "close"):
            cleanup("arabic voice", tutor_voice.close)
        if getattr(self, "languages", None) is not None:
            cleanup("language voices", self.languages.close)
        # The event writer remains available until the other cleanup attempts
        # are recorded. A failed shutdown keeps the liveness marker on disk.
        cleanup("events", self.events.close)
        if self.recovery is not None and not self.shutdown_errors:
            cleanup("recovery", self.recovery.mark_clean_exit)


def known_contacts(business, memory_path) -> str:
    """Contact details HAYAT has a record of: saved leads and clients, the owner's configured email
    account, and memories or owner messages that carry an address or number. A reply's phone numbers and
    emails must match this (app.action_claims.without_unrecorded_contacts); the owner's own addresses must
    never be blanked as invented."""
    import json
    import sqlite3

    from app.messaging import EmailSender

    parts = [business.contact_record()]
    try:
        account = EmailSender.account_path()
        if account.is_file():
            data = json.loads(account.read_text(encoding="utf-8"))
            parts.append(" ".join(str(data.get(key, "")) for key in ("from_address", "username")))
    except (OSError, ValueError):
        pass  # no configured account is the normal case
    from app.action_claims import contact_tokens

    try:
        db = sqlite3.connect(memory_path, timeout=5)
        try:
            # Only the addresses and numbers themselves: the owner's long pastes made this 8.8 MB of text.
            wanted = ("(content LIKE '%@%' OR content GLOB '*[0-9][0-9][0-9]*[0-9][0-9][0-9][0-9]*') "
                      "AND length(content) < 4000")
            for table, extra in (("memories", ""), ("conversations", "role='user' AND ")):
                for (content,) in db.execute(f"SELECT content FROM {table} WHERE {extra}{wanted} ORDER BY id DESC LIMIT 1000"):
                    parts.extend(contact_tokens(content))
        finally:
            db.close()
    except sqlite3.Error:
        log_swallowed(_log, "reading remembered contact details")
    return "\n".join(str(part) for part in parts if part)


def build_runtime(interactive_approvals: bool = False, use_model: bool = True,
                  start_background_services: bool = True, safe_mode: bool = False) -> Runtime:
    ensure_data_directories()
    # HAYAT belongs to one owner; refuse to start if that identity was tampered with (app/identity.py).
    from app.identity import verify_owner
    verify_owner()
    # NLTK's model-file sandbox bypass (PYSEC-2026-3740, no fixed release yet) is closed before anything can import it.
    from app.security.nltk_guard import install as guard_nltk
    guard_nltk()
    settings = load_settings()
    from app.policy.cost import background_autostart_allowed
    background_disabled = (safe_mode or not start_background_services or "PYTEST_CURRENT_TEST" in os.environ or
                           not background_autostart_allowed(settings) or
                           os.getenv("HAYAT_DISABLE_BACKGROUND", "").strip().lower() in {"1", "true", "yes"})
    cost_policy = CostPolicy.from_settings(settings.get("cost_policy", {})); cost_policy.assert_zero_cost()
    memory = MemoryDatabase()
    memory.initialize()
    permissions = PermissionManager()
    storage = StorageManager(settings.get("storage", {}))
    events = EventBus()
    turns = TurnLog()
    authority_store = AuthorityStore()
    authority = AuthorityManager(
        # On by default. This is a single-user machine the owner controls, and
        # the alternative -- prompting for every file write and app launch --
        # produced exactly the click-through habit that makes the prompts that
        # DO matter useless. A stored preference still wins over this default.
        owner_mode=bool(settings.get("owner_mode", {}).get("enabled", True)),
        event_bus=events, store=authority_store,
    )
    failures = FailureLog(events)
    observability = ObservabilityManager(memory, storage, events)
    downloads = DownloadManager(storage)
    # Every download (files, models, videos, web requests) in one log; the header's download icon opens it.
    from app.transfers import use_file as use_transfer_file
    use_transfer_file(memory.path.parent.parent / 'logs' / 'transfers.jsonl')
    dependencies = DependencyManager()
    backups = BackupManager(storage)
    modes = ModeManager()
    projects = ProjectManager()
    learning = LearningManager()
    # Built-in language courses (Arabic): the voice speaks Arabic, the listener
    # records one answer when asked and never while privacy mode is on.
    from app.learning.language import ArabicListener, ArabicVoice, LanguageTutor

    voice_settings = settings.get("voice", {})
    learning.language = LanguageTutor(
        learning.path,
        voice=ArabicVoice({"output_device": voice_settings.get("tts", {}).get("output_device", "auto")}),
        listener=ArabicListener(voice_settings.get("microphone", {}), privacy=lambda: bool(authority.privacy_mode),
                                model=str(settings.get("learning", {}).get("language_speech_model", "small"))),
    )
    labs = LabManager(learning)
    training = TrainingDataPipeline(config=settings.get("training", {}))
    approval: ApprovalCallback = console_approval if interactive_approvals else deny_unattended
    registry = ToolRegistry(permissions, approval)
    # Authority decides every ASK-category dispatch from here on; the
    # approval callback is only reached for genuinely protected actions.
    registry.authority = authority
    registry.failures = failures
    registry.events = events
    tool_understanding = ToolUnderstanding()
    registry.understanding = tool_understanding
    secret_broker = connectors = connector_gateway = mcp_gateway = browser_extension = None
    if not safe_mode:
        secret_broker = SecretBroker(event_bus=events)
        register_secret_tools(registry, secret_broker)
        connectors = ConnectorRegistry(event_bus=events)
        connector_gateway = ConnectorGateway(connectors, secret_broker, event_bus=events, zero_cost_only=True)
        register_connector_tools(registry, connectors, connector_gateway)
        mcp_gateway = MCPGateway(secrets=secret_broker, event_bus=events, zero_cost_only=True)
        mcp_settings = settings.get("mcp_gateway", {})
        built_in_web = mcp_settings.get("builtin_web_research", {})
        if mcp_settings.get("enabled", True) and built_in_web.get("enabled", True):
            register_builtin_web_research(mcp_gateway, enabled=True)
        register_mcp_tools(registry, mcp_gateway)
        bridge_settings = settings.get("browser_extension_bridge", {})
        browser_extension = BrowserExtensionBridge(
            secret_broker, event_bus=events,
            host=str(bridge_settings.get("host", "127.0.0.1")),
            port=int(bridge_settings.get("port", 8765)),
        )
        register_browser_bridge_tools(registry, browser_extension)
        if bridge_settings.get("enabled", True) and not background_disabled:
            try:
                browser_extension.start()
            except Exception as exc:
                events.publish("SERVICE_DEGRADED", "browser_extension_bridge",
                               {"error": f"{type(exc).__name__}: {exc}"})
    approvals_lifecycle = ApprovalLifecycle(bus=events, store=authority_store, turns=turns)
    authority.approvals_lifecycle = approvals_lifecycle
    filesystem = register_filesystem_tools(registry, storage)
    from app.tools.business import BusinessLedger, register_business_tools
    from app.tools.products import ProductStore, register_product_tools
    products = ProductStore(
        memory.path.parent / "products.db",
        storage.root / "data/artifacts/products", permissions,
    )
    register_product_tools(registry, products)
    # Clients, time, quotes, invoices, expenses, leads and the profit report.
    # Local bookkeeping only: nothing here sends, charges or logs in.
    business = BusinessLedger(memory.path.parent / "business.db",
                              storage.root / "data/artifacts/business", permissions, products=products)
    register_business_tools(registry, business)
    from app.tools.product_workshop import register_product_workspace_tools
    register_product_workspace_tools(registry, products, filesystem, business)
    # Starting a business or service: ideas, launch plan, services, pricing, website, marketing kit.
    from app.tools.launch import Launchpad, register_launch_tools
    launchpad = Launchpad(business, products)
    register_launch_tools(registry, launchpad)
    from app.revenue import RevenueWorkspace, register_revenue_tools
    revenue = RevenueWorkspace(business, registry, output=storage.root / 'data/artifacts/revenue')
    register_revenue_tools(registry, revenue)
    from app.customer_work import CustomerWorkspace, register_customer_tools
    customer_workspace = CustomerWorkspace(revenue)
    register_customer_tools(registry, customer_workspace)
    applications = register_application_tools(registry)
    from app.contacts import ContactBook, register_contact_tools
    from app.messaging import EmailSender, register_messaging_tools
    contacts = ContactBook()
    register_contact_tools(registry, contacts)
    # settings.yaml, or else the account set up from the Business window.
    email = EmailSender(contacts, secret_broker, EmailSender.merged_config(settings.get("email", {})),
                        registry.permissions)
    register_messaging_tools(registry, contacts, applications.windows, secrets=secret_broker,
                             permissions=registry.permissions, mailer=email)
    register_terminal_tools(registry)
    register_powershell_tools(registry)
    register_python_tools(registry)
    from app.safety.container_sandbox import register_container_sandbox_tools
    register_container_sandbox_tools(registry)
    register_lab_tools(registry, labs)
    register_download_tools(registry, downloads)
    register_backup_tools(registry, backups)
    register_introspection_tools(registry, tool_understanding)
    # The plain-language catalog of every area, so HAYAT can read its own powers while planning.
    from app.capabilities import register_capability_tools
    register_capability_tools(registry, lambda: runtime)
    from app.self_knowledge import register_self_knowledge_tools
    register_self_knowledge_tools(registry, lambda: runtime)
    register_memory_tools(registry, memory)
    router = ModelRouter(cost_policy=cost_policy)
    intelligent_router = IntelligentModelRouter(storage, router)
    embedding_config = router.config.get("embedding_model", {})
    embedding_config = {**embedding_config, "allow_cloud": bool(embedding_config.get("allow_cloud", False) and cost_policy.cloud_embeddings_allowed)}
    embeddings = EmbeddingRegistry.from_config(embedding_config, router.config.get("providers", {})) if use_model else EmbeddingRegistry([])
    memory.embedding_provider = embeddings.active
    model = router.get("conversation_model") if use_model else None
    revenue.model = model
    reasoning_model = router.get("reasoning_model") if use_model else None
    classification_model = router.get("classification_model") if use_model else None
    # A larger model used after HAYAT -- on a declined subject and in DEEP mode.
    escalation = load_escalation(router) if use_model else Escalation()
    research_settings = {**settings.get("research", {}), "paid_search_allowed": cost_policy.paid_search_allowed}
    research = ResearchManager(research_settings, model)
    public_documents = PublicDocumentLearner(research, memory, storage, scanner=downloads.scanner)
    retrieval = RetrievalIndex(memory, embeddings.active)
    register_research_tools(registry, research, public_documents)
    from app.intelligence.manager import IntelligenceManager
    from app.intelligence.tools import register_intelligence_tools
    from app.research.fetcher import PublicFetcher
    intelligence_settings = settings.get('intelligence', {})
    intelligence = IntelligenceManager(memory.path.parent / 'intelligence.db',
        PublicFetcher('HAYAT-PublicIntelligence/1.0', timeout=10, delay=.35), registry, events,
        enabled=intelligence_settings.get('enabled', True),
        interval=intelligence_settings.get('tick_seconds', 60),
        extended=intelligence_settings.get('extended_sources', True), knowledge=research.knowledge)
    intelligence.model = model
    # Camera catalogs HAYAT discovered and activated earlier become networks again.
    from app.intelligence.discovery import restore_discovered
    restore_discovered(memory.path.parent / "discovery.db", intelligence)
    register_intelligence_tools(registry, intelligence)
    # Read-only crypto markets and blockchain state (app/markets.py): no trading, wallet or keys.
    from app.markets import MarketData, register_market_tools
    markets = MarketData(PublicFetcher('HAYAT-Markets/1.0', timeout=12, delay=.35), memory.path.parent / 'markets.db')
    from app.markets_live import LiveStream
    markets.live = LiveStream()  # starts only when a price is first asked for, stops when idle
    register_market_tools(registry, markets)
    # Web3 (read-only chain data and an explorer) and Web 4.0 (how ready a site is for AI agents).
    from app.web3 import Web3Data
    from app.web3_chat import register_web3_tools
    from app.web4 import AgentWebInspector
    web3 = Web3Data(markets.fetcher, memory.path.parent / 'web3.json')
    agent_web = AgentWebInspector(PublicFetcher('HAYAT-AgentWeb/1.0', timeout=15, delay=.35))
    register_web3_tools(registry, web3, agent_web)
    browser = register_browser_tools(registry, scanner=downloads.scanner)
    # Publishing, registering and submitting forms: HAYAT prepares, the owner
    # checks and confirms, HAYAT acts once and shows evidence, the owner verifies.
    from app.safety.confirmations import ConfirmationGate
    from app.tools.actions import ActionPreparer, register_action_tools
    from app.tools.publish import NetlifyPublisher

    confirmations = ConfirmationGate(log_path=memory.path.parent / "confirmed-actions.jsonl")
    publisher = NetlifyPublisher(secret_broker)
    register_action_tools(registry, ActionPreparer(confirmations, browser, launchpad, publisher))
    # The owner's own accounts (Google, Microsoft, GitHub, Notion, Slack): signed in on each provider's page,
    # tokens in the secret broker, reads direct, anything sent or changed only after the owner confirms.
    from app.accounts import AccountManager, register_account_tools
    accounts = AccountManager(secret_broker, memory.path.parent.parent / 'integrations' / 'accounts.json')
    register_account_tools(registry, accounts, confirmations)
    # Jobs: public postings read and matched to the owner's CV; applications prepared automatically and
    # sent only after the owner confirms each one in the confirmation window (app/jobs/).
    from app.jobs.center import JobCenter
    from app.jobs.tools import register_job_tools
    from app.jobs.watcher import JobWatcher
    jobs = JobCenter(memory.path.parent.parent / 'jobs', PublicFetcher('HAYAT-Jobs/1.0', timeout=20, delay=1.0),
                     mailer=email, gate=confirmations, model=model, browser=browser)
    job_watcher = JobWatcher(jobs, lambda text: None)  # its notice is connected once the assistant exists
    register_job_tools(registry, jobs, job_watcher)
    from app.networking import NetworkingBook
    from app.tools.networking import register_networking_tools
    networking = NetworkingBook(memory.path.parent.parent / 'networking')
    register_networking_tools(registry, networking)
    # The deep review of the owner's books, with the market from the jobs HAYAT collected (app/business_deep.py).
    from app.business_deep import register_business_deep_tools
    register_business_deep_tools(registry, business, jobs)
    # Money methods by nickname, money the owner added, and pay-now QR codes and links; nothing ever moves (app/wallet_money.py).
    from app.wallet_money import register_wallet_money_tools
    register_wallet_money_tools(registry, business)
    # Portfolio and net worth in USD: what the owner holds at their bank, broker and exchange, valued live (app/portfolio.py).
    from app.portfolio import Portfolio, register_portfolio_tools
    portfolio = Portfolio(memory.path.parent / "portfolio.db", markets, web3, markets.fetcher, business)
    register_portfolio_tools(registry, portfolio)
    # The HAYAT Wallet desk: pots, bills, rules and HAYAT's money decisions, approved only by the owner (app/money_desk.py).
    from app.money_desk import MoneyDesk, register_money_desk_tools
    money_desk = MoneyDesk(business, portfolio)
    register_money_desk_tools(registry, money_desk)
    # Today's brief: what needs attention, read from the books. Reminds; never acts.
    from app.tools.brief import BusinessBrief, register_brief_tools
    brief = BusinessBrief(business, launchpad, confirmations, money_desk)
    register_brief_tools(registry, brief)
    research.attach_browser(browser)
    computer = register_computer_tools(registry)
    # Desktop apps by their named controls (Windows UI Automation), not only by screen coordinates.
    from app.tools.ui_automation import register_ui_automation_tools
    register_ui_automation_tools(registry)
    displays = DisplayManager(); register_display_tools(registry, displays)
    register_system_tools(registry)
    # What is plugged in, and where this computer is: read from Windows, never guessed.
    from app.tools.devices import register_device_tools
    register_device_tools(registry, get_json=research.fetcher.get_json)
    # An email address checked honestly: its domain's mail setup and where it is published; never "located"
    # (app/email_check.py). The owner's own address also reads where this computer is, through device_location.
    from app.email_check import register_email_check_tools
    mail_pages = PublicFetcher('HAYAT-EmailCheck/1.0', timeout=12, delay=.35)
    register_email_check_tools(
        registry, fetch=lambda url: mail_pages.fetch(url, 512 * 1024).content,
        own_addresses=lambda: {str(email.config.get(key) or '').lower() for key in ('from_address', 'username')} - {''})
    # Wi-Fi channel state information: CSI devices connected, checked, recorded and read for movement (app/csi.py).
    from app.csi import register_csi_tools
    from app.sensing import SensingMonitor, register_sensing_tools
    csi_hub = register_csi_tools(registry)
    # The live movement timeline, camera cross-checks and SENSING_MOTION events (app/sensing.py).
    register_sensing_tools(registry, SensingMonitor(csi_hub, registry=registry, events=events,
                                                    privacy=lambda: bool(authority.privacy_mode)))
    # The Wi-Fi router and home network: connection, router status page, nearby Wi-Fi, recent devices (app/network.py).
    # Text analysis with NLTK: keywords, readability, sentiment, parts of speech (app/text_analysis.py).
    from app.text_analysis import register_text_tools
    register_text_tools(registry)
    from app.network import register_network_tools
    register_network_tools(registry, fetch=lambda url: research.fetcher.fetch(url, 8 * 1024 * 1024).content)
    # HAYAT may read the owner's location; nobody else learns it through HAYAT (app/location_privacy.py): place
    # names on this computer, the outgoing-location guard in the tool registry, and the exposure check.
    from app.location_privacy import LocationPrivacy, register_location_privacy_tools
    register_location_privacy_tools(registry, LocationPrivacy(
        get_json=research.fetcher.get_json, privacy=lambda: bool(authority.privacy_mode)))
    # Masks: a VPN for the whole computer (detected and leak-tested), Tor for HAYAT's own web reading, and the
    # location guard (app/privacy_masks.py). Tor starts again at launch when the owner left it on.
    from app.location_privacy import SHIELD
    from app.privacy_masks import PrivacyMasks, TorMask, register_privacy_mask_tools
    masks = register_privacy_mask_tools(registry, PrivacyMasks(
        tor=TorMask(scanner=downloads.scanner),
        masked_json=research.fetcher.get_json, privacy=lambda: bool(authority.privacy_mode),
        guard_on=lambda: SHIELD.enabled))
    # Explicit onion research can start installed Tor on its background worker.
    research.ensure_tor = masks.tor.start
    research.interlock = registry.interlock
    research.authority = authority
    def search_browser(url, tor_required=False):
        from app.privacy_masks import ROUTE
        if tor_required or ROUTE.proxy is not None:
            status = masks.tor.start()
            if not status.get('masked'):
                raise PermissionError('Tor browser handoff is blocked: ' + str(status.get('reason') or 'Tor not ready'))
            return masks.open_masked_browser(url=url)
        import webbrowser
        if not webbrowser.open(url):
            raise RuntimeError('The default browser could not be opened.')
        return {'browser': 'default', 'opened': True, 'routing': 'ordinary browser; network routing is not verified'}
    research.search_browser = search_browser
    if masks.tor.wanted():
        masks.tor.route.set(f"socks5://127.0.0.1:{masks.tor.port}")
        if not background_disabled:
            threading.Thread(target=masks.tor.start, name="hayat-tor-start", daemon=True).start()
    # Background autostart is off by default, which left the remembered Tor
    # route pointing at a port nothing listened on: every search was refused.
    # The first masked read now starts Tor itself (app/privacy_masks.py).
    if not safe_mode and "PYTEST_CURRENT_TEST" not in os.environ:
        from app.privacy_masks import ROUTE
        ROUTE.set_starter(masks.tor.ensure_started)
    # Reverse engineering: REA (morluto/rea, MIT) run locally from runtime/rea (app/reverse_engineer.py). Reads
    # are passive; opening a target, extracting, capturing or editing notes go through the confirmation gate.
    from app.reverse_engineer import ReaSession, ReverseEngineer, register_reverse_engineer_tools
    from app.safety.sandbox import ProcessSandbox as _ReaSandbox
    reverse_engineer = register_reverse_engineer_tools(
        registry, ReverseEngineer(lambda: ReaSession(env=_ReaSandbox().environment())), gate=confirmations, fetcher=research.fetcher)
    # Everything around a place, fused from every public source, and how it changed since the last look.
    from app.intelligence.catalog import source_catalog
    from app.intelligence.fusion import Fusion, register_fusion_tools, reverse_place
    from app.intelligence.live import LiveData
    from app.intelligence.history import PositionHistory
    from app.intelligence.aviation import Aviation
    from app.intelligence.objects import Objects
    from app.intelligence.osint import register_osint_tools
    intelligence.history = PositionHistory(intelligence.store.path.parent / "positions.db")
    intelligence.live = LiveData(research.fetcher.get_json, recorder=intelligence.history)
    intelligence.aviation = Aviation(intelligence.live)
    intelligence.objects = Objects(intelligence)
    def fusion_location():
        result = registry.execute("device_location", {"wait_seconds": 5})
        if not result.success:
            raise ValueError(result.error or "Device location is unavailable.")
        return result.output
    intelligence.fusion = Fusion(intelligence, intelligence.live,
                                 my_location=fusion_location,
                                 reverse=lambda lat, lon: reverse_place(research.fetcher.get_json, lat, lon))
    register_fusion_tools(registry, intelligence.fusion,
                          lambda type="": source_catalog(intelligence, intelligence.live, type))
    register_osint_tools(registry, intelligence)
    # Around me: every hazard near a place by distance and bearing (app/intelligence/radar.py).
    from app.intelligence.radar import register_radar_tools
    register_radar_tools(registry, intelligence)
    # Launches, satellites, aircraft details, weather maps, imagery, fires, routes, bike share, vehicle counts.
    from app.intelligence.explore import Explore, register_explore_tools
    def camera_still(event_id):
        frame = intelligence.camera_frame(event_id)
        row = intelligence.event_record(event_id)["event"]
        return {**frame, "title": row.get("title") or event_id, "latitude": row.get("latitude"), "longitude": row.get("longitude")}
    intelligence.explore = Explore(PublicFetcher('HAYAT-Explore/1.0', timeout=40, delay=.35),
                                   intelligence.store.path.parent / 'explore', intelligence.fusion.resolve,
                                   frame=camera_still, land=PROJECT_ROOT / 'app/assets/world-land.json')
    register_explore_tools(registry, intelligence.explore)
    # Everything about one place at once, reasoned into findings with evidence and blind spots.
    from app.intelligence.deep import DeepLook, register_deep_tools
    intelligence.deep = DeepLook(intelligence, intelligence.explore)  # the atlas asks it too
    register_deep_tools(registry, intelligence.deep)
    # World events lined up with the industries and markets they usually touch.
    from app.markets import register_market_event_tools
    register_market_event_tools(registry, intelligence, markets)
    # The owner's own cameras, by exact address; logins live in the credential store (my_cameras.py).
    from app.intelligence.my_cameras import MyCameras, register_my_camera_tools
    intelligence.my_cameras = MyCameras(memory.path.parent / 'my-cameras.json', secret_broker)
    register_my_camera_tools(registry, intelligence.my_cameras)
    # Real companies to approach, from a web search; nothing invented, no one contacted.
    from app.tools.prospects import register_prospect_tools
    register_prospect_tools(registry, research, business, storage.root / "data/artifacts/business")
    # The Quran from the published Tanzil text with transliteration; cached after the first read.
    from app.tools.quran import register_quran_tools
    register_quran_tools(registry, research.fetcher.get_json, storage.root / "data/knowledge/quran")
    images = register_image_tools(registry)
    from app.gallery import register_gallery_tools
    register_gallery_tools(registry, permissions, downloads.scanner, images, memory)
    # install_package_project has been a permission category since the
    # authority kernel was written with nothing using it. This is the
    # capability that category was describing.
    packages = register_package_tools(registry)
    from app.software import register_software_tools

    software = register_software_tools(registry)
    # Model downloads are real now: planned with the registry's size, started
    # only after Mohsin's yes, run in the background into HAYAT's Ollama.
    from app.models.pull import ModelPulls, register_model_pull_tools

    model_pulls = register_model_pull_tools(registry, ModelPulls(
        str(getattr(router.providers.get("ollama"), "base_url", "") or "http://localhost:11434"),
        store=Path(os.environ["OLLAMA_MODELS"]) if os.getenv("OLLAMA_MODELS") else None))
    vision_model = router.get("vision_model") if use_model else None
    vision = VisionAnalyzer(vision_model, permissions) if vision_model else None
    clipboard = ClipboardManager(vision); register_clipboard_tools(registry, clipboard)
    video = VideoAnalyzer(permissions, downloads.scanner, PROJECT_ROOT / "data/cache/video",
                          vision=vision, storage=storage, stt_config=settings.get("voice", {}).get("stt", {}))
    register_video_tools(registry, video)
    from app.video_workflow import VideoWorkflow, register_video_workflow_tools
    video_workflow = VideoWorkflow(permissions, downloads.scanner, storage=storage, model=model, vision=vision,
                                   stt_config=settings.get("voice", {}).get("stt", {}))
    register_video_workflow_tools(registry, video_workflow)
    camera = CameraManager(settings.get("camera", {}), vision); camera.event_bus = events; register_camera_tools(registry, camera)
    from app.perception.live_screen import register_screen_reader
    from app.perception.people import register_person_tools
    from app.replica import register_replica_tools
    screen_reader = register_screen_reader(registry)
    register_person_tools(registry, camera)
    register_replica_tools(registry, model, research.fetcher)
    incidents = IncidentManager(observability); register_incident_tools(registry, incidents)
    model_resources = ModelResourceManager(storage, observability, settings.get("resources", {}))
    intelligent_router.resource_manager = model_resources
    security = DefensiveSecurityAuditor(permissions, dependencies, downloads.scanner)
    if vision:
        register_vision_tools(registry, computer, vision)
    # Read the words in a picture the owner points at (on-device OCR), and
    # confirm the owner is present with Windows Hello. Neither looks anyone up.
    from app.tools.text_recognition import register_text_recognition_tools
    from app.tools.owner_verification import register_owner_verification_tools
    register_text_recognition_tools(registry)
    register_owner_verification_tools(registry)
    # Recognise only people the owner has enrolled, on the owner's own images;
    # the enrolled set persists until the owner clears it. Never a people-search.
    from app.tools.faces import register_face_tools
    register_face_tools(registry)
    from app.tools.image_tools import register_local_image_tools
    register_local_image_tools(registry)
    from app.tools.image_sources import register_image_source_tools
    register_image_source_tools(registry, research)
    # Brighten dark images/frames (low-light / night vision), on-device.
    from app.tools.image_enhance import register_image_enhance_tools
    register_image_enhance_tools(registry)
    # HAYAT looks through public cameras: one current picture, described by the vision model.
    from app.intelligence.camera_eyes import CameraEyes, register_camera_eyes
    intelligence.eyes = CameraEyes(intelligence, vision)
    register_camera_eyes(registry, intelligence.eyes)
    planner = Planner(registry, reasoning_model or model, int(settings.get("max_steps", 20)), research.knowledge)
    agent = Agent(planner, Executor(registry), Verifier(), memory, int(settings.get("max_steps", 20)))
    skills = SkillRegistry(registry)
    if not safe_mode:
        skills.reload()
    promoter = SkillPromoter(memory, skills.loader)
    scheduler = AgentScheduler(agent, JobRepository(memory))
    if settings.get("scheduler", {}).get("enabled", False) and not background_disabled:
        scheduler.start()
    manager = ManagerAgent(agent, {
        "researcher": ResearchAgent(agent), "coder": CodingAgent(agent), "developer": DeveloperAgent(agent),
        "computer": ComputerAgent(agent), "reviewer": ReviewerAgent(agent),
        # Works on this system itself. Same core, same registry, same authority
        # -- it is a specialist, not a second runtime with its own rules.
        "repair": RepairAgent(agent, project_root=PROJECT_ROOT),
    })
    # Resume the last conversation across a restart -- clean or crashed -- so
    # "what were we talking about?" survives a shutdown instead of starting
    # blank. The thread's messages are already on disk keyed by session id;
    # adopting the most recent id is all it takes for conversation_history to
    # load it back. Gated by a recency window (default 3 days) so a thread from
    # weeks ago does not silently reopen as if no time had passed; set the
    # window to 0 to always start fresh.
    resume_window = float(settings.get("conversation", {}).get("resume_within_seconds", 259200))
    resumed = memory.latest_session(resume_window) if resume_window > 0 else None
    session = SessionState(session_id=resumed["session_id"]) if resumed else SessionState()
    # Correlation fallback for callers that predate ExecutionContext. It is
    # deliberately limited to session and turn: both are single-valued for the
    # runtime and are set before any tool can run. It supplies no task id --
    # `session.last_task` is assigned only after an outcome finishes, so while
    # Task B is executing it still names Task A, and an approval bound to the
    # wrong task would silently break the lifecycle's wrong-task guarantee.
    # The task id comes from the scope Agent._run binds, or it stays unknown.
    registry.context_supplier = lambda: {
        "session_id": session.session_id, "turn_id": session.active_turn_id,
    }
    state = AgentStateService(model=model, reasoning_model=reasoning_model or model, agent=agent, registry=registry, memory=memory, session=session, storage=storage, research=research, vision=vision, modes=modes, projects=projects)
    state.camera = camera; state.displays = displays; state.clipboard = clipboard; state.browser = browser; state.observability = observability; state.cost_policy = cost_policy; state.model_resources = model_resources
    state.self_change = SelfChangeTracker(PROJECT_ROOT)
    register_self_state_tool(registry, state)
    def route_conversation(capability: str, active_modes: list[str]):
        from app.models.intelligent_router import RouteRequest
        simple = capability in {"conversation", "classification", "summarization"}
        complexity = "simple" if simple or "FAST" in active_modes else "complex"
        latency_budget = 1500 if simple else 1200 if "FAST" in active_modes else 8000
        return intelligent_router.route(RouteRequest(capability, complexity, "text", tuple(active_modes), "private" if "PRIVATE" in active_modes else "local", latency_budget, "text"))

    def select_conversation_model(capability: str, active_modes: list[str]):
        if not use_model:
            return None
        # HAYAT writes every reply; routing evidence decides whom it consults.
        return intelligent_router.provider(intelligent_router.hayat_first(route_conversation(capability, active_modes)))

    def specialist_advisor(capability: str, active_modes: list[str]):
        if not use_model:
            return None
        name = intelligent_router.specialist(route_conversation(capability, active_modes))
        base = router.providers.get("ollama")
        if not name or base is None or not intelligent_router._admit(name):
            return None
        from copy import copy as clone

        advisor = clone(base)
        advisor.model = name
        return advisor

    def retrieve_context(query: str, limit: int = 8, session_id: str | None = None):
        from app.memory.state_policy import eligible_conversation_context

        candidates = [*memory.retrieve(query, limit=limit, session_id=session_id),
                      *retrieval.search(query, limit=max(limit, min(64, limit * 4)))]
        unique = {}
        for item in candidates:
            if not eligible_conversation_context(item, session_id=session_id):
                continue
            key = str(item.get("content", "")).strip().lower()
            if key and (key not in unique or float(item.get("relevance", 0)) > float(unique[key].get("relevance", 0))): unique[key] = item
        return sorted(unique.values(), key=lambda item: float(item.get("relevance", 0)), reverse=True)[:limit]

    conversation = ConversationService(model, memory, session.session_id,
                                       lambda: {**state.snapshot(), "helper_roles": manager.roles()},
                                       classification_model or model, reasoning_model or model,
                                       retrieve_context, select_conversation_model)
    conversation.answer_model_name = intelligent_router.identity_model
    conversation.tool_reference = lambda text: tool_reference(registry, tool_understanding, text)
    from app.capabilities import abilities_note
    conversation.abilities_supplier = lambda: abilities_note(registry.names())
    conversation.known_contacts = lambda: known_contacts(business, memory.path)
    # HAYAT is the brain: it consults the DEEP model or a measured specialist
    # when a turn is worth it, then writes the reply itself. Choosing DEEP
    # preloads the DEEP advisor; the router's health breaker covers it.
    escalation.bind(intelligent_router)
    conversation.council = Council(escalation, specialist_advisor,
                                   intelligent_router._record_failure, intelligent_router._record_success)
    modes.listeners.append(lambda chosen: escalation.warm(mode.value for mode in chosen))
    if not background_disabled:
        # DEEP saved from the last session: load it now rather than on the first question.
        escalation.warm(mode.value for mode in modes.effective)
    # Decides what each turn is worth instead of every turn getting the
    # same fixed window, history depth and recall count.
    context_budget = ContextBudget()
    conversation.budget = context_budget
    # Only a configured classification model, never the brain by default: the
    # rules route almost everything, and a 35B call on each turn cost 5-9 s.
    intent_router = IntentRouter(classification_model)
    model_capabilities = ModelCapabilityRegistry(storage, router)
    evaluation = EvaluationManager()
    runtime = Runtime(agent, registry, memory, router, model, browser, computer, skills, scheduler, manager, conversation, vision, promoter, research, storage, session, intent_router, modes, projects, state, learning, training, model_capabilities, intelligent_router, embeddings, retrieval, evaluation, cost_policy, downloads, dependencies, camera, displays, clipboard, observability, model_resources, incidents, events, security, labs, backups, str(settings.get("proactive_assistance", "SUGGEST")).upper(), turns, authority, failures)
    runtime.screen_reader = screen_reader
    runtime.networking = networking
    from app.executive import ExecutiveCortex
    from app.dual_brain import DualBrain
    from app.inquiry import SelfInquiry
    from app.invention import InventionWorkbench
    from app.tools.invention import register_invention_tools

    runtime.executive = ExecutiveCortex()
    brain_settings = settings.get('executive_brain', {})
    runtime.dual_brain = DualBrain(runtime.executive,
                                   lambda: specialist_advisor('reasoning', ['ADVERSARIAL']),
                                   enabled=bool(brain_settings.get('dual_review_enabled', True)),
                                   deep_turns=bool(brain_settings.get('review_deep_turns', True)))
    runtime.inquiry = SelfInquiry(runtime.executive, scope=str(brain_settings.get('self_inquiry', 'deep_turns')))
    conversation.executive, conversation.dual_brain = runtime.executive, runtime.dual_brain
    conversation.inquiry = runtime.inquiry
    agent.executive = runtime.executive

    def invention_research(goal):
        result = registry.execute('research_topic', {'query': goal, 'level': 'primary_only',
                                                   'official_only': True, 'remember': False})
        if not result.success:
            raise RuntimeError(result.error)
        return result.output

    runtime.invention = InventionWorkbench(PROJECT_ROOT / 'data/invention', runtime.executive,
                                           runtime.dual_brain,
                                           lambda: select_conversation_model('reasoning', ['DEEP']),
                                           invention_research)
    register_invention_tools(registry, runtime.invention, runtime.executive, runtime.dual_brain)
    monitor_settings = settings.get("development", {}).get("code_monitor", {})
    from app.output import get_output

    runtime.code_monitor = CodeMonitor(
        projects,
        interval_seconds=float(monitor_settings.get("interval_seconds", 180)),
        notify=lambda message: get_output().emit(message, source="code_monitor"),
    )
    register_code_monitor_tools(registry, runtime.code_monitor)
    manager.specialists["developer"].monitor = runtime.code_monitor
    runtime.secret_broker = secret_broker
    runtime.escalation = escalation
    runtime.contacts = contacts
    runtime.safe_mode = bool(safe_mode)
    runtime.connectors = connectors
    runtime.connector_gateway = connector_gateway
    runtime.mcp_gateway = mcp_gateway
    runtime.browser_extension = browser_extension
    ollama_settings = router.config.get("providers", {}).get("ollama", {})
    runtime.hayat_model = HayatModelManager(
        name=str((router.config.get("identity_model") or {}).get("model") or "hayat:latest"),
        base_model=str((router.config.get("identity_model") or {}).get("base_model") or "qwen3.6-35b-a3b-uncensored:q4_k_m"),
        base_url=str(ollama_settings.get("base_url", "http://localhost:11434")),
    )
    runtime.training_lifecycle = PrivateFineTuneLifecycle(training)
    from app.learning.architecture import LearningArchitecture
    runtime.learning_architecture = LearningArchitecture(runtime)
    runtime.public_documents = public_documents
    runtime.tool_benchmarks = ToolBenchmark(registry, tool_understanding)
    runtime.images = images
    # Supervises the one dependency whose absence stops every reasoning path.
    # Constructed unconditionally so /provider status answers even when the
    # provider is down -- that is exactly when it is worth asking.
    runtime.supervisor = RuntimeSupervisor(
        OllamaService(
            str(ollama_settings.get("base_url", "") or ""),
            str(ollama_settings.get("model", "") or ""),
        ),
        events=events, failures=failures,
    )
    # The repair specialist diagnoses from the runtime's own evidence -- the
    # failure log, the supervisor, doctor -- so it needs the assembled runtime,
    # which does not exist when the specialists are constructed.
    # Camera activation that cannot outlive its reason: a stated purpose, a
    # watchdog that closes the device whatever the caller is doing, and a
    # journal answering "was it on, when, and why".
    runtime.camera_sessions = CameraSessions(camera, authority=authority, events=events)
    # Narrates work from the events it already publishes, so Mohsin can
    # watch HAYAT plan and act instead of waiting through a silent pause.
    # Watches finished work for repetition worth writing down, and reads
    # HAYAT's own record for things worth improving.
    runtime.apprenticeship = Apprenticeship(memory, promoter, skills, events)
    # What worked, what failed and what fixed it, consulted before planning and
    # when a step fails; and the running understanding of the owner's
    # situation (focus, habits, gaps, checked predictions), kept across restarts.
    from app.experience import ExperienceMemory
    from app.situation import SituationModel, register_situation_tools
    runtime.experience = ExperienceMemory(memory.path.parent / "experience.db")
    runtime.situation = SituationModel(memory.path.parent / "experience.db", runtime=lambda: runtime)
    agent.experience = runtime.experience
    agent.situation_brief = runtime.situation.brief
    conversation.situation_supplier = runtime.situation.brief
    register_situation_tools(registry, runtime.situation)
    if not background_disabled:
        # Once, when these are new: learn from the history and task logs already on disk.
        def _learn_from_history() -> None:
            try:
                runtime.situation.backfill(memory)
                runtime.experience.backfill_from_logs(storage.root / "data/logs")
            except Exception:
                log_swallowed(_log, "learning from history on first start", level=logging.WARNING)

        threading.Thread(target=_learn_from_history, name="hayat-history-learning", daemon=True).start()
    runtime.packages = packages
    # What HAYAT knows about its own tools from having used them, which is
    # what lets a failure *rate* exist at all -- and so what makes the
    # reliability threshold in SelfImprovement mean something.
    runtime.tool_understanding = tool_understanding
    runtime.context_budget = context_budget
    # Reads files Mohsin hands over and keeps the understanding, so a
    # later mention of one finds it without handing it over again.
    runtime.attachments = AttachmentReader(runtime, scanner=downloads.scanner)
    # Old task memories average 25KB each and are 96% of everything
    # remembered; digested they keep the goal and lose the transcript.
    runtime.compaction = MemoryCompactor(memory)
    conversation.attachments = runtime.attachments
    conversation.language_supplier = lambda: getattr(runtime.session, "reply_language", "en")
    context_budget.runtime = runtime
    runtime.improvement = SelfImprovement(runtime)
    # Languages HAYAT can detect, speak and study, and the persona switch.
    from app.languages import LanguageSkills
    from app.persona import Persona

    runtime.software = software
    runtime.model_pulls = model_pulls
    model_pulls.notify = lambda job, text: runtime.assistant.deliver_notice(job, text, kind="model_pull")
    runtime.languages = LanguageSkills(learner=lambda: getattr(runtime, "learner", None),
                                       output_device=voice_settings.get("tts", {}).get("output_device", "auto"))
    runtime.persona = Persona()
    runtime.business = business
    runtime.revenue = revenue
    runtime.customers = customer_workspace
    runtime.launch = launchpad
    runtime.confirmations = confirmations
    runtime.accounts = accounts
    runtime.jobs, runtime.job_watcher = jobs, job_watcher
    runtime.brief = brief
    runtime.publisher = publisher
    runtime.email = email
    runtime.products = products
    # Searches run as background jobs that report back when done.
    from app.research.jobs import ResearchJobs

    research_settings = settings.get("research", {})
    runtime.research_jobs = ResearchJobs(
        lambda: runtime.research, deliver=lambda job, text: runtime.assistant.deliver_notice(job, text),
        inline_seconds=float(research_settings.get("inline_wait_seconds", 20)),
        timeout_seconds=float(research_settings.get("job_timeout_seconds", 180)),
    )
    # Temporary worker teams for multi-part goals (app/agents/team.py): designed
    # per goal, read-and-research tools only, reported back like a search job.
    from app.agents.team import WorkerTeam

    runtime.teams = WorkerTeam(model, registry, root=PROJECT_ROOT,
                               deliver=lambda report, text: runtime.assistant.deliver_notice(report, text, kind="team"))
    conversation.persona_supplier = lambda: runtime.persona.rule()
    def proactive_suggestion(goal: str, event: dict[str, Any]) -> None:
        if runtime.proactive_mode == "OFF":
            return
        message = f"HAYAT noticed {event['event_type']}: {goal}"
        memory.store_memory("episodic", message, .7, "proactive_trigger",
                            metadata={"event_type": event["event_type"], "event_id": event["event_id"]},
                            session_id=session.session_id)
        from app.output import get_output
        get_output().emit(message, source="proactive")

    runtime.event_triggers = EventTriggerEngine(
        events, run_goal=lambda goal: manager.route(goal), suggest=proactive_suggestion,
    )
    register_event_trigger_tools(registry, runtime.event_triggers)
    if settings.get("proactive", {}).get("event_triggers_enabled", True) and not background_disabled:
        runtime.event_triggers.start()
    # Claimed before any work starts, so a task begun in this session and cut
    # off is attributable to this session rather than blamed on the last one.
    runtime.recovery = CrashRecovery(memory)
    runtime.recovery.mark_started(session.session_id)
    runtime.thoughts = ThoughtStream(events, enabled=bool(
        settings.get('thinking', {}).get('narrate', False)))
    repair_specialist = manager.specialists.get("repair")
    if repair_specialist is not None:
        repair_specialist.runtime = runtime
    # The always-on learner. Constructed always so /learn can report on it,
    # started only when configured -- an unattended loop should be something
    # Mohsin switched on, not something he discovers already running.
    learning_settings = settings.get("learning", {}).get("continuous", {})
    runtime.learner = ContinuousLearner(
        runtime=runtime,
        budget=LearningBudget(**{
            key: value for key, value in learning_settings.get("budget", {}).items()
            if key in LearningBudget.__dataclass_fields__
        }),
        research_level=str(learning_settings.get("research_level", "normal")),
        anticipate_followups=bool(learning_settings.get("anticipate_followups", True)),
        anticipated_per_request=int(learning_settings.get("anticipated_per_request", 2)),
        idle_reflection=bool(learning_settings.get("idle_reflection", True)),
        reflection_candidates=int(learning_settings.get("reflection_candidates", 2)),
        standby_after_budget=bool(learning_settings.get("standby_after_budget", False)),
        retry_delay_seconds=float(learning_settings.get("retry_delay_seconds", 60)),
        maintenance_interval_seconds=float(learning_settings.get("maintenance_interval_seconds", 900)),
    )
    for topic in learning_settings.get("seed_topics", []) or []:
        runtime.learner.enqueue(str(topic), source="configured_learning_seed", priority=40)
    if learning_settings.get("enabled", False) and start_background_services and not background_disabled:
        runtime.learner.start(
            topics=runtime.learner.next_topic,
            interval=float(learning_settings.get("interval_seconds", 900)),
        )
    runtime.assistant = AssistantService(runtime)
    runtime.intelligence = intelligence
    runtime.markets = markets
    intelligence.markets = markets  # the browser atlas's world-trade layer shows linked funds' moves
    runtime.web3, runtime.agent_web = web3, agent_web
    runtime.reverse_engineer = reverse_engineer
    runtime.portfolio, runtime.money_desk = portfolio, money_desk
    runtime.voice = VoiceManager(runtime, settings.get("voice", {}))
    runtime.state.voice = runtime.voice
    runtime.brain_mesh = BrainMesh(runtime)
    register_brain_mesh_tools(registry, runtime.brain_mesh)
    runtime.metacognition = AdaptiveControlPlane(runtime)
    register_metacognition_tools(registry, runtime.metacognition)
    if monitor_settings.get("enabled", True) and not background_disabled:
        runtime.code_monitor.start()
    if intelligence.enabled and not background_disabled:
        intelligence.start()
    # Price alerts reported on their own while HAYAT runs (reads nothing while none is waiting).
    from app.market_alerts import AlertWatcher
    runtime.price_alerts = AlertWatcher(markets, lambda text: runtime.assistant.deliver_notice(None, text, kind="price_alert"))
    if not background_disabled:
        runtime.price_alerts.start()
    # The automatic job round: search, match, prepare strong matches if allowed, draft follow-ups; never sends.
    job_watcher.notify = lambda text: runtime.assistant.deliver_notice(None, text, kind="job_matches")
    if not background_disabled and settings.get("jobs", {}).get("background", True):
        job_watcher.start()
    # Public camera and data source discovery while idle, under the learner's gates.
    from app.intelligence.discovery import DiscoveryStore, SourceScout, register_discovery_tools
    discovery_settings = intelligence_settings.get("discovery", {}) or {}
    runtime.discovery = SourceScout(
        DiscoveryStore(memory.path.parent / "discovery.db"),
        search=lambda query, limit, domains=None: research.search.search(query, limit, None, domains),
        fetcher=research.fetcher,
        manager=intelligence, learner=runtime.learner, authority=authority,
        requests_per_hour=int(discovery_settings.get("requests_per_hour", 30)),
        interval_seconds=float(discovery_settings.get("interval_seconds", 600)),
        priority_regions=discovery_settings.get('priority_regions', ()))
    register_discovery_tools(registry, runtime.discovery)
    if discovery_settings.get("enabled", True) and intelligence.enabled and not background_disabled:
        runtime.discovery.start()
    return runtime
