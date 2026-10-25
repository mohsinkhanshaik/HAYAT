from app.agent.planner import Planner
from app.intent.router import IntentRouter
from app.safety.permissions import PermissionManager
from app.tools.filesystem import register_filesystem_tools
from app.tools.python_runner import register_python_tools
from app.tools.registry import ToolRegistry
from app.models.base import ModelProvider, ModelResponse
import json


def registry(tmp_path):
    permissions = PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path)
    result = ToolRegistry(permissions)
    register_filesystem_tools(result)
    register_python_tools(result)
    return result


def test_python_report_plan(tmp_path):
    plan = Planner(registry(tmp_path)).create_plan("Find all Python files. Count them. Create data/artifacts/python_report.md")
    assert [step.tool for step in plan] == ["search_files", "write_file", "file_exists", "read_file"]


def test_hello_plan(tmp_path):
    plan = Planner(registry(tmp_path)).create_plan("Create a Python script that prints Hello from my AI and execute it")
    assert [step.tool for step in plan] == ["write_file", "python_execute_file"]


class PlanningModel(ModelProvider):
    name = "planning-fake"

    def list_models(self):
        return ["planning-fake"]

    def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None):
        assert "input_schema" not in messages[-1].content
        assert '"parameters"' in messages[-1].content
        assert response_schema["properties"]["steps"]["type"] == "array"
        expected_schema = response_schema["properties"]["steps"]["items"]["properties"]["expected"]
        assert "content_contains" in expected_schema["properties"]["type"]["enum"]
        return ModelResponse('{"steps":[{"description":"Read README","tool":"read_file","arguments":{"path":"README.md"},"expected":{"type":"content_contains","value":"AI"}}]}', "planning-fake", self.name)


def test_model_planner_receives_tool_schemas(tmp_path):
    plan = Planner(registry(tmp_path), PlanningModel()).create_plan("Read the README title")
    assert plan[0].tool == "read_file"
    assert plan[0].arguments == {"path": "README.md"}


def test_candidate_tools_reduce_irrelevant_context(tmp_path):
    planner = Planner(registry(tmp_path))
    candidates = planner._candidate_tools("Read README.md")
    assert "read_file" in candidates
    assert "python_execute_code" not in candidates


def test_tool_candidates_match_whole_words_not_substrings(tmp_path):
    tools = registry(tmp_path)
    empty_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    tools.register("generate_image", "Create an image", empty_schema, "medium", "media_generation", lambda: {})
    tools.register("launch_application", "Open an application", empty_schema, "medium", "computer_control", lambda: {})
    tools.register("system_status", "Read system state", empty_schema, "low", "system_read", lambda: {})
    candidates = Planner(tools)._candidate_tools("Start Notepad")
    assert "launch_application" in candidates
    assert "generate_image" not in candidates
    assert "read_file" in Planner(tools)._candidate_tools("Review files in the project")


def test_model_planner_sees_purpose_impact_and_observed_tool_history(tmp_path):
    tools = registry(tmp_path)

    class Understanding:
        def record_for(self, name):
            return {"calls": 7, "refused": 1, "failure_rate": .167,
                    "avg_ms": 25, "enough_evidence": True} if name == "read_file" else {}

    class CapturingModel(ModelProvider):
        name = "capture"

        def list_models(self):
            return ["capture"]

        def generate(self, messages, **kwargs):
            prompt = json.loads(messages[-1].content)
            selected = next(item for item in prompt["available_tools"] if item["name"] == "read_file")
            assert selected["risk"] == "low"
            assert selected["observed_use"] == {
                "attempted": 6, "failure_rate": .167,
                "typical_ms": 25, "enough_evidence": True,
            }
            assert any("less impact" in rule for rule in prompt["rules"])
            return ModelResponse('{"steps":[{"description":"Read the requested file",'
                                 '"tool":"read_file","arguments":{"path":"README.md"},'
                                 '"expected":{"type":"tool_success"},"max_retries":0}]}',
                                 "capture", "capture")

    tools.understanding = Understanding()
    plan = Planner(tools, CapturingModel()).create_plan("Read README.md")
    assert plan[0].tool == "read_file"


def test_content_goal_strengthens_weak_model_verification(tmp_path):
    planner = Planner(registry(tmp_path))
    expected = planner._strengthen_expected(
        "Read README.md and verify that it describes a personal Windows agent.",
        "read_file",
        {"type": "file_exists"},
    )
    assert expected == {"type": "content_contains", "value": "personal Windows agent"}


