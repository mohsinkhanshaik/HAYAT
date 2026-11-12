"""Component neurons for the Brain 5D map: the real parts inside each subsystem.

The 3D brain's 21 subsystem nodes are ``BrainMesh`` nodes. Each one is made of
smaller real things, and this module lists them -- built only from what the
running HAYAT actually has:

* capability areas: the ``app.capabilities`` catalog, with the registered
  tools behind each area (``classify`` over the live registry);
* intent routes: every ``IntentKind`` the deterministic intent router can
  return;
* model roles: each ``*_model`` slot in the model router's configuration and
  the model configured for it;
* specialists: the manager's registered helper roles;
* risk tiers: the authority kernel's ``RiskTier`` levels, each grouping the
  permission categories it governs;
* services and stores: runtime services that exist in this process (scheduler,
  learner, MCP gateway, event log, backups...), excluding the ones a subsystem
  node already stands for and the ones that back a capability area (those are
  part of that area's neuron).

Nothing is padded: a section whose source is missing contributes nothing, so
the total is whatever this runtime really has. Live numbers come from the event
log (``brain_history.load_usage``) and the model router's verified-outcome
table; links come from real membership, recorded co-use within one turn or
task, the authority tier each area's tools need, and the manager's routing.
"""
from __future__ import annotations

import itertools
import inspect
import logging
import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

#: Kinds in drawing/sorting order, with their plain names.
KINDS = {
    "capability": "capability area",
    "tool": "tool",
    "intent": "intent route",
    "model": "model role",
    "specialist": "specialist",
    "policy": "risk tier",
    "service": "service",
    "store": "store",
}

#: Which subsystem each capability area belongs to.
CAPABILITY_HOME = {
    "networking": "network", "replica": "helpers", "invention": "invention", "learning_atlas": "learning",
    "public_intelligence": "network", "markets": "network", "accounts": "network", "jobs": "network",
    "web3": "network", "chat": "interface", "voice": "interface", "research": "research", "memory": "memory",
    "files": "tools", "text_analysis": "tools", "media": "tools", "senses": "perception",
    "image_files": "perception", "owner_check": "authority", "people_recognition": "perception",
    "computer": "tools", "wifi_sensing": "perception", "home_network": "network", "browser": "network",
    "act_online": "authority", "code": "tools", "messages": "network", "location_privacy": "authority",
    "reverse_engineer": "tools", "privacy_masks": "network", "email_check": "network", "money": "tools",
    "portfolio": "tools", "growth": "tools", "languages": "learning", "software": "tools",
    "connect": "network", "automation": "planner", "health": "observability", "learning": "learning",
    "research_lab": "research",
}

#: Runtime services that back a capability area: they are part of that area's neuron.
CAPABILITY_BACKING = {
    "public_intelligence": "intelligence", "markets": "markets", "accounts": "accounts", "jobs": "jobs",
    "web3": "agent_web", "voice": "voice", "senses": "camera", "computer": "computer", "browser": "browser",
    "messages": "email", "money": "business", "portfolio": "portfolio", "reverse_engineer": "reverse_engineer",
    "software": "packages", "connect": "connectors", "learning": "learner", "act_online": "confirmations",
}

#: Runtime attributes a subsystem node already stands for (no second neuron for them).
SUBSYSTEM_OBJECTS = frozenset({
    "intent_router", "context_budget", "intelligent_router", "manager", "agent", "tool_understanding",
    "authority", "registry", "memory", "research", "learning_architecture", "observability", "executive",
    "dual_brain", "invention", "inquiry", "assistant", "conversation", "session", "state", "gui", "router",
})

