"""Start a business or a service: from an idea to the first invoice.

This sits on top of the business books (app/tools/business.py). It helps
choose an idea that fits the owner's skills, turns it into a business with a
launch plan, defines the services and their prices, works out a sustainable
rate, writes a one-page website and a marketing kit, and ticks launch steps
off by itself as the books show them done (a service priced, ten leads
found, the first invoice sent, a withdrawal method set up).

What it does not do: it predicts no income, quotes no market rates from
memory, registers no company and publishes nothing. Market rates and demand
are checked with the research tools; legal registration and tax are the
owner's to check locally; the website is a file until the owner hosts it.
"""
from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from app.logging import log_swallowed
from app.tools.business import BusinessLedger, _currency, _decimal, money
from app.tools.products import cents
from app.tools.registry import ToolRegistry

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Idea:
    key: str
    title: str
    kind: str  # service | product
    pitch: str
    skills: tuple[str, ...]
    startup: str
    first_clients: str
    first_offer: str
    hayat_helps: str


#: Low-cost businesses one person can start. No earnings figures on purpose:
#: rates and demand change by place and year, so they are researched, not recited.
IDEAS: tuple[Idea, ...] = (
    Idea("writing", "Freelance writing and editing", "service",
         "Write or polish blog posts, website text, product descriptions and newsletters for small businesses.",
         ("writing", "english", "write", "blog", "editing", "content", "copywriting", "reading"),
         "A laptop and an internet connection.", "Small local businesses, online shops, Upwork and Fiverr.",
         "Five product descriptions or one 800-word blog post, delivered in 3 days.",
         "drafting, research for articles, proofreading, invoices"),
    Idea("design", "Graphic design", "service",
         "Design logos, social media graphics, flyers and simple brand kits.",
         ("design", "graphic", "art", "drawing", "canva", "photoshop", "logo", "creative", "illustration"),
         "A laptop; free tools like Canva or GIMP to begin with.", "New businesses, shops, events, Fiverr, Instagram.",
         "A logo with two revisions, or ten social media post templates.",
         "image generation for ideas and mock-ups, client tracking, quotes"),
    Idea("social", "Social media management", "service",
         "Plan, create and schedule posts for small businesses that have no time for it.",
         ("social", "instagram", "facebook", "tiktok", "marketing", "content", "posting", "community"),
         "A phone or laptop.", "Cafés, salons, gyms, clinics and shops near you.",
         "A month of 12 posts with captions and a simple monthly report.",
         "post writing, images, scheduling reminders, monthly invoices"),
    Idea("webdesign", "Simple websites for small businesses", "service",
         "Build one-page websites and keep them updated for businesses that only have a social page.",
         ("website", "web", "html", "coding", "wordpress", "programming", "developer", "computer"),
         "A laptop; free hosting such as GitHub Pages to start.", "Local businesses without a website, Upwork.",
         "A one-page website with contact form, delivered in 5 days.",
         "writing the code, page text, screenshots of examples, quotes and invoices"),
    Idea("tutoring", "Online tutoring", "service",
         "Teach a subject or skill one to one over video calls.",
         ("teaching", "tutor", "math", "maths", "science", "english", "teach", "school", "exam", "physics"),
         "A laptop with a camera.", "Parents in local groups, students, tutoring platforms.",
         "A one-hour lesson, or a package of 8 lessons.",
         "lesson plans, practice questions, scheduling, invoices"),
    Idea("language", "Translation and language lessons", "service",
         "Translate documents or teach a language you speak well.",
         ("arabic", "urdu", "hindi", "french", "spanish", "translation", "translate", "language", "bilingual"),
         "A laptop.", "Families abroad, small exporters, ProZ, Upwork, community groups.",
         "Translation of a 1,000-word document, or a 4-lesson starter course.",
         "first-draft translation, lesson material, pronunciation practice, invoices"),
    Idea("va", "Virtual assistant", "service",
         "Handle email, calendars, data entry, research and admin for busy people remotely.",
         ("organised", "organized", "admin", "email", "excel", "spreadsheet", "data", "assistant", "typing"),
         "A laptop and an internet connection.", "Coaches, consultants, small agencies, LinkedIn, Upwork.",
         "10 hours of admin support per month.",
         "research, spreadsheets, document drafting, time tracking, monthly invoices"),
    Idea("video", "Video editing", "service",
         "Edit YouTube videos, reels and short clips for creators and businesses.",
         ("video", "editing", "youtube", "premiere", "capcut", "film", "reels", "camera"),
         "A computer that can edit video; free editors to start.", "YouTubers, coaches, local businesses.",
         "Editing one 10-minute video with captions, delivered in 3 days.",
         "video scene analysis, captions, thumbnails, quotes and invoices"),
    Idea("bookkeeping", "Bookkeeping for small businesses", "service",
         "Keep the books, send invoices and prepare monthly summaries for sole traders.",
         ("accounting", "bookkeeping", "numbers", "excel", "finance", "tax", "math", "spreadsheet"),
         "A laptop; the business books in HAYAT to start.", "Sole traders, shops, freelancers.",
         "Monthly bookkeeping for up to 50 transactions.",
         "the business books, Excel exports, invoices, reminders"),
    Idea("tech", "Computer help and IT setup", "service",
         "Set up computers, Wi-Fi, printers and backups for homes and small offices.",
         ("computer", "tech", "it", "windows", "repair", "network", "wifi", "hardware", "software"),
         "Your own skills and a phone.", "Neighbours, elderly people, small offices, local groups.",
         "A one-hour home visit or remote session.",
         "troubleshooting guides, scheduling, invoices"),
    Idea("resume", "CV and cover letter writing", "service",
         "Write and polish CVs, cover letters and LinkedIn profiles.",
         ("writing", "cv", "resume", "career", "hr", "linkedin", "english", "editing"),
         "A laptop.", "Graduates, job seekers, Facebook groups, Fiverr.",
         "A rewritten CV plus one cover letter in 2 days.",
         "drafting, PDFs and Word files, research on the target job, invoices"),
    Idea("data", "Data entry and spreadsheet work", "service",
         "Clean up lists, build spreadsheets and dashboards, and type up documents.",
         ("excel", "spreadsheet", "data", "typing", "organised", "organized", "sheets", "detail"),
         "A laptop.", "Small businesses, researchers, Upwork.",
         "Cleaning and formatting a list of up to 1,000 rows.",
         "Python scripts, Excel files, checking work, invoices"),
    Idea("digital", "Digital products", "product",
         "Make something once and sell it many times: templates, planners, guides, printables.",
         ("design", "writing", "templates", "canva", "planner", "notion", "excel", "ebook", "creative"),
         "A laptop; a free store account such as Gumroad or Etsy (fees apply).",
         "Etsy, Gumroad, your own social media.",
         "A printable weekly planner or a budget spreadsheet template.",
         "the product tools: draft, package, record sales; images; descriptions"),
    Idea("photo", "Photography", "service",
         "Photograph products, events, portraits or property.",
         ("photography", "photo", "camera", "pictures", "editing", "lightroom", "events"),
         "A camera or a good phone.", "Online shops, estate agents, families, local events.",
         "Photos of 10 products on a clean background, delivered in 2 days.",
         "editing ideas, gallery, client tracking, quotes"),
    Idea("crafts", "Handmade goods", "product",
         "Make and sell crafts, food, art or clothing.",
         ("crafts", "handmade", "sewing", "baking", "cooking", "art", "painting", "jewellery", "jewelry"),
         "Materials for your first batch.", "Markets, Instagram, Etsy, friends and family.",
         "A small batch of your best-selling item.",
         "product photos, descriptions, costs and sales records"),
    Idea("coaching", "Coaching or consulting", "service",
         "Sell advice in an area you know well: fitness, careers, business, study skills.",
         ("coaching", "fitness", "career", "consulting", "advice", "experience", "expert", "business"),
         "Your experience and a video-call app.", "LinkedIn, your network, communities in your field.",
         "A 60-minute strategy session with a written action plan.",
         "session notes, action plans, scheduling, invoices"),
)