def test_explicit_webpage_learning_routes_to_rendered_capture(tmp_path):
    tools = registry(tmp_path)
    tools.register(
        "capture_web_page", "capture", {"type": "object"}, "low",
        "knowledge_research", lambda **kwargs: kwargs,
    )
    plan = Planner(tools).create_plan(
        "Learn this page and remember it https://example.com/guide"
    )
    assert [step.tool for step in plan] == ["capture_web_page"]
    assert plan[0].arguments["url"] == "https://example.com/guide"
    assert plan[0].arguments["remember"] is True


def test_public_document_learning_routes_to_deep_quarantine_pipeline(tmp_path):
    tools = registry(tmp_path)
    tools.register(
        "learn_public_document", "learn document", {"type": "object"}, "medium",
        "public_download", lambda **kwargs: kwargs,
    )
    plan = Planner(tools).create_plan(
        "Download and deeply learn this PDF https://example.com/manual.pdf"
    )
    assert [step.tool for step in plan] == ["learn_public_document"]
    assert plan[0].arguments["url"] == "https://example.com/manual.pdf"
    assert plan[0].arguments["deep"] is True
    assert plan[0].arguments["remember"] is True


def test_bare_domain_visit_routes_to_scrolled_full_page_capture(tmp_path):
    tools = registry(tmp_path)
    tools.register(
        "capture_web_page", "capture", {"type": "object"}, "low",
        "knowledge_research", lambda **kwargs: kwargs,
    )
    plan = Planner(tools).create_plan(
        "Visit hamuzair.com, scroll through the whole website, analyze it and remember it"
    )
    assert [step.tool for step in plan] == ["capture_web_page"]
    assert plan[0].arguments == {
        "url": "https://hamuzair.com", "topic": "Visit hamuzair.com, scroll through the whole website, analyze it and remember it",
        "remember": True, "screenshot": True, "full_page": True,
        "scroll_page": True, "max_scrolls": 20,
    }


def test_reported_mohsinkhanshaik_phrasings_always_plan_a_capture(tmp_path):
    tools = registry(tmp_path)
    tools.register(
        "capture_web_page", "capture", {"type": "object"}, "low",
        "knowledge_research", lambda **kwargs: kwargs,
    )
    for prompt in (
        "GREAT AND NOW TRY ACESS TO MOHSINKHANSHAIK.COM AND WEB SCRAP",
        "MOHSINKHANSHAIK.COM SUMMARIZE IT",
        "https://mohsinkhanshaik.com/ BROWSE IT",
    ):
        plan = Planner(tools).create_plan(prompt, intent=IntentRouter().route(prompt))
        assert [step.tool for step in plan] == ["capture_web_page"]
        step_url = plan[0].arguments["url"]
        assert step_url
        assert "mohsinkhanshaik.com" in step_url.lower()
        assert plan[0].expected["type"] == "research_complete"


def test_stored_contradiction_request_routes_to_conflict_report(tmp_path):
    tools = registry(tmp_path)
    tools.register(
        "knowledge_conflicts", "conflicts", {"type": "object"}, "low",
        "filesystem_read", lambda **kwargs: kwargs,
    )
    plan = Planner(tools).create_plan("Show knowledge conflicts about Widget safe mode")
    assert [step.tool for step in plan] == ["knowledge_conflicts"]
    assert plan[0].arguments["unresolved_only"] is True


def test_make_a_pdf_plan(tmp_path):
    plan = Planner(registry(tmp_path)).create_plan("make a pdf called plan with step one and step two")
    assert [step.tool for step in plan] == ["write_pdf", "file_exists"]
    # Bare name lands in the artifacts folder, and every step agrees on that one
    # path so the verify step does not look where the write did not land.
    assert plan[0].arguments["path"] == "data/artifacts/plan.pdf"
    assert plan[1].arguments["path"] == "data/artifacts/plan.pdf"
    assert "step one" in plan[0].arguments["content"]


def test_make_a_word_doc_plan(tmp_path):
    plan = Planner(registry(tmp_path)).create_plan("make a word document called notes with alpha and beta")
    assert [step.tool for step in plan] == ["write_docx", "file_exists"]
    assert plan[0].arguments["path"] == "data/artifacts/notes.docx"
    assert plan[1].arguments["path"] == "data/artifacts/notes.docx"