#: (runtime attribute, label, subsystem, kind, what it is, event sources it reports under)
SERVICES: tuple[tuple[str, str, str, str, str, tuple[str, ...]], ...] = (
    ("events", "Event log", "observability", "store",
     "Durable record of every turn, tool call, routing and authority decision (events.db).", ()),
    ("turns", "Turn log", "observability", "store", "Each conversation turn with its route and outcome.", ()),
    ("failures", "Failure log", "observability", "store",
     "Recorded failures with their stage, service and cause.", ("tool",)),
    ("retrieval", "Retrieval index", "dna", "store", "Search index used to find related memories and documents.", ()),
    ("embeddings", "Embedding index", "memory", "store", "Vector embeddings for meaning-based recall.", ()),
    ("storage", "Storage manager", "memory", "store", "Disk layout and model inventory under data/.", ()),
    ("backups", "Backups", "memory", "store", "Snapshots of HAYAT's own data for restore.", ()),
    ("compaction", "Memory compaction", "memory", "service", "Folds old history into compact summaries.", ()),
    ("nervous_system", "Nervous system", "cortex", "service",
     "One shared sense of whether the owner is waiting on HAYAT: background parts wait during a reply or task "
     "(never past 90 s) and reflexes never do.", ()),
    ("memory_network", "Memory network", "memory", "service",
     "Links between memories: each task bound to its lesson and to the task before it, tasks with the same "
     "purpose linked, links strengthened by recall that helped and faded when unused.", ()),
    ("public_documents", "Public documents", "research", "store",
     "Published documents learned into the shared memory.", ()),
    ("experience", "Experience lessons", "learning", "store",
     "Lessons from past outcomes, offered to later plans.", ()),
    ("situation", "Situation model", "context", "store",
     "Focus, habits, gaps and checked predictions used in every reply.", ()),
    ("skills", "Skill library", "learning", "store", "Trusted and untrusted skills; only trusted ones run.", ()),
    ("training", "Training data", "learning", "store", "The gated private training-data pipeline.", ()),
    ("projects", "Projects", "planner", "store", "Saved projects that plans and tasks belong to.", ()),
    ("products", "Product store", "tools", "store", "The business product catalog.", ()),
    ("scheduler", "Scheduler", "planner", "service", "Runs scheduled agent jobs on time.", ()),
    ("event_triggers", "Event triggers", "planner", "service", "Starts work when a watched event happens.", ()),
    ("job_watcher", "Job watcher", "planner", "service", "Watches saved job searches for new matches.", ()),
    ("price_alerts", "Price alerts", "planner", "service", "Watches market prices against your alert levels.", ()),
    ("teams", "Worker teams", "helpers", "service", "Temporary specialist teams sized by this machine.", ()),
    ("modes", "Modes", "router", "service", "DEEP, ADVERSARIAL and other turn modes that change routing.", ()),
    ("model_capabilities", "Model inventory", "router", "service",
     "Which installed model serves each role, and what each can do.", ("model",)),
    ("model_resources", "Model resources", "router", "service",
     "GPU memory and context sizes for the loaded models.", ("local_inference",)),
    ("hayat_model", "HAYAT identity model", "router", "service",
     "Builds and checks HAYAT's own identity model.", ()),
    ("cost_policy", "Cost policy", "router", "service", "Keeps private tasks on free local models.", ()),
    ("colibri_service", "Colibri engine", "router", "service",
     "The optional second inference engine, able to serve HAYAT's own weights; started only on request. Its "
     "check reads the engine's own scheduler counters.", ()),
    ("promoter", "Skill promoter", "learning", "service", "Promotes repeated successful procedures to skills.", ()),
    ("training_lifecycle", "Training lifecycle", "learning", "service",
     "The gated private fine-tune lifecycle.", ()),
    ("apprenticeship", "Apprenticeship", "learning", "service", "Learns procedures from watching you work.", ()),
    ("improvement", "Self-improvement", "learning", "service", "Finds and proposes its own fixes.", ()),
    ("evaluation", "Evaluation", "verifier", "service", "Scores answers and routes against held-out checks.",
     ("benchmark_multimodal",)),
    ("tool_benchmarks", "Tool benchmarks", "tool_judgment", "service",
     "Measures tool matching against benchmark requests.", ()),
    ("labs", "Labs", "invention", "service", "Bounded experiments run for learning.", ()),
    ("downloads", "Downloads & scanner", "network", "service",
     "Downloads with an antimalware scan before use.", ("web",)),
    ("connector_gateway", "Connector gateway", "network", "service",
     "Scoped calls to connected services through secrets it never shows.", ()),
    ("mcp_gateway", "MCP gateway", "network", "service", "Model Context Protocol servers HAYAT can use.",
     ("mcp_gateway",)),
    ("browser_extension", "Browser extension bridge", "network", "service",
     "The local bridge to the HAYAT browser extension (port 8765).", ("browser_extension_bridge",)),
    ("publisher", "Publisher", "network", "service", "Publishes your website after your confirmation.", ()),
    ("discovery", "Source discovery", "research", "service",
     "Idle scout for official public sources; never bypasses restrictions.", ()),
    ("topics", "Conversation topics", "cortex", "service",
     "Places each message in a conversation and topic, reads follow-ups in their topic and keeps replies to "
     "their own topic's history (topics.db).", ()),
    ("retention", "Retention practice", "memory", "service",
     "Spaced reviews of whether each memory still comes back from its own key words (retention.db).", ()),
    ("forecasts", "Forecast ledger", "cortex", "store",
     "HAYAT's own forecasts with their check dates and your verdicts (forecasts.db).", ()),
    ("scorecard", "Scorecard", "observability", "service",
     "Dated measurements of accuracy, hallucination, calibration, retention, latency and resources.", ()),
    ("development", "Development stages", "observability", "service",
     "Twelve stages from observation to problem-solving, each passed only on measured records.", ()),
    ("robustness", "Robustness laboratory", "observability", "service",
     "Measures routing under misspellings, the injection guard, and discovered models under attack (ART).", ()),
    ("code_defense", "Code defence", "authority", "service",
     "Security review of HAYAT's own code and packages; tests patches on an isolated copy, never applies them.", ()),
    ("model_discovery", "Model discovery", "research", "service",
     "Finds whether a column can be predicted, against a baseline, with leak-free validation.", ()),
    ("secret_broker", "Secret broker", "authority", "service",
     "Holds credentials and leases them to one call at a time.", ("secret_broker",)),
    ("security", "Security auditor", "authority", "service", "Defensive audit of this machine and HAYAT.", ()),
    ("supervisor", "Supervisor", "observability", "service",
     "Watches the model service and brings it back.", ("supervisor",)),
    ("recovery", "Crash recovery", "observability", "service", "Restores interrupted work after a crash.", ()),
    ("incidents", "Incidents", "observability", "service", "Groups related failures into incidents.", ()),
    ("code_monitor", "Code monitor", "observability", "service", "Watches HAYAT's own code for problems.", ()),
    ("dependencies", "Dependencies", "observability", "service", "Checks the packages HAYAT depends on.", ()),
    ("thoughts", "Thought stream", "interface", "service", "Narrates what HAYAT is doing as it works.", ()),
    ("attachments", "Attachments", "interface", "service", "Reads files you attach, after a malware scan.", ()),
    ("displays", "Displays", "perception", "service", "The screens attached to this computer.", ()),
    ("clipboard", "Clipboard", "perception", "service", "Reads or writes the clipboard when allowed.", ()),
    ("vision", "Vision analyzer", "perception", "service", "Describes images with the vision model.", ()),
    ("camera_sessions", "Camera sessions", "perception", "service", "Bounded camera sessions and watch mode.",
     ("camera",)),
    ("launch", "Launchpad", "tools", "service", "Business launch checklist and steps.", ()),
    ("money_desk", "Money desk", "tools", "service", "Owner-only approvals for wallet actions.", ()),
    ("brief", "Business brief", "tools", "service", "Daily business summary from your own books.", ()),
)