IDEA_BY_KEY = {idea.key: idea for idea in IDEAS}

_IDEA_TOPIC = re.compile(
    r"(?i)\b(?:business(?:es)?|side[\s-]?hustles?|start-?ups?|ventures?|small company|"
    r"(?:make|earn|making|earning)\s+(?:some\s+|more\s+|extra\s+)?money|income\s+streams?|passive\s+income|"
    # "start earning" was taken for an application called Earning (2026-10-05).
    r"(?:start|begin)\s+earning|(?:want|need)\s+to\s+(?:start\s+)?earn(?:ing)?(?=\s*[.!?]*$)|"
    r"(?:can|could|should)\s+(?:i|we)\s+sell|something\s+to\s+sell)\b")
_IDEA_ASK = re.compile(
    r"(?i)\b(?:what|which|kinds?|types?|sorts?|ideas?|suggest|recommend|help|ways?|options?|good\s+for\s+me|"
    r"(?:can|could|should)\s+(?:you|u|i|we)|start|make|build|create|open|launch|begin|set\s+up|give\s+me|"
    r"show\s+me|tell\s+me|want|need|how)\b")
#: Not a request for ideas: a definition, or the owner's existing books and details.
_IDEA_NOT = re.compile(
    r"(?i)^(?:what|who)\s+(?:is|are|was|does|do)\s+(?:a\s+|an\s+|the\s+)?(?:business|start-?up|side\s+hustle|venture|"
    r"passive\s+income|income\s+streams?)\b"
    r"|\b(?:website|web\s+site|landing\s+page|marketing\s+kit|logo)\b"
    r"|\bbusiness\s+(?:hours|email|emails|card|cards|report|profile|details|records|expenses?|class|classes|school|"
    r"news|meeting|trip|account|loan|insurance|law|english|studies)\b"
    r"|\b(?:my|the)\s+business\s+(?:website|site|plan)\b")
