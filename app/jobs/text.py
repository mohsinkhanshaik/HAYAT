"""Plain text from posting HTML, and the skill vocabulary both postings and CVs are read with.

The same vocabulary reads both sides, so a skill counts as matched only when the posting and the
CV name it (or one of its usual spellings). Words that are also ordinary English ("Go", "R", "C")
are recognised only in the spellings that mean the skill.
"""
from __future__ import annotations

import html
import re
from functools import lru_cache
from html.parser import HTMLParser

#: Category → entries; each entry is "Canonical|alias|alias" (aliases are matched case-insensitively).
SKILLS: dict[str, tuple[str, ...]] = {
    "Programming": (
        "Python", "Java", "JavaScript|js|ecmascript", "TypeScript", "C++|cpp", "C#|csharp|c sharp", "PHP", "Ruby", "Rust", "Kotlin",
        "Swift|Swift 5", "Scala", "Perl", "MATLAB", "Dart", "Elixir", "Haskell", "Lua", "Julia", "Objective-C|objective c", "Bash|shell scripting",
        "PowerShell", "VBA", "Solidity", "COBOL", "Fortran", "Groovy", "Clojure", "F#",
    ),
    "Web": (
        "React|ReactJS|React.js|reactjs|react.js", "Angular|angularjs", "Vue|vue.js|vuejs", "Svelte", "Next.js|nextjs", "Nuxt", "Node.js|nodejs|node",
        "Express.js|expressjs", "Django", "Flask", "FastAPI", "Spring Boot|spring framework", "Ruby on Rails|rails", "Laravel", "Symfony",
        "ASP.NET|asp.net core", ".NET|dotnet|.net core", "HTML|html5", "CSS|css3", "Sass|scss", "Tailwind|tailwindcss",
        "Bootstrap", "jQuery", "Redux", "GraphQL", "REST|rest api|restful|rest apis", "WebSockets|websocket", "gRPC",
        "WordPress", "Shopify", "Webflow", "SEO", "Web accessibility|wcag|accessibility", "Webpack", "Vite",
    ),
    "Mobile": ("Android", "iOS", "React Native", "Flutter", "SwiftUI", "Jetpack Compose", "Xamarin", "Ionic"),
    "Data & AI": (
        "SQL", "Machine learning|ml", "Deep learning", "Data analysis|data analytics", "Data science", "Statistics",
        "Pandas", "NumPy", "scikit-learn|sklearn", "TensorFlow", "PyTorch", "Keras", "NLP|natural language processing",
        "Computer vision", "LLM|llms|large language models", "Generative AI|genai", "Prompt engineering", "LangChain",
        "Spark|Apache Spark|PySpark|pyspark", "Hadoop", "Airflow|apache airflow", "dbt", "ETL|elt", "Data engineering", "Data modeling|data modelling",
        "Data warehousing|data warehouse", "Tableau", "Power BI|powerbi", "Looker", "Excel|MS Excel|Microsoft Excel|MS-Excel", "Google Sheets",
        "A/B testing|ab testing", "Kafka|apache kafka", "Snowflake", "BigQuery", "Databricks", "Redshift", "MLOps", "R programming|rstats",
        "SPSS", "SAS", "Jupyter", "Matplotlib", "Data visualization|data visualisation", "Forecasting", "Hugging Face|huggingface",
    ),
    "Databases": (
        "PostgreSQL|postgres", "MySQL", "SQLite", "MongoDB", "Redis", "Elasticsearch", "Cassandra", "DynamoDB", "Oracle Database|oracle db",
        "SQL Server|mssql|microsoft sql server", "MariaDB", "Neo4j", "Firebase", "Supabase",
    ),
    "Cloud & DevOps": (
        "AWS|amazon web services", "Azure|microsoft azure", "GCP|google cloud|google cloud platform", "Docker", "Kubernetes|k8s",
        "Terraform", "Ansible", "Jenkins", "GitHub Actions", "GitLab CI|gitlab", "CI/CD|ci cd|continuous integration", "Linux", "Unix",
        "Git", "Helm", "Prometheus", "Grafana", "Nginx", "Serverless", "Lambda|aws lambda", "Microservices", "DevOps", "SRE|site reliability",
        "Networking", "TCP/IP", "Cloudflare", "OpenShift", "Vagrant",
    ),
    "Security": (
        "Cybersecurity|cyber security|information security|infosec", "Penetration testing|pentesting|pen testing", "SIEM", "SOC",
        "Vulnerability management", "IAM|identity and access management", "OWASP", "ISO 27001", "Network security", "Incident response",
        "Cryptography", "Splunk", "Threat intelligence",
    ),
    "Testing & quality": (
        "QA|quality assurance", "Test automation|automated testing", "Selenium", "Cypress", "Playwright", "Jest", "pytest", "JUnit",
        "Unit testing", "TDD|test driven development", "Manual testing",
    ),
    "Product & design": (
        "Figma", "Sketch", "Adobe XD", "Photoshop|adobe photoshop", "Illustrator|adobe illustrator", "InDesign", "After Effects",
        "Premiere Pro|premiere", "UX|user experience|ux design", "UI design|ui|user interface", "Product management", "Product design",
        "Wireframing", "Prototyping", "User research", "Design systems", "Graphic design", "Video editing", "Motion graphics", "Blender",
        "3D modeling|3d modelling", "Canva", "AutoCAD", "SolidWorks", "Revit", "Agile", "Scrum", "Kanban", "Jira", "Confluence",
        "Roadmapping", "Stakeholder management",
    ),
    "Marketing & content": (
        "Digital marketing", "Content marketing", "Social media|social media marketing", "Copywriting", "Content writing", "Email marketing",
        "Google Analytics", "Google Ads", "Facebook Ads|meta ads", "PPC", "Marketing automation", "HubSpot", "Mailchimp", "Brand strategy",
        "Public relations", "Growth marketing", "Technical writing", "Editing", "Proofreading", "Translation", "Community management",
    ),
    "Sales & business": (
        "Sales", "B2B sales|b2b", "Business development", "Account management", "Lead generation", "Salesforce", "CRM",
        "Negotiation", "Customer success", "Partnerships", "Cold calling", "Market research", "Business analysis", "Consulting",
        "Project management", "PMP", "Strategy", "Operations", "Supply chain", "Logistics", "Procurement", "E-commerce|ecommerce",
    ),
    "Finance & admin": (
        "Accounting", "Bookkeeping", "Financial analysis", "Financial modeling|financial modelling", "QuickBooks", "Xero", "SAP",
        "Tally", "Taxation|tax", "Auditing|audit", "Payroll", "Budgeting", "FP&A", "CPA", "ACCA", "CFA", "Invoicing", "Compliance",
        "Risk management", "Data entry", "Office administration", "Microsoft Office|ms office", "Recruiting|recruitment|talent acquisition",
        "HR|human resources",
    ),
    "Support & people": (
        "Customer service|customer support", "Technical support|tech support|help desk|helpdesk", "Zendesk", "Communication",
        "Teamwork", "Leadership", "Problem solving|problem-solving", "Time management", "Mentoring", "Teaching", "Training",
        "Presentation skills|public speaking",
    ),
    "Spoken languages": (
        "English", "German|deutsch", "French", "Spanish", "Portuguese", "Italian", "Dutch", "Arabic", "Hindi", "Urdu",
        "Mandarin|chinese", "Japanese", "Korean", "Russian", "Turkish", "Polish", "Swedish", "Telugu", "Tamil", "Bengali",
    ),
}
#: Skills whose everyday spelling is also an ordinary word: matched on the original text, case and context included.
SPECIAL = {
    "Go": r"\bGolang\b|\bgolang\b|\bGo\b(?=\s*(?:,|;|/|\)|\band\b|developer|engineer|programming|language|lang\b|\(golang))|(?<=[,/(]\s)Go\b",
    "R": r"\bR\b(?=\s*(?:,|;|/|\)|programming|language|studio|shiny|\band\b))|(?<=[,/(]\s)R\b(?![-'])",
    "C": r"(?<![\w#+.])C(?![\w#+.])(?=\s*(?:,|;|/|\)|programming|language|\band\b))|(?<=[,/(]\s)C(?![\w#+.'-])",
}
#: Skills whose name is also an everyday word in lower case ("excel at", "react quickly", "rust", "at the helm"):
#: only these spellings, case as written, count.
CASE_SENSITIVE = frozenset({"React", "Excel", "Sketch", "Spark", "Swift", "Rust", "Helm"})
LANGUAGES = frozenset(entry.split("|")[0] for entry in SKILLS["Spoken languages"])
CATEGORY = {entry.split("|")[0]: category for category, entries in SKILLS.items() for entry in entries} | {
    "Go": "Programming", "R": "Data & AI", "C": "Programming"}