#: Model roles that do not live on the router node.
MODEL_HOME = {"embedding_model": "memory", "vision_model": "perception"}

#: Which intent each specialist receives from the manager (app.agents.manager.ManagerAgent.run).
SPECIALIST_ROUTES = {"CODING": ("developer", "coder"), "RESEARCH": ("researcher",),
                     "COMPUTER_CONTROL": ("computer",)}

INTENT_WORDS = {
    "CONVERSATION": "small talk and chat", "QUESTION": "a question answered directly",
    "SYSTEM_STATE": "asking about HAYAT's own state", "DIAGNOSTIC": "diagnosing a problem",
    "RESEARCH": "researching something", "ACTION": "doing a task with tools", "CONTROL": "control commands",
    "MEMORY": "remembering or recalling", "LEARNING": "learning or teaching", "AUTOMATION": "automating",
    "CODING": "writing code", "COMPUTER_CONTROL": "using the computer", "BROWSER": "browsing a website",
    "UNKNOWN": "not recognised; handled cautiously",
}

TIER_WORDS = {
    "PASSIVE": "observing only; no effect on the world", "ORDINARY": "everyday reversible local work",
    "EXTENDED": "broader local reach, still recoverable",
    "HIGH_CONSEQUENCE": "security, credentials, money, posting and sending",
    "PROHIBITED": "never permitted by this runtime",
}


@dataclass(frozen=True, slots=True)
class Component:
    component_id: str
    label: str
    kind: str
    parent: str
    what: str
    #: Registered tools behind this neuron (capability areas only).
    tools: tuple[str, ...] = ()
    #: A window to open: a HayatGUI method name, "view:<name>" or "studio:<name>".
    opens: str = ""
    opens_label: str = ""
    #: The runtime attribute whose status() is its readiness check.
    probe: str = ""
    #: A short fact: the configured model, the categories governed, the backing service.
    detail: str = ""
    #: Event sources this component reports under.
    sources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Link:
    a: str
    b: str
    kind: str          # "co-use", "governs", "routes", "integration"
    weight: int
    basis: str


@dataclass(frozen=True, slots=True)
class State:
    calls: int = 0
    failures: int = 0
    last_at: float | None = None
    last_ok: bool | None = None
    note: str = ""
    #: What the counts are: "tool calls", "turns routed", "authority decisions"...
    unit: str = ""
    readiness: str = ""
    readiness_note: str = ""


@dataclass(slots=True)
class Snapshot:
    """Everything the Brain 5D view needs about its component neurons."""
    components: tuple[Component, ...]
    states: dict[str, State] = field(default_factory=dict)
    links: tuple[Link, ...] = ()
    usage_since: float | None = None
    usage_rows: int = 0
    build_ms: float = 0.0
    #: Memory and knowledge neurons bringing the brain to 6× its parts (app/brain_population.py).
    population: Any = None

    def tool_index(self) -> dict[str, tuple[str, ...]]:
        index: dict[str, list[str]] = {}
        for component in self.components:
            for tool in component.tools:
                index.setdefault(tool, []).append(component.component_id)
        return {tool: tuple(owners) for tool, owners in index.items()}

    def counts(self) -> dict[str, int]:
        result = {kind: 0 for kind in KINDS}
        for component in self.components:
            result[component.kind] = result.get(component.kind, 0) + 1
        return result

    def link_counts(self) -> dict[str, int]:
        result = {"membership": len(self.components)}
        for link in self.links:
            result[link.kind] = result.get(link.kind, 0) + 1
        return result


# --------------------------------------------------------------- building

def capability_components(tool_names: Iterable[str]) -> list[Component]:
    from app.capabilities import CATALOG, classify

    grouped, _unplaced = classify(sorted(set(tool_names)))
    result = []
    for capability in CATALOG:
        tools = tuple(grouped.get(capability.key, ()))
        backing = CAPABILITY_BACKING.get(capability.key, "")
        result.append(Component(
            f"cap:{capability.key}", capability.title, "capability", CAPABILITY_HOME.get(capability.key, "tools"),
            capability.summary, tools, capability.opens, capability.opens_label or capability.title,
            backing, f"{len(tools)} registered tool{'s' if len(tools) != 1 else ''}"
            + (f" · backed by runtime.{backing}" if backing else "")))
    return result