_IDEA_SKILLS = re.compile(
    r"(?i)\b(?:with\s+(?:my\s+)?(?:skills?\s+in\s+)?|for\s+(?:someone|a\s+person|people|me)\s+(?:who|that)\s+"
    r"(?:knows?|is\s+good\s+at|can\s+do|likes?|loves?)\s+|i\s+(?:know|am\s+good\s+at|can\s+do|like|love|speak)\s+|"
    r"using\s+(?:my\s+)?|based\s+on\s+(?:my\s+)?)(.+?)[?.!]*$")


def idea_request(text: str) -> str | None:
    """Whether the owner is asking which business to start; returns the skills mentioned ('' if none), or None.

    "What type of business can u make", "what can I sell", "ways to make money with Excel", "give me a business
    idea": all mean "suggest businesses I could start", however they are worded.
    """
    value = " ".join((text or "").split())
    from app.business_acquisition import acquisition_request
    if acquisition_request(value):
        return None
    if not value or _IDEA_NOT.search(value) or not _IDEA_TOPIC.search(value) or not _IDEA_ASK.search(value):
        return None
    found = _IDEA_SKILLS.search(value)
    skills = found.group(1).strip() if found else ""
    # "help me with a business idea" names no skill.
    if re.search(r"(?i)\b(?:business|idea|ideas|money|side\s+hustle)\b", skills):
        skills = ""
    return skills

#: The launch plan. ``auto`` names a check against the books; the rest are ticked by the owner.
SERVICE_STEPS = (
    ("offer", "Describe your offer in one sentence", "What you do, for whom, and the result they get.", "offer"),
    ("research", "Check demand and what others charge", "Ask HAYAT: \"Research {offer} rates for {audience}\".", ""),
    ("price", "Set your price", "Use the price calculator, then add your services with prices.", "service"),
    ("profile", "Fill in your business details", "Business name and email, used on quotes, invoices and the website.",
     "profile"),
    ("payment", "Decide how clients pay you", "Bank transfer, PayPal, or similar; it goes on every invoice.", "payment"),
    ("website", "Make your one-page website", "HAYAT writes it from your services; you can host it for free.",
     "website"),
    ("marketing", "Make your marketing kit", "A pitch, an outreach email, a gig listing and posts, ready to edit.",
     "marketing"),
    ("leads", "Find your first 10 potential clients", "Ask HAYAT to research where your clients are, then add them "
     "as leads.", "leads"),
    ("outreach", "Contact them", "Send your outreach message and mark each lead as contacted.", "outreach"),
    ("first_invoice", "Send your first invoice", "Quote from a service, then invoice when they say yes.", "invoice"),
    ("withdrawal", "Set up withdrawals", "Choose where your earnings go in the Wallet.", "payout"),
    ("legal", "Check local rules", "Find out whether you must register the business and how to pay tax where you "
     "live. HAYAT can research it; the decision is yours.", ""),
)
PRODUCT_STEPS = (
    ("offer", "Describe your product in one sentence", "What it is, who it is for, and what it helps them do.",
     "offer"),
    ("research", "Check demand and what similar products sell for", "Ask HAYAT: \"Research {offer} for {audience}\".",
     ""),
    ("product", "Make your product and add it", "Create the files, then add the product with its price and licence.",
     "product"),
    ("package", "Package it for delivery", "HAYAT builds a checked ZIP with your licence and a listing.", ""),
    ("profile", "Fill in your business details", "Business name and email for your store and website.", "profile"),
    ("website", "Make your one-page website", "HAYAT writes it; link it from your store and social pages.", "website"),
    ("marketing", "Make your marketing kit", "A pitch, a store listing and posts, ready to edit.", "marketing"),
    ("store", "List it on a store", "Etsy, Gumroad or similar. Publishing and payments happen in your account.", ""),
    ("first_sale", "Record your first sale", "Tell HAYAT the order reference so it counts in your wallet.", "sale"),
    ("withdrawal", "Set up withdrawals", "Choose where your earnings go in the Wallet.", "payout"),
    ("legal", "Check local rules", "Selling may need registration and tax where you live. HAYAT can research it.", ""),
)

