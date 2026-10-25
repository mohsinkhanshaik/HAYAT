"""Starting a business or service: ideas, launch plan, services, pricing, website, marketing kit."""
import pytest

from app.agent.planner import Planner
from app.intent.router import IntentRouter, _business_command
from app.safety.permissions import PermissionManager
from app.tools.business import BusinessLedger, register_business_tools
from app.tools.launch import IDEAS, Launchpad, register_launch_tools
from app.tools.products import ProductStore
from app.tools.registry import ToolRegistry


@pytest.fixture
def pad(tmp_path):
    permissions = PermissionManager({"default": "ALLOW"}, tmp_path)
    products = ProductStore(tmp_path / "products.db", tmp_path / "bundles", permissions)
    ledger = BusinessLedger(tmp_path / "business.db", tmp_path / "business", permissions, products=products)
    launchpad = Launchpad(ledger, products)
    registry = ToolRegistry(permissions)
    register_business_tools(registry, ledger)
    register_launch_tools(registry, launchpad)
    return launchpad, ledger, registry


def test_ideas_match_skills_and_promise_nothing(pad):
    launchpad, _, _ = pad
    result = launchpad.ideas("Arabic, graphic design", hours_per_week=4, budget="almost nothing")
    titles = [idea["title"] for idea in result["ideas"]]
    assert "Graphic design" in titles[:3] and "Translation and language lessons" in titles[:3]
    assert any("not promises" in note for note in result["notes"])
    assert any("almost no money" in note for note in result["notes"]) and any("4 hours" in note for note in result["notes"])
    for idea in result["ideas"]:
        assert idea["check_demand"] and idea["first_clients"] and idea["first_offer"]
    # No income figures are recited from memory.
    for idea in IDEAS:
        assert not any(sign in idea.pitch + idea.first_offer for sign in ("$", "€", "£", "per hour"))


def test_launch_plan_ticks_itself_off_from_the_books(pad):
    launchpad, ledger, _ = pad
    started = launchpad.start("Mohsin Designs", "Logos and brand kits", "new small businesses", idea="design")
    assert started["progress"] == "1 of 12 steps done" and "Check demand" in started["next_step"]
    assert ledger.profile()["business_name"] == "Mohsin Designs"
    launchpad.add_service("Logo design", "150", "A custom logo", "fixed", 5, "3 concepts; 2 revisions")
    ledger.set_profile(email="me@example.com", payment_instructions="PayPal")
    for index in range(10):
        lead = ledger.add_lead(f"Shop {index}", "Instagram")
        if index < 3:
            ledger.update_lead(lead["lead_id"], status="contacted")
    ledger.add_client("Acme")
    launchpad.quote_from_service("Acme", "Logo design")
    ledger.create_invoice("Acme", [{"description": "Logo", "unit_price": "150"}])
    ledger.set_payout("paypal", "me@example.com")
    launchpad.website()
    launchpad.marketing_kit()
    status = launchpad.status()
    done = {step["step"] for step in status["steps"] if step["done"]}
    assert done == {"offer", "price", "profile", "payment", "website", "marketing", "leads", "outreach",
                    "first_invoice", "withdrawal"}
    assert {step["ticked_by"] for step in status["steps"] if step["done"]} == {"HAYAT saw it in your books"}
    launchpad.step_done("research")
    launchpad.step_done("Check local rules")
    assert launchpad.status()["progress"] == "12 of 12 steps done"
    assert "Launched" in launchpad.status()["next_step"]
    launchpad.step_done("legal", undo=True)
    assert launchpad.status()["done"] == 11


def test_product_businesses_get_their_own_plan(pad):
    launchpad, _, _ = pad
    status = launchpad.start("Planner Shop", "Printable weekly planners", "busy parents", kind="product")
    assert [step["step"] for step in status["steps"]][:4] == ["offer", "research", "product", "package"]
    assert launchpad.products.create("Planner", "parents", "A planner", "5", "Personal use")
    assert any(step["step"] == "product" and step["done"] for step in launchpad.status()["steps"])


def test_services_quotes_and_validation(pad):
    launchpad, ledger, _ = pad
    with pytest.raises(ValueError, match="No business started"):
        launchpad.add_service("Logo", "100")
    launchpad.start("Studio", "Logos")
    with pytest.raises(ValueError, match="already have"):
        launchpad.start("studio", "Again")
    with pytest.raises(ValueError):
        launchpad.add_service("Logo", "0")
    with pytest.raises(ValueError, match="Unit"):
        launchpad.add_service("Logo", "100", unit="forever")
    launchpad.add_service("Logo", "100", unit="fixed")
    launchpad.add_service("Logo", "120", unit="fixed")  # updating keeps one service
    services = launchpad.services()["services"]
    assert len(services) == 1 and services[0]["price"] == "120.00 USD per project"
    ledger.add_client("Acme")
    quote = launchpad.quote_from_service("Acme", "logo", quantity=2)
    assert quote["number"].startswith("Q-") and quote["total"] == "240.00 USD"
    with pytest.raises(ValueError, match="No service"):
        launchpad.quote_from_service("Acme", "Website")


def test_rate_calculator_is_plain_arithmetic():
    result = Launchpad.rate(2000, 20, 100, 20)
    # (2000 + 100) / 0.8 = 2625 a month over 20 h x 48 weeks / 12 = 80 h -> 32.81 an hour.
    assert result["hourly_rate"] == "32.81 USD" and result["day_rate_8h"] == "262.48 USD"
    assert result["revenue_needed_per_month"] == "2,625.00 USD"
    assert "research" in result["check"]
    with pytest.raises(ValueError):
        Launchpad.rate(2000, 0)