def tool_components(capabilities: Iterable[Component], descriptions: dict[str, str] | None = None) -> list[Component]:
    """Each registered tool as its own neuron inside its capability area (the area's ``tools``, nothing else).

    Before 2026-11-08 a tool existed in the map only as part of its area's count. Now every one is a neuron with
    its own recorded calls, failures and last outcome, linked to the tools it actually ran with.
    """
    descriptions = descriptions or {}
    result = []
    for area in capabilities:
        if area.kind != "capability":
            continue
        for name in area.tools:
            what = " ".join(str(descriptions.get(name) or f"A tool in {area.label}.").split())
            result.append(Component(f"tool:{name}", name, "tool", area.component_id,
                                    what if len(what) <= 220 else what[:219].rstrip() + "…", (name,),
                                    probe="registry", detail=f"in {area.label}"))
    return result


def intent_components() -> list[Component]:
    from app.intent.models import IntentKind

    return [Component(f"intent:{kind.value}", kind.value.replace("_", " ").title(), "intent", "intent",
                      f"Requests routed as {kind.value}: {INTENT_WORDS.get(kind.value, kind.value.lower())}.",
                      probe="intent_router")
            for kind in IntentKind]


def model_components(config: dict[str, Any]) -> list[Component]:
    result = []
    for role, selection in (config or {}).items():
        if not role.endswith("_model") or not isinstance(selection, dict):
            continue
        model = selection.get("model")
        provider = selection.get("provider") or "auto"
        name = role.removesuffix("_model").replace("_", " ")
        detail = f"{provider} / {model}" if model else f"{provider} / no model: the deterministic path"
        result.append(Component(f"model:{role}", f"{name.title()} model", "model", MODEL_HOME.get(role, "router"),
                                f"The model slot the router uses for {name}.", probe="intelligent_router",
                                detail=detail))
    return result


def specialist_components(roles: Iterable[str]) -> list[Component]:
    routed = {role: intent for intent, roles_ in SPECIALIST_ROUTES.items() for role in roles_}
    return [Component(f"helper:{role}", f"{role.title()} specialist", "specialist", "helpers",
                      f"A focused {role} helper on the shared agent core and its tool permissions.",
                      probe="manager",
                      detail=f"receives {routed[role]} work" if role in routed else "receives work by request words")
            for role in sorted(set(roles))]


def policy_components() -> list[Component]:
    from app.safety.authority import CATEGORY_TIERS, RiskTier

    result = []
    for tier in RiskTier:
        categories = sorted(name for name, value in CATEGORY_TIERS.items() if value == tier)
        result.append(Component(f"tier:{tier.name}", tier.name.replace("_", " ").title() + " tier", "policy",
                                "authority", f"Actions that are {TIER_WORDS.get(tier.name, tier.name.lower())}.",
                                probe="authority", detail=f"{len(categories)} categories: " + ", ".join(categories)))
    return result


def service_components(present: Iterable[str]) -> list[Component]:
    present = set(present)
    backed = set(CAPABILITY_BACKING.values())
    return [Component(f"svc:{attr}", label, kind, parent, what, probe=attr, sources=sources,
                      detail=f"runtime.{attr}")
            for attr, label, parent, kind, what, sources in SERVICES
            if attr in present and attr not in SUBSYSTEM_OBJECTS and attr not in backed]


def build(*, tool_names: Iterable[str] = (), model_config: dict[str, Any] | None = None,
          specialists: Iterable[str] = (), services: Iterable[str] = (),
          include_intents: bool = True, include_policy: bool = True, include_tools: bool = False,
          tool_descriptions: dict[str, str] | None = None) -> list[Component]:
    """Every component neuron from supplied facts; nothing is added that the inputs do not name."""
    tool_names = list(tool_names)
    components = capability_components(tool_names) if tool_names else []
    if include_tools:
        components += tool_components(list(components), tool_descriptions)
        mapped = {name for component in components if component.kind == "tool" for name in component.tools}
        for name in sorted(set(tool_names) - mapped):
            description = " ".join(str((tool_descriptions or {}).get(name) or "A registered tool with no mapped capability area.").split())
            components.append(Component(f"tool:{name}", name, "tool", "tools", description[:220], (name,),
                                        probe="registry", detail="Registered tool; capability area not mapped"))
    if include_intents:
        components += intent_components()
    components += model_components(model_config or {})
    components += specialist_components(specialists)
    if include_policy:
        components += policy_components()
    components += service_components(services)
    seen: set[str] = set()
    unique = []
    for component in components:
        if component.component_id not in seen:
            seen.add(component.component_id)
            unique.append(component)
    return unique


