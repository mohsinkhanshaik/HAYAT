from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.agent.context import AgentContext, UNTRUSTED_DATA_NOTICE
from app.agent.critic import PlanCritic
from app.intent.models import TaskComplexity
from app.models.base import Message, ModelProvider
from app.research.manager import ResearchManager
from app.tools.registry import ToolRegistry


@dataclass(slots=True)
class PlanStep:
    description: str
    tool: str
    arguments: dict[str, Any]
    expected: dict[str, Any] = field(default_factory=lambda: {"type": "tool_success"})
    max_retries: int = 2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Planner:
    def __init__(self, registry: ToolRegistry, model: ModelProvider | None = None, max_steps: int = 20, knowledge: Any | None = None) -> None:
        self.registry = registry
        self.model = model
        self.max_steps = max_steps
        self.knowledge = knowledge
        self.last_knowledge_ids: list[str] = []
        self.critic = PlanCritic()
        self.last_direct_response: str | None = None
        self.last_candidate_tools: list[str] = []

    def create_plan(self, goal: str, context: AgentContext | None = None, intent: Any | None = None, session: Any | None = None) -> list[PlanStep]:
        self.last_knowledge_ids = []
        self.last_direct_response = None
        deterministic = self._deterministic_plan(goal, intent, session)
        if deterministic:
            self.last_candidate_tools = [step.tool for step in deterministic]
            return deterministic
        if self.model:
            return self._model_plan(goal, context, intent)
        if getattr(intent, "entities", {}).get("preferred_shell"):
            raise ValueError("I need the local planning model to turn that task into a shell command. "
                             "You can also give me an exact command after 'Run CMD:' or 'Run PowerShell:'.")
        return self._safe_fallback(goal)

    def _deterministic_plan(self, goal: str, intent: Any | None = None, session: Any | None = None) -> list[PlanStep] | None:
        normalized = " ".join(goal.lower().split())
        names = self.registry.names()
        from app.research.onion import parse_tor_web_request
        tor_query = parse_tor_web_request(goal)
        if tor_query and 'tor_web_research' in names:
            return [PlanStep(description='Research ordinary public sources through Tor only.',
                             tool='tor_web_research', arguments={'query': tor_query, 'level': 'deep'},
                             expected={'type': 'tool_success'}, max_retries=0)]
        from app.research.contact_actions import plan as contact_plan
        contacts = contact_plan(goal, names)
        if contacts:
            return [PlanStep(**contacts)]
        from app.research.people_finder import plan as people_plan
        people = people_plan(goal, names)
        if people:
            return [PlanStep(**people)]
        from app.research.onion_directory import plan as onion_sites_plan
        onion_sites = onion_sites_plan(goal, names)
        if onion_sites:
            return [PlanStep(**onion_sites)]
        from app.networking_actions import plan as networking_plan
        networking = networking_plan(goal, names)
        if networking:
            return [PlanStep(**networking)]
        from app.invention_commands import command as invention_command
        invention = invention_command(goal)
        if invention and invention[0] in names:
            expected = {'type': 'invention_run_finished'} if invention[0] == 'invention_run' else {'type': 'tool_success'}
            return [PlanStep('Invention workbench operation', invention[0], invention[1], expected, max_retries=0)]
        from app.intelligence.tools import direct_plan as intelligence_plan
        intelligence = intelligence_plan(goal, names, session)
        if intelligence:
            feeds = intelligence.pop('refresh_first', None)
            steps = [PlanStep(**intelligence)]
            if feeds:
                # "No earthquakes" must mean none reported, not a feed never fetched.
                steps.insert(0, PlanStep('Refresh the public feeds this answer depends on', 'intelligence_refresh',
                                         {'feeds': feeds, 'due_only': True}, {'type': 'tool_success'}, max_retries=0))
            return steps
        from app.portfolio import direct_plan as portfolio_plan
        portfolio_step = portfolio_plan(goal, names)
        if portfolio_step:
            return [PlanStep(**portfolio_step)]
        from app.money_desk import direct_plan as desk_plan
        desk_step = desk_plan(goal, names)
        if desk_step:
            return [PlanStep(**desk_step)]
        from app.markets import direct_plan as market_plan
        market = market_plan(goal, names)
        if market:
            return [PlanStep(**market)]
        from app.web3_chat import direct_plan as web3_plan
        web3 = web3_plan(goal, names)
        if web3:
            return [PlanStep(**web3)]
        from app.accounts_chat import direct_plan as account_plan
        account = account_plan(goal, names)
        if account:
            return [PlanStep(**account)]
        from app.reverse_engineer import direct_plan as reverse_engineer_plan
        reverse_engineer = reverse_engineer_plan(goal, names)
        if reverse_engineer:
            return [PlanStep(**reverse_engineer)]
        from app.jobs.tools import direct_plan as job_plan
        from app.workspace_actions import plan as workspace_plan
        workspace = workspace_plan(goal, names)
        if workspace:
            return [PlanStep(**workspace)]
        job = job_plan(goal, names)
        if job:
            return [PlanStep(**job)]
        from app.customer_chat import plan as customer_plan
        customer = customer_plan(goal, names, session, intent)
        if customer:
            return [PlanStep(**customer)]
        from app.video_workflow import direct_video_plan
        video_plan = direct_video_plan(goal, self.registry.names(), session)
        if video_plan:
            return [PlanStep(**video_plan)]
        from app.tools.computer import direct_computer_plan
        computer_plan = direct_computer_plan(goal, self.registry.names())
        if computer_plan:
            return [PlanStep(**step) for step in computer_plan]
        entities = getattr(intent, "entities", {}) if intent else {}
        # Location, the device list and prospect search are read, never imagined.
        from app.situation import direct_situation_plan
        from app.tools.devices import direct_device_plan
        from app.tools.prospects import direct_prospect_plan
        from app.intelligence.discovery import direct_discovery_plan
        from app.csi import direct_csi_plan
        from app.network import direct_network_plan
        from app.text_analysis import direct_text_plan
        from app.tools.faces import direct_face_setup_plan
        from app.tools.image_tools import direct_image_tool_plan
        from app.tools.image_sources import direct_image_source_plan
        from app.email_check import direct_email_check_plan
        from app.location_privacy import direct_privacy_plan
        from app.privacy_masks import direct_masks_plan
        device_plan = (direct_masks_plan(goal, names) or direct_privacy_plan(goal, names) or direct_device_plan(goal, names) or direct_csi_plan(goal, names) or direct_network_plan(goal, names)
                       or direct_text_plan(goal, names) or direct_face_setup_plan(goal, names) or direct_image_tool_plan(goal, names)
                       or direct_image_source_plan(goal, names, getattr(session, 'last_file', None))
                       or direct_email_check_plan(goal, names)
                       or direct_prospect_plan(goal, names)
                       or direct_situation_plan(goal, names) or direct_discovery_plan(goal, names))
        if device_plan:
            return [PlanStep(**step) for step in device_plan]
        if isinstance(entities.get("quran"), dict):
            from app.tools.quran import direct_quran_plan
            quran = entities["quran"]
            tool = "quran_continue" if quran.get("continue") else "quran_read"
            if tool in names:
                return [PlanStep("Read the Quran from the published text with transliteration and translation.",
                                 tool, {} if quran.get("continue") else dict(quran), {"type": "tool_success"}, 0)]
            plan = direct_quran_plan(goal, names, getattr(session, "last_response", "") or "")
            if plan:
                return [PlanStep(**step) for step in plan]
        if isinstance(entities.get("prospects"), dict) and entities["prospects"].get("niche") and "find_prospects" in names:
            request = entities["prospects"]
            return [PlanStep(f"Search the web for {request['niche']} in {request.get('place', '')}, keep real company "
                             "websites, read the contact details each publishes, and save them as leads.",
                             "find_prospects", dict(request), {"type": "tool_success"}, 0)]
        application = entities.get("application")
        handle = entities.get("handle")
        if session and getattr(session, "active_window", None) and (not application or application == getattr(session, "active_application", None)):
            handle = session.active_window.get("handle")
        if application == "notepad" and entities.get("text") and "notepad_edit" in self.registry.names():
            text = str(entities["text"])
            append_line = normalized.startswith("add")
            arguments = {"text": text, "append_line": append_line}
            if handle:
                arguments["handle"] = handle
            edit = PlanStep("Focus the Notepad editor, insert the requested text, and read it back.", "notepad_edit", arguments, {"type": "ui_text_contains", "value": text, "application": "notepad"}, 1)
            if handle:
                return [edit]
            return [PlanStep("Ensure the intended Notepad window exists and is focused.", "launch_application", {"application": "notepad"}, {"type": "application_window_exists", "application": "notepad"}, 1), edit]
        from app.revenue_routing import direct_revenue_plan
        revenue = direct_revenue_plan(goal, names)
        if revenue:
            return [PlanStep(**revenue)]
        if re.fullmatch(r"(?:show|list) (?:my |the )?product catalog[.!?]*", normalized) and "product_catalog" in names:
            return [PlanStep("Read the local digital-product catalog.", "product_catalog", {}, {"type": "tool_success"})]
        if re.fullmatch(r"(?:show|report) (?:my |the )?(?:digital )?product (?:sales(?: revenue)?|revenue)[.!?]*", normalized) and "product_sales_report" in names:
            return [PlanStep("Read recorded digital-product sales by currency.", "product_sales_report", {}, {"type": "tool_success"})]
        from app.wallet_money import direct_plan as wallet_plan
        wallet_step = wallet_plan(goal, names)
        if wallet_step:
            return [PlanStep(**wallet_step)]
        from app.business_deep import direct_plan as deep_plan
        deep = deep_plan(goal, names)
        if deep:
            return [PlanStep(**deep)]
        business = self._business_plan(normalized, names)
        if business:
            return business
        if entities.get("powershell_command") and "powershell" in names:
            return [PlanStep(
                "Run the exact requested PowerShell command and check its exit code.",
                "powershell", {"command": str(entities["powershell_command"])},
                {"type": "exit_code_zero"}, 0,
            )]
        if entities.get("cmd_command") and "terminal" in names:
            return [PlanStep(
                "Run the exact requested CMD command and check its exit code.",
                "terminal", {"command": str(entities["cmd_command"]), "interpreter": "cmd"},
                {"type": "exit_code_zero"}, 0,
            )]
        preferred_shell = entities.get("preferred_shell")
        if preferred_shell in {"cmd", "powershell"}:
            task = str(entities.get("shell_task") or goal).lower()
            tool = "terminal" if preferred_shell == "cmd" else "powershell"
            if tool in names:
                command = None
                if re.search(r"\b(?:current|working|present)\s+(?:directory|folder)|\bwhere\s+(?:am\s+i|are\s+we)\b", task):
                    command = "cd" if preferred_shell == "cmd" else "Get-Location"
                elif re.search(r"\b(?:list|show|display)\s+(?:the\s+)?(?:files|folders|directory\s+contents)\b", task):
                    command = "dir" if preferred_shell == "cmd" else "Get-ChildItem"
                elif re.search(r"\b(?:list|show|display)\s+(?:the\s+)?(?:running\s+)?processes\b", task):
                    command = "tasklist" if preferred_shell == "cmd" else "Get-Process"
                if command:
                    return [PlanStep(
                        f"Use {preferred_shell.upper()} to {task.rstrip('.')}, then check the exit code.",
                        tool, {"command": command, **({"interpreter": "cmd"} if preferred_shell == "cmd" else {})},
                        {"type": "exit_code_zero"}, 0,
                    )]
        system_task = str(entities.get("system_shell_task") or "")
        if system_task and "powershell" in names:
            command = (
                "Get-Process" if "process" in system_task else
                "Get-Service" if "service" in system_task else
                "Get-NetAdapter" if "adapter" in system_task else
                "Get-PSDrive -PSProvider FileSystem"
            )
            return [PlanStep(
                f"Read live {system_task} with PowerShell and check the result.",
                "powershell", {"command": command}, {"type": "exit_code_zero"}, 0,
            )]
        operation = entities.get("file_operation")
        if entities.get("review_project_changes") and "code_scan" in names:
            return [PlanStep(
                "Scan registered project source for changed files and syntax errors.",
                "code_scan", {}, {"type": "tool_success"}, 0,
            )]
        if operation and entities.get("path"):
            path = str(entities["path"])
            if operation == "list" and "list_folder" in names:
                return [PlanStep(f"List {path}.", "list_folder", {"path": path, "recursive": False}, {"type": "tool_success"})]
            if operation == "read" and "read_file" in names:
                return [PlanStep(f"Read {path}.", "read_file", {"path": path}, {"type": "tool_success"})]
            if operation == "find" and "search_files" in names:
                pattern = str(entities.get("pattern") or "*")
                return [PlanStep(f"Find {pattern} in {path}.", "search_files", {"path": path, "pattern": pattern},
                                 {"type": "tool_success"})]
        if entities.get("contact_name") and "contact_save" in names:
            arguments = {"name": entities["contact_name"]}
            for field in ("phone", "email"):
                if entities.get(f"contact_{field}"):
                    arguments[field] = entities[f"contact_{field}"]
            return [PlanStep(f"Save {entities['contact_name']} to your contacts.", "contact_save", arguments,
                             {"type": "tool_success"}, 0)]
        whatsapp = entities.get("channel") == "whatsapp" or application == "whatsapp"
        if whatsapp and entities.get("recipient") and entities.get("message_text") and "whatsapp_send" in names:
            return [PlanStep(
                f"Send {entities['recipient']} the WhatsApp message.", "whatsapp_send",
                {"to": entities["recipient"], "message": entities["message_text"]},
                {"type": "tool_success"}, 0,
            )]
        if (entities.get("channel") == "email" and entities.get("recipient") and entities.get("message_explicit")
                and entities.get("message_text") and "email_send" in names):
            return [PlanStep(
                f"Email {entities['recipient']}.", "email_send",
                {"to": entities["recipient"], "body": entities["message_text"]},
                {"type": "tool_success"}, 0,
            )]
        if application and normalized.startswith("close") and "close_application" in self.registry.names():
            arguments = {"application": application, "save_choice": entities.get("save_choice", "ask")}
            if handle:
                arguments["handle"] = handle
            return [PlanStep("Close the intended application window and observe its final state.", "close_application", arguments, {"type": "target_window_absent", "application": application}, 0)]
        if "explain_knowledge" in self.registry.names() and any(phrase in normalized for phrase in ("why do you believe that", "what evidence supports that", "what sources support that", "which source is strongest", "when was this last verified")):
            return [PlanStep(
                "Explain the stored evidence, conflicts, confidence, and freshness behind the prior claim.",
                "explain_knowledge",
                {"query": goal, "limit": 5},
                {"type": "tool_success"},
                0,
            )]
        if "knowledge_conflicts" in self.registry.names() and any(
            phrase in normalized for phrase in (
                "knowledge conflicts", "conflicting knowledge", "contradictory knowledge",
                "stored contradictions", "what sources disagree", "which sources disagree",
            )
        ):
            return [PlanStep(
                "Show unresolved stored claim contradictions and the evidence for both sides.",
                "knowledge_conflicts", {"query": goal, "unresolved_only": True, "limit": 20},
                {"type": "tool_success"}, 0,
            )]
        # Spoken and typed requests usually contain a bare domain, not a
        # carefully formed URL. Requiring ``https://`` is why "visit
        # hamuzair.com" used to fall through to conversation.
        url_match = re.search(
            r"(?<![@\w])(?:(?:https?://)?(?:www\.)?)"
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
            r"(?:\.[a-z]{2,})+(?:/[^\s<>\]\[\)\(]*)?",
            goal, re.IGNORECASE,
        )
        entity_url = str(entities.get("url") or "").strip()
        capture_requested = bool(entities.get("web_capture")) or any(
            phrase in normalized for phrase in (
                "learn this page", "learn this website", "remember this page",
                "capture this page", "capture this website", "scrape this page",
                "scrape this website", "take a snippet", "screenshot this page",
                "visit ", "browse ", "inspect ", "analyze ", "analyse ",
                "snapshot", "scroll through", "look at ", "open ",
                "access ", "acess ", "web scrap", "scrape", "summarize",
                "summarise", "what's in it", "what is in it",
            )
        )
        document_requested = bool(url_match or entity_url) and any(
            phrase in normalized for phrase in (
                "download", "pdf", "document", "read this file", "learn this file",
                "study this file", "understand this file", "analyze this file",
                "analyse this file", "research paper", "spreadsheet", "presentation",
            )
        )
        if ("download_public_file" in self.registry.names() and (url_match or entity_url)
                and re.search(r"\b(?:download|save|get)\b", normalized)
                and not re.search(r"\b(?:learn|study|understand|analy[sz]e|extract|read)\b", normalized)):
            url = (url_match.group(0) if url_match else entity_url).rstrip(".,;:!?")
            if not re.match(r"(?i)^https?://", url):
                url = "https://" + url
            return [PlanStep("Download the file and verify the saved bytes.",
                             "download_public_file", {"url": url, "purpose": goal[:500]},
                             {"type": "download_complete"}, 0)]
        if "learn_public_document" in self.registry.names() and document_requested:
            url = (url_match.group(0) if url_match else entity_url).rstrip(".,;:!?")
            if not re.match(r"(?i)^https?://", url):
                url = "https://" + url
            return [PlanStep(
                "Download the requested public document into quarantine, deeply extract it, validate its claims, and retain bounded provenance-bearing recall chunks.",
                "learn_public_document",
                {"url": url, "topic": goal, "deep": True,
                 "remember": "do not remember" not in normalized},
                {"type": "research_complete"},
                1,
            )]
        if "capture_web_page" in self.registry.names() and (url_match or entity_url) and capture_requested:
            url = (url_match.group(0) if url_match else entity_url).rstrip(".,;:!?")
            if not re.match(r"(?i)^https?://", url):
                url = "https://" + url
            whole_page = any(
                phrase in normalized for phrase in
                ("full page", "whole page", "entire page", "whole website", "entire website")
            )
            return [PlanStep(
                "Render the requested public page, capture it, extract its content, and retain provenance for recall.",
                "capture_web_page",
                {
                    "url": url, "topic": goal,
                    "remember": "do not remember" not in normalized,
                    "screenshot": True, "full_page": whole_page,
                    "scroll_page": True, "max_scrolls": 20,
                },
                {"type": "research_complete"},
                1,
            )]
        if entities.get("modality") in {"image", "video"} and "generate_image" in self.registry.names():
            visual_style = (
                "photo" if any(word in normalized for word in ("photo", "photograph", "photorealistic", "realistic"))
                else "cinematic" if any(word in normalized for word in ("cinematic", "film still", "movie scene"))
                else "illustration" if any(word in normalized for word in ("illustration", "drawing", "storybook", "cartoon"))
                else "concept" if any(word in normalized for word in ("concept art", "game art", "environment design"))
                else "auto"
            )
            if entities.get("modality") == "video" and "generate_video" in self.registry.names():
                return [PlanStep(
                    "Generate the requested video clip locally, retain it, and record reproducibility metadata.",
                    "generate_video",
                    {"prompt": str(entities.get("prompt") or goal), "quality": media_quality(normalized),
                     "style": visual_style, "enhance": True},
                    {"type": "video_created"},
                    0,
                )]
            return [PlanStep(
                "Generate the requested image locally, retain it, and record reproducibility metadata.",
                "generate_image",
                {"prompt": str(entities.get("prompt") or goal), "quality": "ultra", "style": visual_style, "enhance": True},
                {"type": "image_created"},
                0,
            )]
        research_requested = bool(getattr(intent, "requires_research", False)) if intent is not None else ResearchManager.needs_research(goal)
        if "research_topic" in self.registry.names() and research_requested:
            level = "local_knowledge_only" if any(phrase in normalized for phrase in ("don't use the internet", "do not use the internet", "use what you've already learned", "use local knowledge only")) else "adversarial" if any(phrase in normalized for phrase in ("opposing argument", "evidence against", "adversarial research", "research the other side")) else "primary_only" if any(phrase in normalized for phrase in ("only primary sources", "use primary sources")) else "deep" if any(phrase in normalized for phrase in ("deep research", "research deeply", "research this deeply", "deeply research")) else "fast" if any(phrase in normalized for phrase in ("quick search", "quickly look", "look this up", "check quickly")) else "normal"
            return [PlanStep(
                "Research the question using bounded public-web evidence and preserve provenance.",
                "research_topic",
                {"query": goal, "level": level, "official_only": "official sources" in normalized or "only official" in normalized or level == "primary_only", "remember": "do not remember" not in normalized},
                {"type": "research_complete"},
                1,
            )]
        image_path = (getattr(intent, "entities", {}) or {}).get("image_path") if intent is not None else None
        if image_path and "describe_image" in self.registry.names():
            return [PlanStep("Show the image to the vision model and describe what is in it.", "describe_image",
                             {"path": str(image_path), "prompt": goal}, {"type": "vision_interpretation"})]
        if "describe_screen" in self.registry.names() and any(phrase in normalized for phrase in ("what is on my screen", "what's on my screen", "tell me what is on my screen", "describe my screen")):
            return [PlanStep("Capture and interpret the current Windows screen.", "describe_screen", {}, {"type": "vision_interpretation"})]
        camera_requested = bool(getattr(intent, "entities", {}).get("modality") == "camera") if intent is not None else False
        if "camera_status" in self.registry.names() and (camera_requested or any(word in normalized for word in ("camera", "webcam", "look through", "what am i showing", "read this label"))):
            if any(phrase in normalized for phrase in ("turn off", "camera off", "stop camera")):
                return [PlanStep("Release the camera and remove temporary frames.", "camera_off", {}, {"type": "tool_success"}, 0)]
            if any(phrase in normalized for phrase in ("turn on", "camera on", "activate camera")):
                return [PlanStep("Explicitly activate the configured camera.", "camera_on", {}, {"type": "tool_success"}, 0)]
            if any(phrase in normalized for phrase in ("what cameras", "list cameras", "camera devices")):
                return [PlanStep("Read Windows camera device metadata without opening a stream.", "camera_devices", {}, {"type": "tool_success"}, 0)]
            # The intent router already decided whether this is an observation
            # request; trust that rather than re-matching a shorter phrase list
            # here. "Can you access the camera and look at me?" previously matched
            # none of these phrases and silently fell through to camera_status,
            # which never opens the device or captures anything.
            observation = bool(getattr(intent, "entities", {}).get("observation")) if intent is not None else False
            if observation or any(phrase in normalized for phrase in ("look through", "what am i showing", "what is this object", "read this label", "camera see", "through the camera", "look at me", "see me", "what do you see", "what can you see")):
                return [PlanStep("Capture several temporary camera frames, compare them, analyze the clearest observations locally, and delete them.", "camera_observe", {"shots": 4, "interval_ms": 350}, {"type": "camera_observation"}, 0)]
            return [PlanStep("Read explicit camera status without opening it.", "camera_status", {}, {"type": "tool_success"}, 0)]
        if "clipboard_status" in self.registry.names() and any(phrase in normalized for phrase in ("clipboard", "what i copied", "copied image", "copy your answer")):
            if "copied image" in normalized or "analyze" in normalized:
                return [PlanStep("Analyze the copied image locally without retaining it.", "clipboard_analyze_image", {}, {"type": "tool_success"}, 0)]
            if any(phrase in normalized for phrase in ("what i copied", "read clipboard", "use the code in my clipboard", "explain what i copied")):
                return [PlanStep("Read clipboard text as ephemeral, non-persistent session data.", "clipboard_read_text", {}, {"type": "tool_success"}, 0)]
            return [PlanStep("Inspect clipboard type without reading its content.", "clipboard_status", {}, {"type": "tool_success"}, 0)]
        if "display_status" in self.registry.names() and any(phrase in normalized for phrase in ("monitor one", "monitor two", "monitor 1", "monitor 2", "second monitor", "other monitor", "which screen")):
            target = 2 if any(phrase in normalized for phrase in ("monitor two", "monitor 2", "second monitor", "other monitor")) else 1
            if "screenshot" in normalized or "look at" in normalized:
                return [PlanStep(f"Capture monitor {target}.", "display_screenshot", {"monitor": target}, {"type": "tool_success"}, 0)]
            if "move" in normalized:
                return [PlanStep(f"Move the foreground window to monitor {target} and verify its rectangle.", "move_active_window_to_display", {"monitor": target}, {"type": "tool_success"}, 0)]
            return [PlanStep("Read real monitor and foreground-window metadata.", "display_status", {}, {"type": "tool_success"}, 0)]
        if "incident_snapshot" in self.registry.names() and any(phrase in normalized for phrase in ("incident", "is down", "diagnose my service", "diagnose the service", "gpu oom", "nccl timeout")):
            scope = "gpu" if any(item in normalized for item in ("gpu", "cuda", "vram", "nccl")) else "ollama" if "ollama" in normalized else "docker" if "docker" in normalized else "kubernetes" if "kubernetes" in normalized else "system"
            return [PlanStep("Collect read-only incident evidence before proposing a change.", "incident_snapshot", {"scope": scope}, {"type": "tool_success"}, 0)]
        if re.fullmatch(r"(?:please )?(?:open|launch|bring up)(?: my| the)? ai (?:project(?: folder)?|folder)(?: please)?[.!?]?", normalized):
            return [PlanStep("Open the current AI project in File Explorer.", "open_project_folder", {}, {"type": "process_running"})]
        application_match = re.fullmatch(r"(?:please )?(?:can you )?(?:open|launch|bring up)(?: the)? (chrome|edge|vscode|notepad|calculator|file explorer)(?: please)?[.!?]?", normalized)
        if application_match:
            application = application_match.group(1).replace(" ", "_")
            return [PlanStep(f"Launch {application}.", "launch_application", {"application": application}, {"type": "application_window_exists", "application": application})]
        file_match = re.fullmatch(r"create (?:a )?(text )?file (?:called|named) (.+?) containing (.+?)[.!]?", normalized)
        if file_match:
            path = self._artifact_path(self._spoken_filename(file_match.group(2), text_file=bool(file_match.group(1))))
            content = file_match.group(3).rstrip(".")
            return [
                PlanStep("Create the requested text file.", "write_file", {"path": path, "content": content}, {"type": "file_exists", "path": path}),
                PlanStep("Read the file back to verify its contents.", "read_file", {"path": path}, {"type": "content_contains", "value": content}),
            ]
        folder_match = re.fullmatch(r"create (?:a )?folder (?:called|named) ([a-z0-9_.-]+)[.!]?", normalized)
        if folder_match:
            return [PlanStep("Create the requested folder.", "create_folder", {"path": folder_match.group(1)}, {"type": "tool_success"})]
        pdf_match = re.fullmatch(r"(?:create|make|write|save|generate) (?:a )?pdf (?:called|named) (.+?)(?: (?:with|containing|that says) (.+?))?[.!]?", normalized)
        if pdf_match:
            path = self._spoken_filename(pdf_match.group(1))
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            path = self._artifact_path(path)
            content = (pdf_match.group(2) or "").rstrip(".")
            return [
                PlanStep("Write the requested PDF document.", "write_pdf", {"path": path, "content": content}, {"type": "file_exists", "path": path}),
                PlanStep("Verify the PDF exists.", "file_exists", {"path": path}, {"type": "exists_true"}),
            ]
        word_match = re.fullmatch(r"(?:create|make|write|save|generate) (?:a )?(?:word|docx|word document|word doc) (?:called|named) (.+?)(?: (?:with|containing|that says) (.+?))?[.!]?", normalized)
        if word_match:
            path = self._spoken_filename(word_match.group(1))
            if not path.lower().endswith(".docx"):
                path += ".docx"
            path = self._artifact_path(path)
            content = (word_match.group(2) or "").rstrip(".")
            return [
                PlanStep("Write the requested Word document.", "write_docx", {"path": path, "content": content}, {"type": "file_exists", "path": path}),
                PlanStep("Verify the Word document exists.", "file_exists", {"path": path}, {"type": "exists_true"}),
            ]
        type_match = re.fullmatch(r"type:?\s+(.+)", goal.strip(), re.IGNORECASE | re.DOTALL)
        if type_match and 'computer_type' in names:
            typing = PlanStep("Type the requested text into the currently focused Windows control.",
                              "computer_type", {"text": type_match.group(1)}, {"type": "tool_success"}, 0)
            if 'computer_screenshot' in names:
                return [PlanStep('Observe the focused target before typing.', 'computer_screenshot', {}, max_retries=0),
                        typing, PlanStep('Observe the application after typing.', 'computer_screenshot', {}, max_retries=0)]
            return [typing]
        if "python_report.md" in normalized and "find all python files" in normalized:
            return [
                PlanStep("Find every Python file in the current project.", "search_files", {"path": ".", "pattern": "*.py"}, {"type": "minimum_count", "value": 1}),
                PlanStep("Write the Python file list and count to the requested report.", "write_file", {"path": "data/artifacts/python_report.md", "content": "{{python_report_from_previous}}"}, {"type": "file_exists", "path": "data/artifacts/python_report.md"}),
                PlanStep("Verify that the report exists.", "file_exists", {"path": "data/artifacts/python_report.md"}, {"type": "exists_true"}),
                PlanStep("Read the report back.", "read_file", {"path": "data/artifacts/python_report.md"}, {"type": "content_contains", "value": "Total Python files:"}),
            ]
        if "hello from my ai" in normalized and ("execute" in normalized or "prints" in normalized):
            return [
                PlanStep("Create the requested Python script.", "write_file", {"path": "data/artifacts/hello_from_ai.py", "content": "print(\"Hello from my AI\")\n"}, {"type": "file_exists", "path": "data/artifacts/hello_from_ai.py"}),
                PlanStep("Execute the Python script.", "python_execute_file", {"path": "data/artifacts/hello_from_ai.py"}, {"type": "stdout_contains", "value": "Hello from my AI"}),
            ]
        if ("what files" in normalized or "list" in normalized) and ("project" in normalized or "folder" in normalized):
            return [PlanStep("List the project folder.", "list_folder", {"path": ".", "recursive": False}, {"type": "tool_success"})]
        return None

    @staticmethod
    def _spoken_filename(value: str, text_file: bool = False) -> str:
        value = re.sub(r"\s+(?:dash|hyphen)\s+", "-", value, flags=re.IGNORECASE)
        value = re.sub(r"\s+dot\s+(?:t\s*x\s*t|txt)\b", ".txt", value, flags=re.IGNORECASE)
        value = value.strip(" .\"'").replace(" ", "-")
        value = re.sub(r"\.text$", ".txt", value, flags=re.IGNORECASE)
        value = re.sub(r"-+", "-", value)
        if text_file and "." not in value:
            value += ".txt"
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", value):
            raise ValueError("Spoken filename contains unsupported characters.")
        return value

    @staticmethod
    def _artifact_path(name: str) -> str:
        """Send a bare generated filename to data/artifacts/ so it does not

        clutter the project root -- matching where the filesystem tools store a
        bare name, so the plan's write and verify steps agree on one path."""
        if "/" in name or "\\" in name:
            return name
        return f"data/artifacts/{name}"

    def _safe_fallback(self, goal: str) -> list[PlanStep]:
        raise ValueError('No direct plan matches this request and the local planning model is unavailable. '
                         'No substitute action was executed; use an exact supported command or restore the model.')

    def _model_plan(self, goal: str, context: AgentContext | None, intent: Any | None = None, retry_empty: bool = True) -> list[PlanStep]:
        candidates = set(self._candidate_tools(goal))
        preferred_shell = getattr(intent, "entities", {}).get("preferred_shell")
        required_shell_tool = "powershell" if preferred_shell == "powershell" else "terminal" if preferred_shell == "cmd" else None
        if required_shell_tool:
            if required_shell_tool not in self.registry.names():
                raise ValueError(f"The {preferred_shell.upper()} tool is unavailable in this runtime.")
            candidates.add(required_shell_tool)
            candidates.discard("terminal" if required_shell_tool == "powershell" else "powershell")
        self.last_candidate_tools = sorted(candidates)
        metadata = {item["name"]: item for item in self.registry.describe()}
        understanding = getattr(self.registry, "understanding", None)
        tool_info = []
        for schema in self.registry.schemas():
            name = schema["function"]["name"]
            if name not in candidates:
                continue
            profile = {**schema["function"], "risk": metadata[name]["risk"],
                       "permission": metadata[name]["permission"]}
            if understanding is not None:
                record = understanding.record_for(name)
                if record:
                    profile["observed_use"] = {
                        "attempted": record["calls"] - record["refused"],
                        "failure_rate": record["failure_rate"] if record["enough_evidence"] else None,
                        "typical_ms": record["avg_ms"],
                        "enough_evidence": record["enough_evidence"],
                    }
            tool_info.append(profile)
        prompt = {
            "goal": goal,
            "interpreted_intent": intent.to_dict() if intent and hasattr(intent, "to_dict") else None,
            "context": context.as_prompt_data() if context else {},
            "researched_knowledge": self._knowledge_context(goal),
            "available_tools": tool_info,
            "rules": [UNTRUSTED_DATA_NOTICE, "Satisfy the user's final goal, not merely one tool call.", "Use only listed tools.", f"Return at most {self.max_steps} steps.", "Choose only tools whose stated purpose fits this request. Do not add tools just because they are available.", "When two tools can do the job, prefer the one with less impact and better observed reliability; too few past calls is unknown, not proof of reliability.", "Each step description must state its purpose and expected effect. Use a read-only observation before a state-changing step when it reduces uncertainty.", "Select explicit measurable postconditions and include every step required by the interpreted intent.", "Never claim success."],
            "output": {"response_type": "action", "response": None, "goal": goal, "success_criteria": ["measurable final state"], "steps": [{"description": "...", "tool": "tool_name", "arguments": {}, "expected": {"type": "tool_success"}, "max_retries": 2}]},
        }
        if any(name.startswith('computer_') for name in candidates):
            prompt['rules'].extend([
                'Prefer named application or browser operations with readback over coordinate guesses. Observe the target before desktop input and observe the application after it.',
                'Desktop input has no automatic retries: duplicate clicks, typing and shortcuts can have different effects. Re-observe after ambiguous input.',
                'For computer_click_observed, use a real fresh observation_id from tool evidence, or {{desktop_observation_id}} to resolve the preceding verified computer_observe result. Never invent tokens or substitute screenshots for verified application outcomes.',
            ])
        if required_shell_tool:
            prompt["rules"].append(
                f"The owner asked for {preferred_shell.upper()}. Include a {required_shell_tool} step to perform the task, "
                "then verify its exit code. Do not substitute another shell."
            )
        response_schema = {
            "type": "object",
            "properties": {
                "response_type": {"type": "string", "enum": ["conversation", "question", "action", "research"]},
                "response": {"type": ["string", "null"]},
                "goal": {"type": ["string", "null"]},
                "success_criteria": {"type": "array", "items": {"type": "string"}},
                "steps": {
                    "type": "array",
                    "maxItems": self.max_steps,
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "tool": {"type": "string", "enum": sorted(candidates)},
                            "arguments": {"type": "object"},
                            "expected": {
                                "type": "object",
                                "properties": {
                                    "type": {"type": "string", "enum": ["tool_success", "minimum_count", "file_exists", "exists_true", "content_contains", "stdout_contains", "exit_code_zero", "url_matches", "process_running", "application_window_exists", "target_window_focused", "ui_text_contains", "target_window_absent", "browser_destination", "download_complete", "vision_interpretation", "research_complete"]},
                                    "value": {"type": ["string", "integer", "number", "boolean"]},
                                    "path": {"type": "string"},
                                    "application": {"type": "string"},
                                },
                                "required": ["type"],
                                "additionalProperties": False,
                            },
                            "max_retries": {"type": "integer", "minimum": 0, "maximum": 3},
                        },
                        "required": ["description", "tool", "arguments", "expected", "max_retries"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["steps"],
            "additionalProperties": False,
        }
        planning_messages = [
            Message("system", "You are the planner for a permission-controlled Windows agent. Return strict JSON only."),
            Message("user", json.dumps(prompt, default=str)),
        ]
        response = self.model.generate(planning_messages, temperature=0.1, response_schema=response_schema, max_tokens=2048)
        try:
            data = parse_json_object(response.content)
        except (ValueError, json.JSONDecodeError) as exc:
            # One bounded repair attempt with the exact parse error and the
            # broken output fed back, rather than surfacing a raw
            # JSONDecodeError as the task's failure reason. Schema-constrained
            # decoding makes this rare, not impossible (e.g. the model still
            # hit its token budget mid-structure); never retry more than once.
            repair_messages = planning_messages + [
                Message("assistant", response.content),
                Message("user", json.dumps({"error": f"{type(exc).__name__}: {exc}", "instruction": "That response was not valid JSON matching the schema. Return corrected, complete, strict JSON only -- no prose, no markdown fences."}, default=str)),
            ]
            repaired = self.model.generate(repair_messages, temperature=0.0, response_schema=response_schema, max_tokens=2048)
            try:
                data = parse_json_object(repaired.content)
            except (ValueError, json.JSONDecodeError) as second_exc:
                raise ValueError(f"Planner returned malformed JSON twice; giving up rather than retrying indefinitely. Last error: {type(second_exc).__name__}: {second_exc}") from second_exc
        self.last_direct_response = str(data.get("response") or "").strip() or None
        steps = []
        raw_steps = data.get('steps', [])
        if not isinstance(raw_steps, list) or len(raw_steps) > self.max_steps:
            raise ValueError('Planner returned an invalid or oversized step list; no truncated plan will run.')
        for item in raw_steps:
            if not isinstance(item, dict):
                raise ValueError('Planner returned a step that is not an object.')
            if item.get("tool") not in candidates:
                raise ValueError(f"Planner selected an unavailable tool: {item.get('tool')}; no incomplete plan will run.")
            expected = self._strengthen_expected(goal, item["tool"], item.get("expected", {"type": "tool_success"}))
            shell_step = item["tool"] in {"terminal", "powershell"}
            if shell_step and expected.get("type") == "tool_success":
                expected = {"type": "exit_code_zero"}
            desktop_input = self.registry.get(item['tool']).permission == 'computer_input'
            retries = 0 if shell_step or desktop_input else min(3, max(0, int(item.get("max_retries", 2))))
            arguments = dict(item.get("arguments", {}))
            if item["tool"] == "terminal" and preferred_shell == "cmd":
                arguments["interpreter"] = "cmd"
            steps.append(PlanStep(item.get("description", item["tool"]), item["tool"], arguments, expected, retries))
        if not steps:
            response_type = data.get("response_type")
            action_required = getattr(intent, "is_action", True) if intent is not None else response_type not in {"conversation", "question"}
            if not action_required:
                return []
            if retry_empty:
                correction = AgentContext(goal + "\nThe previous action plan was empty. Return the required executable steps and final-state checks.", context.observation if context else {}, context.memories if context else [], context.history if context else [], context.lessons if context else [])
                return self._model_plan(goal, correction, intent, retry_empty=False)
            raise ValueError(f"Action planner returned no executable steps after correction. Response: {response.content[:1000]!r}")
        if required_shell_tool and not any(step.tool == required_shell_tool for step in steps):
            if retry_empty:
                correction = AgentContext(
                    goal + f"\nThe previous plan omitted the required {required_shell_tool} action. Include it.",
                    context.observation if context else {}, context.memories if context else [],
                    context.history if context else [], context.lessons if context else [],
                )
                return self._model_plan(goal, correction, intent, retry_empty=False)
            raise ValueError(f"The planner did not include the requested {preferred_shell.upper()} action.")
        modes = set(getattr(intent, "entities", {}).get("_modes", [])) if intent is not None else set()
        if "FAST" not in modes and intent is not None and getattr(intent, "complexity", None) in {TaskComplexity.STANDARD, TaskComplexity.COMPLEX, TaskComplexity.RESEARCH}:
            critique = self.critic.check(steps, intent)
            if not critique.approved:
                raise ValueError("Plan critic rejected the plan: " + "; ".join(critique.issues))
        return steps

    @staticmethod
    def _business_plan(normalized: str, names: list[str]) -> list[PlanStep] | None:
        """Direct plans for read-only questions about the owner's business books.

        Writes (invoices, payments, expenses) carry free-form details, so they
        go through the model planner with the business tools as candidates.
        """
        from app.tools.business import period_bounds

        text = normalized.rstrip(".!?")

        def period(raw: str | None) -> dict[str, str] | None:
            value = (raw or "").strip()
            value = re.sub(r"^(?:for|in|during)\s+", "", value)
            if not value:
                return {}
            try:
                period_bounds(value)
            except ValueError:
                return None
            return {"period": value}

        def step(purpose: str, tool: str, arguments: dict) -> list[PlanStep] | None:
            if tool not in names:
                return None
            return [PlanStep(purpose, tool, arguments, {"type": "tool_success"})]

        when = r"(?:\s+((?:for|in|during)\s+)?(this month|last month|this year|last year|all time|\d{4}(?:-\d{2})?))?"
        match = (re.fullmatch(r"(?:show|give me|check|what is|what's)?\s*(?:me\s+)?(?:my\s+|the\s+)?"
                              r"(?:business (?:report|summary)|earnings|profit(?: and loss)?|income)" + when, text)
                 or re.fullmatch(r"how much (?:money )?(?:did|have) i (?:earn|earned|make|made)" + when, text))
        if match:
            arguments = period(match.group(2))
            if arguments is not None:
                return step("Read income, expenses, profit, unpaid invoices and next actions from the business books.",
                            "business_report", arguments)
        match = re.fullmatch(r"(?:show|list|check) (?:me )?(?:my |the |all )?(?:(unpaid|overdue|paid|void) )?(invoices|quotes|estimates)", text)
        if match:
            status = "quotes" if match.group(2) != "invoices" else (match.group(1) or "all")
            return step("List invoices and balances from the business books.", "invoice_list", {"status": status})
        if re.fullmatch(r"(?:show|list) (?:me )?(?:my |the |all )?clients", text):
            return step("List clients with rates and totals invoiced.", "client_list", {})
        if re.fullmatch(r"(?:show|list|check) (?:me )?(?:my |the )?(?:lead )?follow[- ]?ups(?: due)?(?: today)?", text):
            return step("List work leads whose follow-up is due.", "lead_list", {"status": "open", "due_only": True})
        match = re.fullmatch(r"(?:show|list) (?:me )?(?:my |the |all )?(open |won |lost )?(?:work )?leads", text)
        if match:
            return step("List tracked work leads.", "lead_list", {"status": (match.group(1) or "open").strip()})
        if re.fullmatch(r"stop (?:the |my )?(?:work |billable )?timer", text):
            return step("Stop the running work timer and save the time.", "time_timer_stop", {})
        if re.fullmatch(r"(?:show|list) (?:me )?(?:my )?unbilled (?:time|hours|work)", text):
            return step("Show logged hours not yet invoiced.", "time_unbilled", {})
        match = re.fullmatch(r"(?:show|list) (?:me )?(?:my |the )?(?:business )?expenses" + when, text)
        if match:
            arguments = period(match.group(2))
            if arguments is not None:
                return step("List business expenses with totals by category.", "expense_list", arguments)
        match = re.fullmatch(r"export (?:my |the )?(?:business records|books|income and expenses)(?: for (?:taxes|tax))?(?: (?:for|in) (\d{4}))?", text)
        if match:
            return step("Export the year's income, expenses and invoices to Excel for tax preparation.",
                        "business_export", {"year": match.group(1)} if match.group(1) else {})
        match = (re.fullmatch(r"(?:show|give me|check|list)?\s*(?:me\s+)?(?:my\s+|the\s+)?(?:income streams?|sources? of "
                              r"income)" + when, text)
                 or re.fullmatch(r"where (?:does|did) my (?:money|income) come from" + when, text))
        if match:
            arguments = period(match.group(2))
            if arguments is not None:
                return step("Read every recorded income source with its share and change.", "income_streams", arguments)
        if re.fullmatch(r"(?:show|give me|what's|what is)?\s*(?:me\s+)?(?:today's\s+(?:business\s+)?|(?:my\s+|the\s+)?"
                        r"(?:daily|business|morning)\s+)brief(?:ing)?(?: for today)?", text):
            return step("Read today's business brief: what needs attention.", "business_brief", {})
        if re.fullmatch(r"show (?:me )?(?:my )?business (?:profile|details)", text):
            return step("Show the business details printed on invoices.", "business_profile", {})
        # The earnings wallet.
        if (re.fullmatch(r"(?:show|check|open|see|give)?\s*(?:me\s+)?(?:my |the )?(?:wallet|withdrawals|"
                         r"(?:wallet |available |earnings )?balance)(?: balance)?", text)
                or re.fullmatch(r"(?:what's|what is) (?:in )?(?:my|the) (?:wallet|(?:wallet |available |earnings )?balance)", text)
                or re.fullmatch(r"how much (?:money )?(?:do i have(?: in (?:my wallet|hayat))?|can i withdraw|is in my wallet)",
                                text)):
            return step("Read the earnings wallet: available balance, withdrawal method and recent activity.",
                        "wallet_balance", {})
        match = re.fullmatch(r"(?:please )?withdraw (?:\$)?(all|everything|\d+(?:\.\d{1,2})?)(?: ?(usd|eur|gbp|cad|aud|nzd)"
                             r"|(?: dollars?| euros?| pounds?))?(?: (?:from|out of) (?:my |the )?wallet)?", text)
        if match:
            arguments = {"amount": match.group(1)}
            if match.group(2):
                arguments["currency"] = match.group(2).upper()
            return step("Record a pending withdrawal to the set-up method; the owner makes the transfer.",
                        "wallet_withdraw", arguments)
        match = re.fullmatch(r"(?:mark |complete |finish )?withdrawal #?(\d+)(?: as)?(?: is)? "
                             r"(?:done|complete|completed|sent|finished)", text)
        if match:
            return step("Mark the withdrawal as transferred.", "wallet_withdrawal_done",
                        {"withdrawal_id": int(match.group(1))})
        match = re.fullmatch(r"(?:set ?up|add|change|use) (?:my |a )?(?:withdrawal|payout)s?(?: method| account)? "
                             r"(?:to|with|via|as|for) (?:my )?(bank|paypal|stripe|wise|payoneer|upi)(?: account)?"
                             r"(?:,? (?:called|named|as|:) ?(.+?))?(?:,? ending (?:in )?(\d{4}))?", text)
        if match:
            from app.tools.business import BusinessLedger

            method = match.group(1)
            name = match.group(2) or f"My {BusinessLedger.METHOD_NAMES[method]} account"
            arguments = {"method": method, "name": name}
            if match.group(3):
                arguments["last4"] = match.group(3)
            return step("Save where withdrawals go (no account numbers or passwords are stored).",
                        "wallet_payout_setup", arguments)
        launch = Planner._launch_plan(text, step)
        if launch:
            return launch
        match = re.fullmatch(r"cancel withdrawal #?(\d+)", text)
        if match:
            return step("Cancel the pending withdrawal.", "wallet_withdrawal_cancel", {"withdrawal_id": int(match.group(1))})
        return None

    @staticmethod
    def _launch_plan(text: str, step: Any) -> list[PlanStep] | None:
        """Direct plans for starting a business (app/tools/launch.py) when the request carries its own details."""
        lead = r"(?:please |can you |could you |i want to |i'd like to |let's |how (?:can|do|should) i |help me (?:to )?)?"
        match = re.fullmatch(lead + r"(?:start|launch|set up|create|open) (?:a |an |my )?(?:new )?(?:(service|product) )?"
                             r"(?:business|company|service|venture) (?:called|named) (.+?),? (?:offering|that offers|that "
                             r"sells|selling|doing|to offer|to sell) (.+?)(?: (?:for|to) (.+?))?", text)
        if match:
            kind = match.group(1) or ("product" if re.search(r"\b(?:sell|selling|sells)\b", text) else "service")
            arguments = {"name": match.group(2).strip().title(), "offer": match.group(3).strip().capitalize(),
                         "kind": kind}
            if match.group(4):
                arguments["audience"] = match.group(4).strip()
            return step("Start the business and its launch plan.", "venture_start", arguments)
        match = (re.fullmatch(lead + r"(?:start|launch|set up|begin|open|create) (?:a |an |my )?(?:new )?(?:small |online |"
                              r"side )?(?:business|service|company|side hustle|freelance(?: business)?|agency|venture)", text)
                 or re.fullmatch(lead + r"(?:make|earn) (?:some |more |extra )?money(?: (?:online|from home|with (.+)))?",
                                 text)
                 or re.fullmatch(r"(?:give me |show me |suggest |find )?(?:some )?(?:business|service|side hustle) ideas"
                                 r"(?: for (?:someone|a person|me|people)? ?(?:who|that)? ?(?:knows?|is good at|can do|"
                                 r"likes?|with skills in|with)? ?(.+))?", text)
                 or re.fullmatch(r"(?:what|which) (?:business|service|side hustle)(?:es)? (?:can|could|should) i "
                                 r"(?:start|do|run|open)(?: with (.+))?", text))
        if match:
            skills = next((group for group in match.groups() if group), "") if match.groups() else ""
            return step("Suggest businesses that fit, with how to check demand before spending anything.",
                        "business_ideas", {"skills": skills} if skills else {})
        if re.fullmatch(r"(?:show|check|what's|what is) (?:me )?(?:my |the )?(?:launch (?:plan|checklist|progress)|"
                        r"business plan)", text):
            return step("Show the launch plan, what is done and the next step.", "venture_status", {})
        if re.fullmatch(lead + r"(?:make|create|build|write|generate|update) (?:a |my |the )?(?:landing page|business "
                        r"website|(?:website|web site|one[- ]page site) for (?:my|the) (?:business|service|company))", text):
            return step("Write the one-page website from the business's offer and services.", "landing_page_create", {})
        if re.fullmatch(lead + r"(?:publish|put|upload) (?:my |the )?(?:business )?(?:website|web site|site|landing page)"
                        r"(?: online| live)?", text):
            return step("Prepare publishing the website; the owner checks and confirms before it goes online.",
                        "publish_website_prepare", {})
        if re.fullmatch(lead + r"(?:make|create|build|write|generate|update) (?:a |my |the )?marketing kit", text):
            return step("Write the marketing kit: pitch, outreach email, gig listing, posts.", "marketing_kit_create", {})
        match = re.fullmatch(r"(?:calculate|work out|figure out) (?:my |an? )?(?:hourly |day )?(?:rate|price|pricing)"
                             r"(?: for| to (?:make|earn))? \$?(\d+(?:\.\d+)?)(?: dollars| usd| euros?| eur| pounds?| gbp)? "
                             r"(?:a|per|each) month(?:,? (?:at|working|with) (\d+(?:\.\d+)?) (?:billable )?hours? "
                             r"(?:a|per|each) week)?", text)
        if match:
            arguments = {"monthly_income_goal": match.group(1),
                         "billable_hours_per_week": match.group(2) or "20"}
            return step("Work out the hourly rate that reaches the goal after tax, costs and time off.",
                        "rate_calculator", arguments)
        match = re.fullmatch(r"mark (?:the )?(.+?) step (?:as )?(?:done|complete|completed|finished)", text)
        if match:
            return step("Mark the launch step done.", "venture_step_done", {"step": match.group(1)})
        from app.tools.launch import idea_request

        skills = idea_request(text)
        if skills is not None:
            return step("Suggest businesses that fit, with how to check demand before spending anything.",
                        "business_ideas", {"skills": skills} if skills else {})
        return None

    def _candidate_tools(self, goal: str) -> list[str]:
        lowered = goal.lower()
        def mentions(*terms: str) -> bool:
            return any(re.search(rf"\b{re.escape(term)}{'s?' if ' ' not in term and not term.endswith('s') else ''}\b", lowered)
                       for term in terms)

        names = self.registry.names()
        prefixes: set[str] = set()
        explicit: set[str] = {"system_status"}
        if mentions("product", "template", "sell", "sale", "revenue", "income", "pricing", "bundle", "listing", "digital products", "make money"):
            prefixes.add("product_")
            explicit.update({"write_file", "write_pdf", "write_docx", "read_file", "file_exists", "search_files",
                             "web_search", "research_topic", "knowledge_search", "generate_image", "write_spreadsheet", "write_presentation"})
        if mentions("invoice", "quote", "estimate", "client", "customer", "prospect", "inquiry", "expense", "lead", "follow-up", "follow up", "timer",
                    "hours", "unbilled", "billable", "income goal", "goal", "profit", "earnings", "earn", "earned",
                    "business", "tax", "freelance", "gig", "make money", "side hustle", "payment", "paid"):
            prefixes.update({"business_", "client_", "customer_", "time_", "quote_", "invoice_", "expense_", "lead_", "income_", "wallet_"})
        if mentions("wallet", "withdraw", "withdrawal", "payout", "balance", "income", "earning", "paypal", "bank account"):
            prefixes.update({"wallet_", "business_"})
            explicit.update({"product_sales_report", "email_send", "contact_lookup", "read_file", "file_exists"})
        if mentions("freelance", "gig", "job", "work opportunity", "find clients", "find work", "make money", "side hustle", "lead"):
            explicit.update({"web_search", "research_topic", "knowledge_search", "public_web_fetch", "lead_add"})
        if mentions("file", "folder", "project", "read", "write", "edit", "revise", "change", "document", "report", "pdf", "word", "docx", "excel", "xlsx", "powerpoint", "pptx", "spreadsheet", "presentation", "save", "export"):
            explicit.update({"list_folder", "read_file", "write_file", "write_pdf", "write_docx", "append_file", "create_folder", "copy_file", "move_file", "search_files", "file_exists", "get_file_info"})
            explicit.update({"read_editable_file", "save_edited_file", "edit_file", "write_spreadsheet", "write_presentation", "read_document_for_edit", "read_spreadsheet_for_edit", "edit_document_text", "edit_spreadsheet_cells"})
        if mentions("download", "fetch", "save from"):
            explicit.add("download_public_file")
        if mentions("code", "project", "error", "traceback", "syntax"):
            explicit.update({"code_monitor_status", "code_scan", "code_explain_error"})
        if mentions("python", "script", "code", "execute", "run", "test"):
            prefixes.add("python_")
            explicit.update({"read_file", "write_file", "search_files", "file_exists", "terminal", "powershell"})
        if mentions("browser", "website", "web", "url", "page", "internet") or re.search(r"\b[a-z0-9-]+(?:\.[a-z]{2,})+(?:/\S*)?", lowered):
            prefixes.add("browser_")
        if mentions("connector", "integration", "account", "marketplace", "github", "notion"):
            prefixes.add("connector_")
            explicit.update({"secret_broker_status"})
        if re.search(r"(?i)\bmcp\b|model context protocol", goal):
            prefixes.add("mcp_")
        if mentions("proactive", "trigger", "event rule", "automation rule"):
            explicit.update({"proactive_rules", "add_proactive_rule"})
        if ResearchManager.needs_research(goal) or ResearchManager.knowledge_gap_likely(goal):
            # General questions need evidence tools, not every specialist's
            # registry/import/profile controls. Broad inclusion inflated even
            # local device and clipboard shortlists as the catalog grew.
            explicit.update({"web_search", "public_web_fetch", "public_api_get", "research_topic",
                             "capture_web_page", "knowledge_search", "explain_knowledge", "knowledge_conflicts"})
            if mentions('dataset', 'datasets', 'publication', 'publications', 'papers', 'doi', 'registry',
                        'indicator', 'world bank', 'literature', 'scientific'):
                explicit.update({'public_data_sources', 'public_data_search', 'public_indicator_read'})
            if mentions('person', 'people', 'professional', 'biography', 'profile', 'published work'):
                explicit.add('public_person_research')
            if mentions('repository', 'repo', 'github', 'source code'):
                explicit.add('github_research')
            if mentions('paper', 'papers', 'pdf', 'study', 'methodology', 'scientific', 'publication', 'journal', 'literature'):
                explicit.add('paper_research')
            if mentions('download', 'import', 'learn', 'study', 'document', 'pdf', 'crawl', 'website', 'read'):
                explicit.update({'public_download', 'learn_public_document', 'public_document_learning_status', 'bounded_crawl'})
        if mentions("screen", "mouse", "keyboard", "click", "windows ui", "desktop", "scroll", "hotkey", "shortcut"):
            prefixes.add("computer_")
        if mentions('intelligence', 'osiris', 'earthquake', 'wildfire', 'space weather', 'public feed', 'watch region'):
            prefixes.add('intelligence_')
        if mentions("camera", "webcam", "frame", "scene"):
            prefixes.add("camera_")
        if mentions("clipboard", "copied"):
            prefixes.add("clipboard_")
        if mentions("monitor", "display"):
            explicit.update({"display_status", "display_screenshot", "move_active_window_to_display"})
        if mentions("image", "picture", "photo", "artwork", "art", "wallpaper",
                    "illustration", "draw", "paint", "render", "portrait", "logo",
                    "video", "clip", "animation", "animate", "gif", "movie"):
            explicit.update({"generate_image", "generate_video", "image_generation_status",
                             "image_generation_history", "video_generation_history"})
        if mentions("incident", "down", "outage", "gpu oom", "nccl timeout"):
            explicit.add("incident_snapshot")
        if mentions("open", "launch", "bring up", "chrome", "edge", "notepad", "calculator", "explorer", "vscode"):
            explicit.update({"discover_applications", "launch_application", "open_project_folder"})
        if mentions("powershell", "pwsh", "terminal", "command", "shell", "cmd"):
            explicit.update({"terminal", "powershell"})
        # The capability catalog speaks the owner's words for every area, so a
        # request phrased the way a beginner would phrase it still reaches the
        # tools behind that area -- including ones no keyword above mentions.
        from app.capabilities import match, tools_in
        from app.tools.relevance import index_for

        index = index_for(self.registry)
        scores = index.scores(goal)

        for capability in match(goal, limit=2):
            if capability.key == 'growth':
                from app.revenue_catalog import relevant_tools
                explicit.update(relevant_tools(goal))
                explicit.add('revenue_tool_catalog')
            else:
                members = tools_in(capability, names)
                # Files and perception share common verbs with unrelated requests.
                # Their explicit families above preserve direct requests; use
                # lexical entry points for an incidental catalog match.
                if capability.key in {'files', 'senses'} and scores:
                    members = sorted(members, key=lambda name: (-scores.get(name, 0), name))[:8]
                explicit.update(members)
        if "my_capabilities" in names:
            explicit.add("my_capabilities")
        # Every tool's own description and area, not only the keywords above:
        # "which planes are over Dubai" must still show intelligence_aircraft.
        from app.revenue_catalog import BY_NAME as revenue_names, relevant_tools

        ranked = index.relevant(goal)
        # One family of near-identical tools (job_*, code_*) tying in the index would fill the shortlist on its own: the
        # index adds at most three of a family. The keyword and area rules above still add a whole family when asked for,
        # and revenue workflows have their own cap below.
        families: dict[str, int] = {}
        capped = []
        for name in ranked:
            if name not in revenue_names:
                family = name.split("_", 1)[0]
                families[family] = families.get(family, 0) + 1
                if families[family] > 2:
                    continue
            capped.append(name)
        ranked = capped
        # The ~170 revenue workflows stay bounded: the index may add its top three.
        # Which three: the ones the revenue catalog's own routing names for this request first, then the index's order.
        routed = relevant_tools(goal)
        revenue_ranked = sorted((name for name in ranked if name in revenue_names),
                                key=lambda name: (routed.index(name) if name in routed else len(routed), ranked.index(name)))
        fitting = {name for name in ranked if name not in revenue_names} | set(revenue_ranked[:3])
        explicit.update(fitting)
        selected = [name for name in names if name in explicit or any(name.startswith(prefix) for prefix in prefixes)]
        relevant_revenue = set(relevant_tools(goal)) | fitting
        selected = [name for name in selected if name not in revenue_names or name in relevant_revenue]
        # The named-control tools ride in with the desktop area ("where am I", "what's plugged in"); they stay only when
        # the request is about a window and what is in it.
        if not re.search(r"\b(?:click|press|tap|button|menu|tab|window|dialog|box|field|control|type\s+\S+\s+into)\b", lowered):
            selected = [name for name in selected if not name.startswith("ui_")]
        return selected or [name for name in names if name not in revenue_names]

    def _knowledge_context(self, goal: str) -> list[dict[str, Any]]:
        if self.knowledge is None:
            return []
        try:
            claims = self.knowledge.search_claims(goal, limit=5, min_confidence=0.55)
        except Exception:
            return []
        self.last_knowledge_ids = [str(item["id"]) for item in claims]
        return [{"claim": item["claim_text"][:800], "status": item["status"], "confidence": item["confidence"], "stale": item["stale"], "last_verified": item.get("last_verified")} for item in claims]

    @staticmethod
    def _strengthen_expected(goal: str, tool: str, expected: dict[str, Any]) -> dict[str, Any]:
        if tool != "read_file":
            return expected
        match = re.search(r"(?i)\b(?:describes?|contains?|includes?)\s+(?:an?\s+|the\s+)?(.+?)(?:[.!?]|$)", goal)
        if not match:
            return expected
        phrase = match.group(1).strip(" \t\r\n\"'`.,")
        phrase = re.sub(r"^(?:that\s+)?(?:it\s+)?", "", phrase, flags=re.IGNORECASE)
        phrase = re.sub(r"^(?:an?\s+|the\s+)", "", phrase, flags=re.IGNORECASE)
        return {"type": "content_contains", "value": phrase} if phrase else expected

    def replan(self, goal: str, failed_step: PlanStep, error: str, context: AgentContext) -> list[PlanStep]:
        if not self.model:
            return []
        recovery_goal = f"Recover the original goal after this failed step. Original goal: {goal}. Failed step: {failed_step.to_dict()}. Error: {error}"
        return self._model_plan(recovery_goal, context)


#: Quality tiers a video request can ask for, best first; ultra is the default and the richest finish.
#: Only unambiguous quality words — not content words like "deep ocean" or style words like "cinematic".
_VIDEO_QUALITY = (
    ("ultra", re.compile(r"(?i)\b(?:ultra|best|highest\s+quality|max(?:imum)?\s+quality|top\s+quality|4k|8k)\b")),
    ("fast", re.compile(r"(?i)\b(?:quick(?:ly)?|fast(?:est)?|speedy|rough|draft|preview|low[\s-]?(?:quality|res))\b")),
    ("quality", re.compile(r"(?i)\b(?:high[\s-]?(?:quality|res|resolution|definition)|hd)\b")),
)


def media_quality(text: str) -> str:
    """The quality tier a request asks for, from its words. Default ultra (the richest); "quick"/"fast" downgrade.

    Read from the whole request, not the drawn subject: the subject is already free of the quality word
    ("make a quick video of a sunset" draws "a sunset"), so nothing is stripped and no prompt is mangled.
    """
    for tier, pattern in _VIDEO_QUALITY:
        if pattern.search(text):
            return tier
    return "ultra"


def parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"Model response did not contain a JSON object: {text[:500]!r}")
    return json.loads(text[start : end + 1])