@lru_cache(maxsize=1)
def _patterns() -> list[tuple[str, re.Pattern[str]]]:
    found = []
    for entries in SKILLS.values():
        for entry in entries:
            names = entry.split("|")
            alternatives = "|".join(re.escape(name).replace(r"\ ", r"[\s-]+") for name in sorted(names, key=len, reverse=True))
            flags = 0 if names[0] in CASE_SENSITIVE else re.IGNORECASE
            # Not after a dot either: the "js" of "Node.js" is not JavaScript.
            found.append((names[0], re.compile(r"(?<![\w+#.])(?:" + alternatives + r")(?![\w+#])", flags)))
    found += [(name, re.compile(pattern)) for name, pattern in SPECIAL.items()]
    return found


def skills_in(text: str) -> dict[str, int]:
    """{skill: times named} for every skill the text names."""
    value = str(text or "")
    counts = {}
    for name, pattern in _patterns():
        hits = len(pattern.findall(value))
        if hits:
            counts[name] = hits
    # "React Native" is not also React, and "Node.js developer" names Node.js once.
    if "React Native" in counts and "React" in counts:
        counts["React"] -= counts["React Native"]
        if counts["React"] <= 0:
            del counts["React"]
    return counts


class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "header", "footer"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n• " if tag == "li" else "\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK and tag != "li":  # the next item starts its own line
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def plain(value: str) -> str:
    """Readable text from HTML (or text that was escaped HTML), with lists kept as bullets."""
    text = str(value or "")
    if "&lt;" in text and "<" not in text:
        text = html.unescape(text)
    if "<" in text and ">" in text:
        parser = _Text()
        parser.feed(text)
        text = "".join(parser.parts)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    out, blank = [], False
    for line in lines:
        if not line or line == "•":
            if out and not blank:
                out.append("")
            blank = True
            continue
        out.append(line)
        blank = False
    return "\n".join(out).strip()


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9+#.]+", str(text or "").lower())


def slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:limit] or "job"