def from_runtime(runtime: Any) -> list[Component]:
    """Component neurons for this runtime, from its own registry, router, manager and services."""
    registry = getattr(runtime, "registry", None)
    try:
        names = list(registry.names()) if registry is not None else []
    except Exception:
        log_swallowed(_log, "brain components: registry names")
        names = []
    router = getattr(runtime, "router", None)
    config = getattr(router, "config", None)
    manager = getattr(runtime, "manager", None)
    specialists = list(getattr(manager, "specialists", {}) or {})
    present = [attr for attr, *_rest in SERVICES if getattr(runtime, attr, None) is not None]
    descriptions: dict[str, str] = {}
    try:
        descriptions = {str(tool["name"]): str(tool.get("description") or "") for tool in registry.describe()}
    except Exception:
        log_swallowed(_log, "brain components: tool descriptions")
    return build(tool_names=names, model_config=config if isinstance(config, dict) else {},
                 specialists=specialists, services=present,
                 include_intents=getattr(runtime, "intent_router", None) is not None,
                 include_policy=getattr(runtime, "authority", None) is not None,
                 include_tools=True, tool_descriptions=descriptions)


# ------------------------------------------------------------------ links

def tool_tiers(permissions: dict[str, str]) -> dict[str, str]:
    """Tool name -> risk tier name, from each tool's permission category."""
    from app.safety.authority import CATEGORY_TIERS

    result = {}
    for tool, category in permissions.items():
        tier = CATEGORY_TIERS.get(str(category))
        if tier is not None:
            result[tool] = tier.name
    return result


def governance_links(components: Iterable[Component], tiers_by_tool: dict[str, str]) -> list[Link]:
    """Each capability area to the risk tiers its tools need, weighted by how many tools."""
    known = {component.component_id for component in components}
    links = []
    for component in components:
        if component.kind != "capability":
            continue
        weights: dict[str, int] = {}
        for tool in component.tools:
            tier = tiers_by_tool.get(tool)
            if tier:
                weights[tier] = weights.get(tier, 0) + 1
        for tier, weight in sorted(weights.items()):
            if f"tier:{tier}" in known:
                links.append(Link(component.component_id, f"tier:{tier}", "governs", weight,
                                  f"{weight} of its tools need {tier.replace('_', ' ').lower()} authority"))
    return links


def route_links(components: Iterable[Component]) -> list[Link]:
    known = {component.component_id for component in components}
    links = []
    for intent, roles in SPECIALIST_ROUTES.items():
        for role in roles:
            if f"intent:{intent}" in known and f"helper:{role}" in known:
                links.append(Link(f"intent:{intent}", f"helper:{role}", "routes", 1,
                                  f"the manager hands {intent} work to the {role}"))
                break
    return links


def integration_links(components: Iterable[Component], runtime: Any) -> list[Link]:
    """Verified runtime handoffs, separate from recorded co-use or activity."""
    known = {component.component_id for component in components}
    links = []
    for name, tool in (("model_discovery", "model_discover"), ("robustness", "robustness_lab"),
                       ("code_defense", "code_defense")):
        service = f"svc:{name}"
        target = getattr(runtime, name, None)
        try:
            handler = runtime.registry.get(tool).handler
            bound = inspect.ismethod(handler) and handler.__self__ is target
            captured = inspect.isfunction(handler) and any(cell.cell_contents is target for cell in handler.__closure__ or ())
        except (AttributeError, KeyError, ValueError):
            bound, captured = False, False
        if target is not None and (bound or captured) and service in known and f"tool:{tool}" in known:
            links.append(Link(f"tool:{tool}", service, "integration", 1,
                              f"the registered handler references this exact runtime.{name} instance; execution not tested"))
    discovery = getattr(runtime, "model_discovery", None)
    robustness = getattr(runtime, "robustness", None)
    if (discovery is not None and robustness is not None and getattr(robustness, "discovery", None) is discovery
            and {"svc:model_discovery", "svc:robustness"} <= known):
        links.append(Link("svc:model_discovery", "svc:robustness", "integration", 1,
                          "Robustness holds this exact model-discovery instance; its last fitted model is the test input"))
    # Research to invention: the hand-off tool belongs to this runtime's workbench, and the workbench's supplier
    # of finished searches was built over this same runtime (app/runtime.py), not a look-alike.
    workbench = getattr(runtime, "invention", None)
    supplier = getattr(workbench, "research_result_supplier", None)
    try:
        handler = runtime.registry.get("invention_from_research").handler
        owned = inspect.ismethod(handler) and handler.__self__ is workbench
        sourced = inspect.isfunction(supplier) and any(cell.cell_contents is runtime for cell in supplier.__closure__ or ())
    except (AttributeError, KeyError, ValueError):
        owned, sourced = False, False
    if (workbench is not None and owned and sourced and getattr(runtime, "research_jobs", None) is not None
            and {"cap:research", "tool:invention_from_research"} <= known):
        links.append(Link("cap:research", "tool:invention_from_research", "integration", 1,
                          "the invention workbench reads this runtime's finished searches and saves a reviewed draft; "
                          "execution not tested"))
    return links