UNITS = {"fixed": "per project", "hour": "per hour", "month": "per month", "session": "per session",
         "item": "per item", "word": "per word", "page": "per page"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "business"


class Launchpad:
    """Ventures, their services and launch plans, kept in the business books' database."""

    def __init__(self, ledger: BusinessLedger, products: Any = None):
        self.ledger, self.products = ledger, products
        self.ledger._run(lambda db: db.executescript("""
            CREATE TABLE IF NOT EXISTS ventures (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, kind TEXT NOT NULL,
                offer TEXT NOT NULL, audience TEXT NOT NULL, idea TEXT NOT NULL DEFAULT '',
                website TEXT NOT NULL DEFAULT '', marketing TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY, venture_id INTEGER NOT NULL REFERENCES ventures(id), name TEXT NOT NULL,
                description TEXT NOT NULL, price_cents INTEGER NOT NULL, currency TEXT NOT NULL, unit TEXT NOT NULL,
                delivery_days INTEGER NOT NULL DEFAULT 0, includes TEXT NOT NULL DEFAULT '[]',
                created TEXT NOT NULL, UNIQUE (venture_id, name));
            CREATE TABLE IF NOT EXISTS published_sites (
                venture_id INTEGER PRIMARY KEY REFERENCES ventures(id), provider TEXT NOT NULL, site_id TEXT NOT NULL,
                url TEXT NOT NULL, published_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS launch_steps (
                venture_id INTEGER NOT NULL REFERENCES ventures(id), step TEXT NOT NULL, done_on TEXT,
                PRIMARY KEY (venture_id, step));
        """))

    # ------------------------------------------------------------------ ideas
    def ideas(self, skills: str = "", interests: str = "", hours_per_week: Any = None, budget: str = "") -> dict:
        """Ideas that fit, best first, each with how to check demand before spending anything."""
        words = set(re.findall(r"[a-z]+", f"{skills} {interests}".lower()))
        scored = []
        for idea in IDEAS:
            matched = sorted(word for word in idea.skills if word in words)
            scored.append((len(matched), IDEAS.index(idea), idea, matched))
        scored.sort(key=lambda item: (-item[0], item[1]))
        top = [item for item in scored if item[0]][:6] or scored[:6]
        hours = None
        if hours_per_week not in (None, ""):
            hours = float(_decimal(hours_per_week, "Hours per week", "1", "100"))
        low_budget = bool(re.search(r"(?i)\b(?:0|zero|none|no money|nothing|free|little|small|low)\b", str(budget)))
        results = []
        for score, _index, idea, matched in top:
            reason = (f"Uses what you said: {', '.join(matched)}." if matched else
                      "A common, low-cost way to start; tell HAYAT your skills for a closer match.")
            results.append({
                "key": idea.key, "title": idea.title, "kind": idea.kind, "what": idea.pitch, "why": reason,
                "startup": idea.startup, "first_clients": idea.first_clients, "first_offer": idea.first_offer,
                "hayat_helps_with": idea.hayat_helps,
                "check_demand": [f"Research what people charge for {idea.title.lower()} near me",
                                 f"Research where to find clients for {idea.title.lower()}"],
                "start_it": f"Start a business called <your name> offering {idea.first_offer[0].lower()}"
                            f"{idea.first_offer[1:].rstrip('.')}",
            })
        notes = ["These are starting points, not promises: no idea here comes with guaranteed income.",
                 "Check demand and real prices with the research tools before you spend money."]
        if hours is not None and hours < 6:
            notes.append(f"With about {hours:g} hours a week, start with one small fixed-price offer.")
        if low_budget:
            notes.append("All of these can start with almost no money: a laptop or phone and free tools.")
        return {"ideas": results, "notes": notes,
                "next_step": "Pick one and say: start a business called <name> offering <what> for <who>."}

    # ------------------------------------------------------------------ ventures
    def _venture(self, db: Any, venture: str | int | None) -> Any:
        if venture in (None, ""):
            row = db.execute("SELECT * FROM ventures ORDER BY id DESC LIMIT 1").fetchone()
            if row is None:
                raise ValueError("No business started yet. Say: start a business called <name> offering <what>.")
            return row
        if str(venture).isdigit():
            row = db.execute("SELECT * FROM ventures WHERE id=?", (int(venture),)).fetchone()
        else:
            row = db.execute("SELECT * FROM ventures WHERE name=?", (str(venture).strip(),)).fetchone()
        if row is None:
            names = ", ".join(r["name"] for r in db.execute("SELECT name FROM ventures ORDER BY id")) or "none yet"
            raise ValueError(f"No business called {venture!r}. Yours: {names}.")
        return row

    def start(self, name: str, offer: str, audience: str = "", kind: str = "service", idea: str = "") -> dict:
        name, offer, audience = (name or "").strip(), (offer or "").strip(), (audience or "").strip()
        kind = (kind or "service").strip().lower()
        if kind not in {"service", "product"}:
            raise ValueError("Kind must be service or product.")
        if not name or not offer:
            raise ValueError("Give the business a name and say what it offers.")
        if idea and idea not in IDEA_BY_KEY:
            idea = ""
        now = self.ledger._now()

        def work(db):
            if db.execute("SELECT 1 FROM ventures WHERE name=?", (name,)).fetchone():
                raise ValueError(f"You already have a business called {name}.")
            return db.execute("INSERT INTO ventures (name, kind, offer, audience, idea, created) VALUES (?, ?, ?, ?, ?, ?)",
                              (name, kind, offer, audience or "your clients", idea, now)).lastrowid
        venture_id = self.ledger._run(work)
        if not self.ledger.profile()["business_name"]:
            self.ledger.set_profile(business_name=name)
        status = self.status(venture_id)
        status["note"] = ("Your launch plan is ready. HAYAT ticks steps off as it sees them done. "
                          "No business is registered and nothing is published.")
        return status

    def ventures(self) -> list[dict]:
        return self.ledger._run(lambda db: [dict(r) for r in db.execute("SELECT * FROM ventures ORDER BY id")])

    # ------------------------------------------------------------------ services
    def add_service(self, name: str, price: str, description: str = "", unit: str = "fixed", delivery_days: Any = 0,
                    includes: list[str] | str | None = None, currency: str | None = None,
                    venture: str | int | None = None) -> dict:
        name = (name or "").strip()
        if not name:
            raise ValueError("Name the service, e.g. 'Logo design'.")
        unit = (unit or "fixed").strip().lower()
        if unit not in UNITS:
            raise ValueError("Unit must be one of: " + ", ".join(UNITS) + ".")
        value = cents(price)
        if value <= 0:
            raise ValueError("A service needs a price above 0.")
        days = int(_decimal(delivery_days or 0, "Delivery days", "0", "365"))
        if isinstance(includes, str):
            includes = [part.strip() for part in re.split(r"[;\n]|,(?!\d)", includes) if part.strip()]
        includes = [str(item).strip() for item in (includes or []) if str(item).strip()][:12]
        cur = _currency(currency, self.ledger._default_currency())

        def work(db):
            row = self._venture(db, venture)
            db.execute("INSERT INTO services (venture_id, name, description, price_cents, currency, unit, delivery_days, "
                       "includes, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(venture_id, name) DO UPDATE SET "
                       "description=excluded.description, price_cents=excluded.price_cents, currency=excluded.currency, "
                       "unit=excluded.unit, delivery_days=excluded.delivery_days, includes=excluded.includes",
                       (row["id"], name, (description or "").strip(), value, cur, unit, days, json.dumps(includes),
                        self.ledger._now()))
            return row["name"]
        venture_name = self.ledger._run(work)
        return {"service": name, "price": f"{money(value, cur)} {UNITS[unit]}", "business": venture_name,
                "delivery": f"{days} days" if days else "", "includes": includes}

    def services(self, venture: str | int | None = None) -> dict:
        def work(db):
            row = self._venture(db, venture)
            return row["name"], [dict(r) for r in db.execute("SELECT * FROM services WHERE venture_id=? ORDER BY id",
                                                             (row["id"],))]
        name, rows = self.ledger._run(work)
        for row in rows:
            row["includes"] = json.loads(row["includes"] or "[]")
            row["price"] = f"{money(row['price_cents'], row['currency'])} {UNITS[row['unit']]}"
        return {"business": name, "services": rows}

    def quote_from_service(self, client: str, service: str, quantity: Any = 1, venture: str | int | None = None,
                           valid_days: int = 30) -> dict:
        found = [row for row in self.services(venture)["services"] if row["name"].lower() == (service or "").strip().lower()]
        if not found:
            raise ValueError(f"No service called {service!r}. Add it first.")
        row = found[0]
        line = {"description": row["name"] + (f": {row['description']}" if row["description"] else ""),
                "quantity": str(quantity or 1), "unit_price": f"{row['price_cents'] / 100:.2f}"}
        return self.ledger.create_quote(client, [line], valid_days=valid_days, currency=row["currency"])

    # ------------------------------------------------------------------ pricing
    @staticmethod
    def rate(monthly_income_goal: Any, billable_hours_per_week: Any, monthly_expenses: Any = "0",
             tax_percent: Any = "20", currency: str = "USD", weeks_off_per_year: Any = "4") -> dict:
        """The hourly rate that reaches a monthly goal after tax, costs and time off. Pure arithmetic."""
        goal = _decimal(monthly_income_goal, "Monthly income goal", "1", "10000000")
        hours = _decimal(billable_hours_per_week, "Billable hours per week", "1", "80")
        expenses = _decimal(monthly_expenses or 0, "Monthly expenses", "0", "10000000")
        tax = _decimal(tax_percent if tax_percent not in (None, "") else 20, "Tax percent", "0", "90")
        weeks_off = _decimal(weeks_off_per_year if weeks_off_per_year not in (None, "") else 4, "Weeks off", "0", "40")
        cur = _currency(currency, "USD")
        working_weeks = Decimal(52) - weeks_off
        billable_month = hours * working_weeks / 12
        needed = (goal + expenses) / (1 - tax / 100)
        hourly = (needed / billable_month).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        to_cents = int(hourly * 100)
        return {
            "hourly_rate": money(to_cents, cur), "day_rate_8h": money(to_cents * 8, cur),
            "billable_hours_per_month": float(billable_month.quantize(Decimal("0.1"))),
            "revenue_needed_per_month": money(int(needed * 100), cur),
            "explained": (f"To keep {money(int(goal * 100), cur)} a month after {tax:g}% tax and "
                          f"{money(int(expenses * 100), cur)} of costs, you need {money(int(needed * 100), cur)} a month. "
                          f"Working {hours:g} billable hours a week for {working_weeks:g} weeks a year is about "
                          f"{billable_month.quantize(Decimal('0.1')):g} hours a month, so charge at least "
                          f"{money(to_cents, cur)} an hour."),
            "check": "Compare it with what others charge (research it). If it is far above, cut costs or raise hours; "
                     "if far below, you are probably undercharging.",
        }

    # ------------------------------------------------------------------ launch plan
    def _auto_done(self, row: Any) -> set[str]:
        done = set()
        if row["offer"]:
            done.add("offer")
        profile = self.ledger.profile()
        if profile["business_name"] and profile["email"]:
            done.add("profile")
        if profile["payment_instructions"]:
            done.add("payment")
        if row["website"] and Path(row["website"]).is_file():
            done.add("website")
        if row["marketing"] and Path(row["marketing"]).is_file():
            done.add("marketing")
        count = self.ledger._run(lambda db: db.execute("SELECT COUNT(*) FROM services WHERE venture_id=?",
                                                       (row["id"],)).fetchone()[0])
        if count:
            done.add("price")
        leads = self.ledger.leads("all")["leads"]
        if len(leads) >= 10:
            done.add("leads")
        if sum(1 for lead in leads if lead["status"] != "new") >= 3:
            done.add("outreach")
        if self.ledger.invoices("all")["count"]:
            done.add("first_invoice")
        if self.ledger.payout()["configured"]:
            done.add("withdrawal")
        if self.products is not None:
            try:
                if self.products.catalog()["products"]:
                    done.add("product")
                if self.products.sales_list(1):
                    done.add("first_sale")
            except Exception:
                log_swallowed(_log, "reading the product store for the launch plan")
        return done

    def status(self, venture: str | int | None = None) -> dict:
        def work(db):
            row = self._venture(db, venture)
            manual = {r["step"]: r["done_on"] for r in db.execute(
                "SELECT step, done_on FROM launch_steps WHERE venture_id=? AND done_on IS NOT NULL", (row["id"],))}
            return row, manual
        row, manual = self.ledger._run(work)
        auto = self._auto_done(row)
        steps = []
        for key, title, how, _check in (SERVICE_STEPS if row["kind"] == "service" else PRODUCT_STEPS):
            done = key in auto or key in manual
            steps.append({"step": key, "title": title, "done": done,
                          "how": how.format(offer=row["offer"][:1].lower() + row["offer"][1:], audience=row["audience"]),
                          "ticked_by": "HAYAT saw it in your books" if key in auto else ("you" if key in manual else "")})
        finished = sum(step["done"] for step in steps)
        upcoming = next((step for step in steps if not step["done"]), None)
        return {"business": row["name"], "kind": row["kind"], "offer": row["offer"], "audience": row["audience"],
                "progress": f"{finished} of {len(steps)} steps done", "done": finished, "total": len(steps),
                "steps": steps, "next_step": (f"{upcoming['title']}: {upcoming['how']}" if upcoming
                                              else "Launched. Keep finding leads and invoicing."),
                "services": self.services(row["id"])["services"], "website": row["website"], "marketing": row["marketing"]}

    def step_done(self, step: str, venture: str | int | None = None, undo: bool = False) -> dict:
        step = (step or "").strip().lower()
        keys = {key for key, *_ in SERVICE_STEPS} | {key for key, *_ in PRODUCT_STEPS}
        if step not in keys:
            titles = {key: title for key, title, *_ in SERVICE_STEPS + PRODUCT_STEPS}
            match = next((key for key, title in titles.items() if step and step in title.lower()), None)
            if match is None:
                raise ValueError("Unknown step. Steps: " + ", ".join(sorted(keys)) + ".")
            step = match

        def work(db):
            row = self._venture(db, venture)
            db.execute("INSERT INTO launch_steps (venture_id, step, done_on) VALUES (?, ?, ?) "
                       "ON CONFLICT(venture_id, step) DO UPDATE SET done_on=excluded.done_on",
                       (row["id"], step, None if undo else date.today().isoformat()))
            return row["id"]
        return self.status(self.ledger._run(work))

    # ------------------------------------------------------------------ publishing record
    def published(self, venture: str | int | None = None) -> dict:
        def work(db):
            row = self._venture(db, venture)
            site = db.execute("SELECT * FROM published_sites WHERE venture_id=?", (row["id"],)).fetchone()
            return dict(row), dict(site) if site else None
        row, site = self.ledger._run(work)
        return {"venture_id": row["id"], "business": row["name"], "website": row["website"], "site": site}

    def remember_publish(self, venture_id: int, provider: str, site_id: str, url: str) -> None:
        self.ledger._run(lambda db: db.execute(
            "INSERT INTO published_sites VALUES (?, ?, ?, ?, ?) ON CONFLICT(venture_id) DO UPDATE SET "
            "provider=excluded.provider, site_id=excluded.site_id, url=excluded.url, published_at=excluded.published_at",
            (venture_id, provider, site_id, url, self.ledger._now())))

    # ------------------------------------------------------------------ website
    def website(self, venture: str | int | None = None) -> dict:
        info = self.status(venture)
        profile = self.ledger.profile()
        esc = html.escape
        cards = "".join(
            f"<article class='card'><h3>{esc(service['name'])}</h3><p class='price'>{esc(service['price'])}</p>"
            + (f"<p>{esc(service['description'])}</p>" if service["description"] else "")
            + ("<ul>" + "".join(f"<li>{esc(item)}</li>" for item in service["includes"]) + "</ul>"
               if service["includes"] else "")
            + (f"<p class='muted'>Delivered in {service['delivery_days']} days</p>" if service["delivery_days"] else "")
            + "</article>" for service in info["services"]) or (
            "<article class='card'><h3>Get in touch</h3><p>Tell me what you need and I'll send a quote.</p></article>")
        email = profile["email"]
        contact = (f"<a class='button' href='mailto:{esc(email)}?subject={esc(info['business'])}%20enquiry'>"
                   f"Email {esc(email)}</a>" if email else "<p class='muted'>Add your email in Business details.</p>")
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(info['business'])}</title><meta name="description" content="{esc(info['offer'])}">
<style>
:root{{--ink:#14213d;--muted:#5c677d;--accent:#e0a526;--bg:#f7f8fb;--card:#fff}}
*{{box-sizing:border-box}}body{{margin:0;font-family:Segoe UI,system-ui,sans-serif;color:var(--ink);background:var(--bg);line-height:1.55}}
header{{background:var(--ink);color:#fff;padding:72px 20px 64px;text-align:center}}
header h1{{margin:0 0 12px;font-size:clamp(28px,5vw,44px)}}header p{{margin:0 auto;max-width:640px;font-size:19px;opacity:.9}}
main{{max-width:980px;margin:0 auto;padding:40px 20px}}h2{{margin:40px 0 16px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:18px}}
.card{{background:var(--card);border-radius:14px;padding:22px;box-shadow:0 2px 10px rgba(20,33,61,.08)}}
.card h3{{margin:0 0 6px}}.price{{font-weight:700;color:var(--accent);font-size:20px;margin:0 0 10px}}
.muted{{color:var(--muted);font-size:14px}}.steps{{counter-reset:s;list-style:none;padding:0}}
.steps li{{counter-increment:s;margin:0 0 12px;padding-left:44px;position:relative}}
.steps li:before{{content:counter(s);position:absolute;left:0;top:0;width:30px;height:30px;border-radius:50%;background:var(--accent);color:#fff;text-align:center;font-weight:700;line-height:30px}}
.button{{display:inline-block;background:var(--accent);color:#14213d;padding:14px 26px;border-radius:10px;font-weight:700;text-decoration:none}}
footer{{text-align:center;color:var(--muted);padding:30px 20px;font-size:14px}}
</style></head><body>
<header><h1>{esc(info['business'])}</h1><p>{esc(info['offer'])}{(' for ' + esc(info['audience'])) if info['audience'] and info['audience'] != 'your clients' else ''}.</p></header>
<main>
<h2>{'Services' if info['kind'] == 'service' else 'Products'}</h2><section class="grid">{cards}</section>
<h2>How it works</h2><ol class="steps"><li>Tell me what you need.</li><li>Get a clear quote with the price and delivery date.</li><li>Receive the work, then pay the invoice.</li></ol>
<h2>Contact</h2><p>{contact}</p>
</main><footer>&copy; {date.today().year} {esc(info['business'])}</footer>
</body></html>
"""
        folder = self.ledger.output / "site" / _slug(info["business"])
        path = self.ledger.permissions.validate_project_path(folder / "index.html", write=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page, encoding="utf-8")
        self.ledger._run(lambda db: db.execute("UPDATE ventures SET website=? WHERE name=?", (str(path), info["business"])))
        return {"path": str(path), "business": info["business"], "services_shown": len(info["services"]),
                "published": False,
                "next_step": ("Open it to check it. To put it online for free, upload the folder to a host such as "
                              "GitHub Pages or Netlify; HAYAT has not published it.")}

    # ------------------------------------------------------------------ marketing
    def marketing_kit(self, venture: str | int | None = None) -> dict:
        info = self.status(venture)
        profile = self.ledger.profile()
        name, offer, audience = info["business"], info["offer"], info["audience"]
        services = info["services"]
        first = services[0] if services else None
        price_line = f" from {first['price']}" if first else ""
        sign = profile["owner_name"] or profile["business_name"] or name
        price_list = "\n".join(f"- **{s['name']}**: {s['price']}" + (f", delivered in {s['delivery_days']} days"
                                                                       if s["delivery_days"] else "")
                               for s in services) or "- Add your services in HAYAT to fill this list."
        kit = f"""# Marketing kit: {name}

Edit every piece before you use it, so it sounds like you. Replace anything in [brackets].

## One-line pitch
{name} helps {audience} with {offer[0].lower() + offer[1:]}{price_line}.

## Profile or gig description (Upwork, Fiverr, LinkedIn)
I'm [your name], and through {name} I help {audience} with {offer[0].lower() + offer[1:]}.
What you get: clear communication, an agreed price before I start, and delivery on the date we set.
{('Packages:' + chr(10) + price_list) if services else ''}
Message me with what you need and I'll reply with a quote within a day.

## Outreach email
Subject: Quick idea for [their business]

Hi [name],

I came across [their business] and noticed [something specific you saw]. I help {audience} with {offer[0].lower() + offer[1:]}{price_line}.

Would it help if I sent you [a free example / a short plan] for [their business]? No obligation.

Best regards,
{sign}{(chr(10) + profile['email']) if profile['email'] else ''}

## Follow-up (send 4 to 5 days later)
Hi [name], just checking whether you saw my note about {offer[0].lower() + offer[1:]}. Happy to send an example if useful. {sign}

## Social posts
1. Starting something new: {name}. I help {audience} with {offer[0].lower() + offer[1:]}. Send me a message if that's you, or someone you know.
2. Three signs you need help with {offer[0].lower() + offer[1:]}: [sign 1], [sign 2], [sign 3]. That's exactly what {name} does{price_line}.
3. Before and after: [describe a result]. If you'd like the same for [their goal], let's talk.

## Price list
{price_list}

## Where to share it
- Your own social profiles and local community groups.
- The leads you track in HAYAT (mark each one contacted).
- Marketplaces such as Upwork or Fiverr, in your own account.
"""
        folder = self.ledger.output / "marketing"
        path = self.ledger.permissions.validate_project_path(folder / f"{_slug(name)}-marketing-kit.md", write=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(kit, encoding="utf-8")
        self.ledger._run(lambda db: db.execute("UPDATE ventures SET marketing=? WHERE name=?", (str(path), name)))
        return {"path": str(path), "business": name, "sections": ["pitch", "profile", "outreach email", "follow-up",
                                                                  "social posts", "price list"],
                "note": "Edit it before sending. Sending and posting stay with you."}


def register_launch_tools(registry: ToolRegistry, launchpad: Launchpad) -> None:
    def schema(properties: dict, required: tuple = ()) -> dict:
        return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}

    text = {"type": "string", "minLength": 1, "maxLength": 2000}
    optional = {"type": "string", "maxLength": 2000}
    number = {"type": ["string", "number"]}
    write, read = "filesystem_write_project", "filesystem_read"

    def reg(name: str, description: str, properties: dict, required: tuple, category: str, handler) -> None:
        registry.register(name, description, schema(properties, required), "low", category, handler)

    reg("business_ideas", "Suggest businesses or services the owner could start, matched to their skills, interests, "
        "hours and budget, each with startup needs, where first clients are, a first offer, and how to check demand. "
        "Gives no income promises.",
        {"skills": optional, "interests": optional, "hours_per_week": number, "budget": optional}, (), read,
        launchpad.ideas)
    reg("venture_start", "Start a business or service: name, what it offers, for whom, kind (service or product). "
        "Creates a launch plan that HAYAT ticks off as the books show progress. Registers and publishes nothing.",
        {"name": text, "offer": text, "audience": optional, "kind": optional, "idea": optional}, ("name", "offer"),
        write, launchpad.start)
    reg("venture_status", "Show a business's launch plan: steps done and to do, the next step, its services, website "
        "and marketing kit.", {"venture": optional}, (), read, launchpad.status)
    reg("venture_step_done", "Mark a launch step done (or undo it), e.g. research, legal, store, package.",
        {"step": text, "venture": optional, "undo": {"type": "boolean"}}, ("step",), write, launchpad.step_done)
    reg("service_add", "Add or update a service package with its price and unit (fixed, hour, month, session, item, "
        "word, page), delivery days and what is included.",
        {"name": text, "price": number, "description": optional, "unit": optional, "delivery_days": number,
         "includes": {"type": ["array", "string"]}, "currency": optional, "venture": optional}, ("name", "price"),
        write, launchpad.add_service)
    reg("service_list", "List a business's services and prices.", {"venture": optional}, (), read, launchpad.services)
    reg("quote_from_service", "Make a quote PDF for a client from one of the business's services.",
        {"client": text, "service": text, "quantity": number, "venture": optional}, ("client", "service"), write,
        launchpad.quote_from_service)
    reg("rate_calculator", "Work out the hourly and day rate needed to reach a monthly income goal after tax, "
        "expenses and time off. Pure arithmetic from the owner's numbers.",
        {"monthly_income_goal": number, "billable_hours_per_week": number, "monthly_expenses": number,
         "tax_percent": number, "currency": optional, "weeks_off_per_year": number},
        ("monthly_income_goal", "billable_hours_per_week"), read, launchpad.rate)
    reg("landing_page_create", "Write a one-page website for the business from its offer, services, prices and contact "
        "email, as an HTML file. It is not published.", {"venture": optional}, (), write, launchpad.website)
    reg("marketing_kit_create", "Write a marketing kit for the business: pitch, profile or gig description, outreach "
        "email, follow-up, social posts and price list, as a Markdown file to edit. Sends nothing.",
        {"venture": optional}, (), write, launchpad.marketing_kit)