def test_website_and_marketing_kit_are_files_not_publications(pad, tmp_path):
    launchpad, ledger, _ = pad
    launchpad.start("Mohsin <Designs>", "Logos & brand kits", "shops")
    launchpad.add_service("Logo", "150", includes=["3 concepts"])
    ledger.set_profile(email="me@example.com")
    site = launchpad.website()
    page = open(site["path"], encoding="utf-8").read()
    assert site["published"] is False and "GitHub Pages" in site["next_step"]
    assert "Mohsin &lt;Designs&gt;" in page and "<script" not in page and "mailto:me@example.com" in page
    assert "150.00 USD per project" in page and 'name="viewport"' in page
    kit = launchpad.marketing_kit()
    text = open(kit["path"], encoding="utf-8").read()
    for section in ("One-line pitch", "Outreach email", "Follow-up", "Social posts", "Price list"):
        assert section in text
    assert "Edit every piece" in text


def test_tools_are_registered_and_permissioned(pad, tmp_path):
    _, _, registry = pad
    assert registry.execute("business_ideas", {"skills": "excel"}).success
    assert registry.execute("venture_start", {"name": "Books", "offer": "Bookkeeping"}).success
    assert registry.execute("rate_calculator", {"monthly_income_goal": 1500, "billable_hours_per_week": 15}).success
    denied = ToolRegistry(PermissionManager({"default": "DENY"}, tmp_path))
    register_launch_tools(denied, pad[0])
    assert not denied.execute("venture_start", {"name": "X", "offer": "Y"}).success


@pytest.mark.parametrize("text, tool, arguments", [
    ("help me start a business", "business_ideas", {}),
    ("how can I start a business?", "business_ideas", {}),
    ("help me make money", "business_ideas", {}),
    ("business ideas for someone who knows arabic and design", "business_ideas", {"skills": "arabic and design"}),
    ("start a business called mohsin designs offering logos and brand kits for small businesses", "venture_start",
     {"name": "Mohsin Designs", "offer": "Logos and brand kits", "kind": "service", "audience": "small businesses"}),
    ("show my launch plan", "venture_status", {}),
    ("make a website for my business", "landing_page_create", {}),
    ("make my marketing kit", "marketing_kit_create", {}),
    ("calculate my hourly rate for 2000 dollars a month at 20 hours a week", "rate_calculator",
     {"monthly_income_goal": "2000", "billable_hours_per_week": "20"}),
    ("mark the research step done", "venture_step_done", {"step": "research"}),
])
def test_requests_reach_the_launch_tools(pad, text, tool, arguments):
    _, _, registry = pad
    assert IntentRouter().route(text).requires_tools
    plan = Planner(registry).create_plan(text)
    assert (plan[0].tool, plan[0].arguments) == (tool, arguments)


@pytest.mark.parametrize("text", ["open the website", "what is a business plan", "make a website in html",
                                  "start chrome"])
def test_other_requests_are_left_alone(text):
    assert not _business_command(text)


@pytest.mark.parametrize("text, skills", [
    ("what type of business can u make", ""),  # the owner's own words, which got "I don't build businesses myself"
    ("can u make a business", ""), ("what can I sell", ""), ("give me a business idea", ""),
    ("build me a business", ""), ("how can I earn extra money from home", ""), ("help me with a business idea", ""),
    ("ways to make money with excel", "excel"), ("what side hustle can I start, I speak arabic", "arabic"),
])
def test_every_wording_of_which_business_reaches_the_ideas_tool(pad, text, skills):
    from app.tools.launch import idea_request

    _, _, registry = pad
    assert idea_request(text) == skills
    intent = IntentRouter().route(text)
    assert intent.requires_tools
    plan = Planner(registry).create_plan(intent.goal or text, intent=intent)
    assert plan[0].tool == "business_ideas" and plan[0].arguments == ({"skills": skills} if skills else {})


@pytest.mark.parametrize("text", ["what is a business", "what are your business hours", "what is passive income?",
                                  "send a business email", "how much money do I have", "make a website for my business"])
def test_definitions_and_other_business_words_are_not_idea_requests(text):
    from app.tools.launch import idea_request

    assert idea_request(text) is None


def test_ideas_and_plans_read_as_plain_replies(pad):
    from app.observed_reply import render

    launchpad, _, _ = pad
    text = render(launchpad.ideas())
    assert text.startswith("Here are businesses one person can start") and "Pick one and say" in text
    assert "matched to what you told me" in render(launchpad.ideas("excel"))
    launchpad.start("Studio", "Logos")
    plan = render(launchpad.status())
    assert plan.startswith("Studio: 1 of 12 steps done.") and "○ Set your price" in plan and "Next:" in plan
    assert "32.81 USD an hour" in render(Launchpad.rate(2000, 20, 100, 20))


def test_the_conversation_model_knows_it_can_start_a_business():
    from app.agent.conversation import _capability_areas, _capability_primer
    from app.capabilities import CATALOG

    areas, primer = _capability_areas(), _capability_primer()
    assert all(capability.title in areas for capability in CATALOG)
    assert "Never say you cannot start" in primer and "Help me start a business" in primer