def co_use_links(components: Iterable[Component], usage: Any, *, limit: int = 60) -> list[Link]:
    """Components used in the same recorded turn or task, strongest first.

    Two capability areas are linked when their tools ran in one turn (or one
    task with no turn); an intent route is linked to the areas whose tools its
    turns ran. The weight is how many turns or tasks did so.
    """
    components = list(components)
    index: dict[str, list[str]] = {}
    tool_neurons: dict[str, str] = {}
    for component in components:
        if component.kind == "tool":
            tool_neurons[component.tools[0]] = component.component_id
            continue  # tools link to tools below, not to areas: a tool and its own area are membership
        for tool in component.tools:
            index.setdefault(tool, []).append(component.component_id)
    known = {component.component_id for component in components}
    weights: dict[tuple[str, str], int] = {}
    tool_weights: dict[tuple[str, str], int] = {}
    for group in getattr(usage, "groups", ()) or ():
        areas = sorted({owner for tool in group.tools for owner in index.get(tool, ())})
        members = list(areas)
        if group.intent and f"intent:{group.intent}" in known:
            members.append(f"intent:{group.intent}")
        for a, b in itertools.combinations(sorted(set(members)), 2):
            if a.startswith("intent:") and b.startswith("intent:"):
                continue
            weights[(a, b)] = weights.get((a, b), 0) + 1
        ran = sorted({tool_neurons[tool] for tool in group.tools if tool in tool_neurons})
        for a, b in itertools.combinations(ran, 2):
            tool_weights[(a, b)] = tool_weights.get((a, b), 0) + 1
    ordered = sorted(weights.items(), key=lambda item: (-item[1], item[0]))[:max(0, int(limit))]
    # Tool-to-tool links are the finest connections the record holds: two tools that ran in one turn or task.
    ordered += sorted(tool_weights.items(), key=lambda item: (-item[1], item[0]))[:max(0, int(limit)) * 4]
    return [Link(a, b, "co-use", weight,
                 f"used together in {weight} recorded turn{'s' if weight != 1 else ''} or task{'s' if weight != 1 else ''}")
            for (a, b), weight in ordered]


# ----------------------------------------------------------------- states

def model_outcomes(path: Path | str | None) -> dict[str, Any]:
    """Verified outcomes per model from the router's own table, read-only."""
    from app.brain_history import Count, _epoch

    if not path:
        return {}
    try:
        with sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=2) as db:
            rows = db.execute(
                "SELECT model, COUNT(*), SUM(CASE WHEN success=1 AND corrected=0 THEN 0 ELSE 1 END), MAX(timestamp), "
                "(SELECT success FROM model_task_outcomes o WHERE o.model=m.model AND verified=1 ORDER BY id DESC LIMIT 1) "
                "FROM model_task_outcomes m WHERE verified=1 GROUP BY model").fetchall()
    except sqlite3.Error:
        return {}
    return {str(model): Count(int(calls), int(failed or 0), _epoch(stamp) if stamp else None,
                              None if last is None else bool(last))
            for model, calls, failed, stamp, last in rows}


def states(components: Iterable[Component], usage: Any, *, models: dict[str, Any] | None = None,
           readiness: dict[str, tuple[str, str]] | None = None) -> dict[str, State]:
    """Live numbers for each component from recorded usage; zero means none recorded."""
    tools = getattr(usage, "tools", {}) or {}
    intents = getattr(usage, "intents", {}) or {}
    tiers = getattr(usage, "tiers", {}) or {}
    sources = getattr(usage, "sources", {}) or {}
    models = models or {}
    readiness = readiness or {}
    result: dict[str, State] = {}
    for component in components:
        counts: list[Any] = []
        unit = ""
        if component.kind in {"capability", "tool"}:
            counts, unit = [tools[name] for name in component.tools if name in tools], "tool calls"
        elif component.kind == "intent":
            key = component.component_id.split(":", 1)[1]
            counts, unit = ([intents[key]] if key in intents else []), "turns routed"
        elif component.kind == "policy":
            key = component.component_id.split(":", 1)[1]
            counts, unit = ([tiers[key]] if key in tiers else []), "authority decisions (failures = denials)"
        elif component.kind == "model":
            model = component.detail.split(" / ", 1)[-1]
            counts, unit = ([models[model]] if model in models else []), "verified model outcomes"
        elif component.probe == "events":
            # The event log's own numbers are everything it holds.
            counts, unit = list(sources.values()), "events recorded (failures = degraded reports)"
        elif component.probe == "turns":
            counts, unit = list(intents.values()), "turns recorded (failures = failed turns)"
        elif component.kind in {"service", "store"}:
            attr = component.probe
            names = [*component.sources, attr]
            counts = [sources[name] for name in dict.fromkeys(names) if name in sources]
            unit = "events logged (failures = degraded reports)"
        calls = sum(int(item.calls) for item in counts)
        failures = sum(int(item.failures) for item in counts)
        latest = max((item for item in counts if item.last_at is not None), key=lambda item: item.last_at,
                     default=None)
        ready, ready_note = readiness.get(component.component_id, ("", ""))
        result[component.component_id] = State(
            calls, failures, latest.last_at if latest else None, latest.last_ok if latest else None,
            latest.last_note if latest else "", unit or "not logged per component", ready, ready_note)
    return result


def capability_readiness(runtime: Any) -> dict[str, tuple[str, str]]:
    """capabilities.readiness for every area: cheap, no network or model call."""
    from app.capabilities import CATALOG, readiness

    result = {}
    for capability in CATALOG:
        try:
            result[f"cap:{capability.key}"] = readiness(capability, runtime)
        except Exception as exc:
            result[f"cap:{capability.key}"] = ("off", f"Readiness check failed: {type(exc).__name__}")
    return result


def tool_permissions(runtime: Any) -> dict[str, str]:
    registry = getattr(runtime, "registry", None)
    try:
        return {str(item["name"]): str(item.get("permission") or "") for item in registry.describe()}
    except Exception:
        log_swallowed(_log, "brain components: tool permissions")
        return {}


def snapshot(runtime: Any, *, events_path: Path | str | None = None, co_use_limit: int = 60) -> Snapshot:
    """Build the whole component layer for a runtime. Runs off the UI thread."""
    import time

    from app.brain_history import Usage, load_usage

    started = time.perf_counter()
    components = from_runtime(runtime)
    path = events_path or getattr(getattr(runtime, "events", None), "path", None)
    usage = load_usage(Path(path)) if path else Usage(None, None, 0, {}, {}, {}, {}, ())
    router_path = getattr(getattr(runtime, "intelligent_router", None), "path", None)
    links = [*co_use_links(components, usage, limit=co_use_limit),
             *governance_links(components, tool_tiers(tool_permissions(runtime))),
             *route_links(components), *integration_links(components, runtime)]
    result = Snapshot(tuple(components),
                      states(components, usage, models=model_outcomes(router_path),
                             readiness=capability_readiness(runtime)),
                      tuple(links), usage.since, usage.rows)
    try:
        from app.brain_population import SUBSYSTEMS, from_runtime as population_from_runtime

        result.population = population_from_runtime(runtime, SUBSYSTEMS + len(components))
    except Exception:  # a locked or missing store leaves the parts as they were
        log_swallowed(_log, "brain components: memory and knowledge neurons")
    result.build_ms = round((time.perf_counter() - started) * 1000, 1)
    return result


# ------------------------------------------------------------- placement

#: The brain shell's semi-axes, the same ellipsoid the subsystem layout keeps inside.
SHELL = (1.22, .88, 1.08)


def place(components: Iterable[Component], layout: dict[str, tuple[float, float, float]],
          *, radius: float = .15, inside: float = .9) -> dict[str, tuple[float, float, float]]:
    """Fixed 3D positions: each component on a small sphere round its subsystem.

    A golden-angle spiral spreads one subsystem's members evenly; a subsystem
    with many members gets a slightly larger sphere. Points stay inside the
    brain shell. Deterministic, so the map never reshuffles between refreshes.
    """
    groups: dict[str, list[Component]] = {}
    for component in components:
        if component.parent in layout:
            groups.setdefault(component.parent, []).append(component)
    order = list(KINDS)
    positions = {}
    golden = math.pi * (3 - math.sqrt(5))
    for parent, members in groups.items():
        members.sort(key=lambda item: (order.index(item.kind) if item.kind in order else 99, item.component_id))
        px, py, pz = layout[parent]
        count = len(members)
        size = radius * (1 + .09 * math.sqrt(count))
        _spiral(members, (px, py, pz), size, inside, golden, positions)
    # Tools sit on a small sphere round their own capability area, one level finer.
    tools: dict[str, list[Component]] = {}
    for component in components:
        if component.kind == "tool" and component.parent in positions:
            tools.setdefault(component.parent, []).append(component)
    for parent, members in tools.items():
        members.sort(key=lambda item: item.component_id)
        _spiral(members, positions[parent], radius * .32 * (1 + .06 * math.sqrt(len(members))), inside, golden,
                positions)
    return positions


def _spiral(members: list[Component], centre: tuple[float, float, float], size: float, inside: float,
            golden: float, positions: dict[str, tuple[float, float, float]]) -> None:
    px, py, pz = centre
    count = len(members)
    if count:
        for i, component in enumerate(members):
            y = 1 - 2 * (i + .5) / count
            ring = math.sqrt(max(0.0, 1 - y * y))
            theta = i * golden
            x, yy, z = px + size * ring * math.cos(theta), py + size * y * .8, pz + size * ring * math.sin(theta)
            reach = (x / SHELL[0]) ** 2 + (yy / SHELL[1]) ** 2 + (z / SHELL[2]) ** 2
            if reach > inside:
                shrink = math.sqrt(inside / reach)
                x, yy, z = x * shrink, yy * shrink, z * shrink
            positions[component.component_id] = (x, yy, z)


# ------------------------------------------------------------ describing

def describe(component: Component, state: State | None, *, parent_label: str = "",
             links: Iterable[Link] = (), labels: dict[str, str] | None = None) -> str:
    """Plain words for one component neuron: what it is, its live numbers, its links."""
    import time

    labels = labels or {}
    state = state or State()
    lines = [f"{component.label}  ·  {KINDS.get(component.kind, component.kind)}"
             + (f"  ·  part of {parent_label}" if parent_label else ""),
             f"What: {component.what}"]
    if component.detail:
        lines.append(f"Detail: {component.detail}")
    if component.tools:
        shown = ", ".join(component.tools[:12])
        lines.append(f"Tools ({len(component.tools)}): {shown}{' …' if len(component.tools) > 12 else ''}")
    if state.calls or state.failures:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(state.last_at)) if state.last_at else "unknown"
        outcome = "succeeded" if state.last_ok else "failed" if state.last_ok is False else "recorded"
        lines.append(f"Recorded: {state.calls:,} {state.unit}; {state.failures:,} failed. "
                     f"Last {outcome} at {when}" + (f" ({state.note})" if state.note else "") + ".")
    else:
        lines.append(f"Recorded: none yet ({state.unit or 'not logged per component'}).")
    if state.readiness:
        lines.append(f"Readiness: {state.readiness} · {state.readiness_note}")
    related = [link for link in links if component.component_id in (link.a, link.b)]
    for kind, title in (("co-use", "Used together with"), ("governs", "Governed by"), ("routes", "Routes"),
                        ("integration", "Software connections (not activity)")):
        rows = [link for link in related if link.kind == kind]
        if rows:
            names = [f"{labels.get(link.b if link.a == component.component_id else link.a, '?')} ({link.weight})"
                     for link in sorted(rows, key=lambda link: -link.weight)[:8]]
            lines.append(f"{title}: " + "; ".join(names))
    return "\n".join(lines)


def check(component: Component, runtime: Any) -> str:
    """The component's own readiness check, run on demand. May block briefly: call it off the UI thread."""
    target = getattr(runtime, component.probe, None) if component.probe else None
    if component.kind == "capability":
        from app.capabilities import BY_KEY, readiness

        capability = BY_KEY.get(component.component_id.split(":", 1)[1])
        names = set(runtime.registry.names()) if getattr(runtime, "registry", None) is not None else set()
        missing = [tool for tool in component.tools if tool not in names]
        state, note = readiness(capability, runtime) if capability is not None else ("off", "Unknown area.")
        return (f"Readiness: {state} · {note}\nTools registered now: {len(component.tools) - len(missing)} of "
                f"{len(component.tools)}" + (f"; missing: {', '.join(missing[:8])}" if missing else "."))
    if component.kind == "model":
        role = component.component_id.split(":", 1)[1]
        router = getattr(runtime, "router", None)
        provider = None
        try:
            provider = router.get(role) if router is not None else None
        except Exception as exc:
            return f"Model role check failed: {type(exc).__name__}: {exc}"
        model = getattr(provider, "model", None)
        health = {}
        try:
            health = runtime.intelligent_router.status().get("model_health", {}).get(model or "", {})
        except Exception:
            log_swallowed(_log, "brain components: router health")
        return (f"Provider: {getattr(provider, 'name', None) or 'none'} · model: {model or 'none'}"
                + (f"\nRouter health: {health}" if health else "\nRouter health: no failures recorded for it."))
    if component.kind == "tool":
        name = component.tools[0]
        registry = getattr(runtime, "registry", None)
        try:
            spec = registry.get(name) if registry is not None else None
        except Exception:
            spec = None
        if spec is None:
            return f"{name} is not registered in this session."
        record = {}
        understanding = getattr(runtime, "tool_understanding", None)
        if understanding is not None:
            try:
                record = understanding.record_for(name) or {}
            except Exception:
                log_swallowed(_log, "brain components: tool record")
        line = f"Registered · risk {spec.risk} · permission {spec.permission}"
        if record.get("calls"):
            succeeded = int(record.get("succeeded") or 0)
            line += (f"\nUsed {record['calls']} times: {succeeded} succeeded"
                     + (f", {int(record.get('refused') or 0)} refused" if record.get("refused") else "")
                     + (f"; about {round(float(record['avg_ms']))} ms each" if record.get("avg_ms") else "") + ".")
        else:
            line += "\nNo recorded use yet."
        return line
    if component.kind == "intent":
        router = getattr(runtime, "intent_router", None)
        return ("Intent router present; this route is one it can return." if callable(getattr(router, "route", None))
                else "Intent router missing.")
    if component.kind == "specialist":
        role = component.component_id.split(":", 1)[1]
        helper = (getattr(target, "specialists", {}) or {}).get(role)
        shared = helper is not None and getattr(helper, "core", None) is getattr(runtime, "agent", None)
        return (f"Registered: {'yes' if helper is not None else 'no'} · shares the agent core and its permissions: "
                f"{'yes' if shared else 'no'}")
    if component.kind == "policy":
        try:
            status = runtime.authority.status()
        except Exception as exc:
            return f"Authority status failed: {type(exc).__name__}: {exc}"
        categories = component.detail.split(": ", 1)[-1].split(", ")
        auto = [name for name in categories if name in set(status.get("auto_categories", []))]
        protected = [name for name in categories if name in set(status.get("always_protected", []))]
        return (f"Owner mode: {status.get('owner_mode')} · privacy mode: {status.get('privacy_mode')}\n"
                f"Allowed automatically for the owner: {', '.join(auto) or 'none'}\n"
                f"Always asks or refuses: {', '.join(protected) or 'none'}")
    if target is None:
        return f"runtime.{component.probe} is not available in this session."
    if component.probe == "colibri_service":
        from app.models.colibri_service import describe_status

        try:
            return "Status: " + describe_status(target.status())
        except Exception as exc:
            return f"Status check failed: {type(exc).__name__}: {exc}"
    status = getattr(target, "status", None)
    if not callable(status):
        return f"runtime.{component.probe} is present ({type(target).__name__}); it has no status check."
    try:
        result = status()
    except Exception as exc:
        return f"Status check failed: {type(exc).__name__}: {exc}"
    if isinstance(result, dict):
        shown = "; ".join(f"{key.replace('_', ' ')}: {str(value)[:80]}" for key, value in list(result.items())[:10])
        return f"Status ({type(target).__name__}): {shown or 'empty'}"
    return f"Status ({type(target).__name__}): {str(result)[:400]}"
