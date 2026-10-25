from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from app.brain_mesh import BrainMesh


EVIDENCE = PROJECT_ROOT / "data/benchmarks/architecture/latest.json"
OUTPUT = PROJECT_ROOT / "output/pdf/HAYAT-Architecture-and-Operator-Report.pdf"

BLACK = colors.HexColor("#07080A")
PANEL = colors.HexColor("#15181D")
LINE_COLOR = colors.HexColor("#343A46")
WHITE = colors.HexColor("#F4F6F8")
MUTED = colors.HexColor("#697382")
GOLD = colors.HexColor("#D4A82C")
GOLD_SOFT = colors.HexColor("#E8C765")
ROYAL = colors.HexColor("#2F5BD9")
ROYAL_SOFT = colors.HexColor("#7FA0F0")
OLIVE = colors.HexColor("#7E9B3F")
ALERT = colors.HexColor("#C94040")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("Title", parent=base["Title"], fontName="Helvetica-Bold",
                                fontSize=28, leading=32, textColor=BLACK, spaceAfter=14),
        "subtitle": ParagraphStyle("Subtitle", parent=base["Normal"], fontName="Helvetica",
                                   fontSize=11, leading=16, textColor=MUTED, spaceAfter=12),
        "h1": ParagraphStyle("H1", parent=base["Heading1"], fontName="Helvetica-Bold",
                             fontSize=18, leading=22, textColor=BLACK, spaceBefore=8, spaceAfter=9),
        "h2": ParagraphStyle("H2", parent=base["Heading2"], fontName="Helvetica-Bold",
                             fontSize=13, leading=16, textColor=ROYAL, spaceBefore=8, spaceAfter=6),
        "body": ParagraphStyle("Body", parent=base["BodyText"], fontName="Helvetica",
                               fontSize=9.2, leading=13.2, textColor=colors.HexColor("#20242B"),
                               spaceAfter=6),
        "small": ParagraphStyle("Small", parent=base["BodyText"], fontName="Helvetica",
                                fontSize=7.6, leading=10.2, textColor=colors.HexColor("#303640")),
        "table_header": ParagraphStyle("TableHeader", parent=base["BodyText"],
                                        fontName="Helvetica-Bold", fontSize=7.6,
                                        leading=10.2, textColor=WHITE),
        "callout": ParagraphStyle("Callout", parent=base["BodyText"], fontName="Helvetica-Bold",
                                  fontSize=9.4, leading=13.2, textColor=BLACK, leftIndent=8,
                                  rightIndent=8, spaceBefore=4, spaceAfter=4),
        "footer": ParagraphStyle("Footer", parent=base["Normal"], fontName="Helvetica",
                                 fontSize=7, textColor=MUTED, alignment=TA_CENTER),
        "caption": ParagraphStyle("Caption", parent=base["Normal"], fontName="Helvetica-Oblique",
                                  fontSize=7.5, leading=10, textColor=MUTED, alignment=TA_CENTER),
    }


STYLES = _styles()


def p(text: str, style: str = "body") -> Paragraph:
    return Paragraph(text, STYLES[style])


def bullets(items: list[str]) -> list[Any]:
    result: list[Any] = []
    for item in items:
        result.append(Paragraph(f"- {item}", ParagraphStyle(
            "bullet-row", parent=STYLES["body"], leftIndent=12, firstLineIndent=-8,
            spaceAfter=3,
        )))
    return result


def _arrow(drawing: Drawing, x1: float, y1: float, x2: float, y2: float,
           colour: colors.Color = MUTED) -> None:
    drawing.add(Line(x1, y1, x2, y2, strokeColor=colour, strokeWidth=1.1))
    if abs(x2 - x1) >= abs(y2 - y1):
        direction = 1 if x2 > x1 else -1
        drawing.add(Polygon([x2, y2, x2 - 5 * direction, y2 + 3,
                             x2 - 5 * direction, y2 - 3], fillColor=colour, strokeColor=colour))
    else:
        direction = 1 if y2 > y1 else -1
        drawing.add(Polygon([x2, y2, x2 - 3, y2 - 5 * direction,
                             x2 + 3, y2 - 5 * direction], fillColor=colour, strokeColor=colour))


def _node(drawing: Drawing, x: float, y: float, w: float, h: float, title: str,
          subtitle: str = "", colour: colors.Color = ROYAL) -> None:
    drawing.add(Rect(x, y, w, h, rx=6, ry=6, fillColor=colors.white,
                     strokeColor=colour, strokeWidth=1.4))
    drawing.add(Rect(x, y + h - 6, w, 6, rx=3, ry=3, fillColor=colour, strokeColor=colour))
    drawing.add(String(x + w / 2, y + h / 2 + 3, title, fontName="Helvetica-Bold",
                       fontSize=8.2, textAnchor="middle", fillColor=BLACK))
    if subtitle:
        drawing.add(String(x + w / 2, y + h / 2 - 9, subtitle[:28], fontName="Helvetica",
                           fontSize=6.3, textAnchor="middle", fillColor=MUTED))


def system_context_figure() -> Drawing:
    d = Drawing(520, 290)
    _node(d, 190, 115, 140, 62, "SHARED RUNTIME", "authority + evidence core", GOLD)
    for x, title in ((15, "GUI OPERATOR"), (190, "CLI"), (365, "VOICE V2")):
        _node(d, x, 230, 140, 42, title, "owner interaction", ROYAL)
        _arrow(d, x + 70, 230, 260, 177, ROYAL_SOFT)
    bottoms = ((5, "WINDOWS + SENSORS"), (135, "LOCAL MODELS"),
               (265, "PUBLIC WEB"), (395, "MCP + CONNECTORS"))
    for x, title in bottoms:
        _node(d, x, 20, 120, 42, title, "scoped adapter", OLIVE)
        _arrow(d, 260, 115, x + 60, 62, MUTED)
    d.add(String(260, 196, "AUTHORITY", fontName="Helvetica-Bold", fontSize=7.2,
                 textAnchor="middle", fillColor=GOLD))
    d.add(String(260, 88, "OBSERVE - VERIFY - REMEMBER", fontName="Helvetica-Bold",
                 fontSize=7.2, textAnchor="middle", fillColor=ROYAL))
    return d


def brain_mesh_figure() -> Drawing:
    d = Drawing(520, 490)
    positions = {
        "interface": (205, 434), "inquiry": (380, 434), "intent": (30, 360), "router": (205, 360),
        "planner": (380, 360), "cortex": (30, 290), "dual_brain": (205, 290),
        "invention": (380, 290), "context": (30, 220), "helpers": (205, 220),
        "tool_judgment": (380, 220), "authority": (30, 150), "tools": (205, 150),
        "verifier": (380, 150), "memory": (30, 80), "dna": (205, 80),
        "research": (380, 80), "learning": (5, 5), "network": (135, 5),
        "perception": (265, 5), "observability": (395, 5),
    }
    unplaced = sorted({node.node_id for node in BrainMesh.NODES} - set(positions))
    if unplaced:
        raise ValueError(f"Brain mesh nodes without a diagram position: {', '.join(unplaced)}")
    colours = {"control": GOLD, "knowledge": OLIVE, "operator": ROYAL,
               "reasoning": ROYAL_SOFT, "action": colors.HexColor("#8564C8"),
               "adaptation": colors.HexColor("#64A878"), "interaction": GOLD_SOFT}
    by_id = {node.node_id: node for node in BrainMesh.NODES}
    centers: dict[str, tuple[float, float]] = {}
    for node_id, (x, y) in positions.items():
        node = by_id[node_id]
        _node(d, x, y, 115, 42, node.label.upper(), node.layer, colours[node.layer])
        centers[node_id] = (x + 57.5, y + 21)
    for edge in BrainMesh.EDGES:
        x1, y1 = centers[edge.source]
        x2, y2 = centers[edge.target]
        dx, dy = x2 - x1, y2 - y1
        length = max(1.0, (dx * dx + dy * dy) ** .5)
        _arrow(d, x1 + dx * 24 / length, y1 + dy * 18 / length,
               x2 - dx * 58 / length, y2 - dy * 21 / length,
               colors.HexColor("#A8AFBA"))
    d.add(String(260, 480, f"{len(BrainMesh.NODES)} INDEPENDENT NODES - {len(BrainMesh.EDGES)} TEAM CONTRACTS",
                 fontName="Helvetica-Bold", fontSize=10, textAnchor="middle", fillColor=BLACK))
    return d


def lifecycle_figure() -> Drawing:
    d = Drawing(520, 170)
    stages = [
        ("INTENT", ROYAL), ("RECALL", OLIVE), ("PLAN", colors.HexColor("#8564C8")),
        ("AUTHORIZE", GOLD), ("ACT", ROYAL), ("OBSERVE", OLIVE),
        ("VERIFY", GOLD), ("REMEMBER", OLIVE),
    ]
    width, gap, start = 55, 9, 3
    for index, (title, colour) in enumerate(stages):
        x = start + index * (width + gap)
        _node(d, x, 78, width, 44, title, str(index + 1), colour)
        if index < len(stages) - 1:
            _arrow(d, x + width, 100, x + width + gap - 1, 100, MUTED)
    d.add(String(260, 145, "REQUEST LIFECYCLE", fontName="Helvetica-Bold", fontSize=10,
                 textAnchor="middle", fillColor=BLACK))
    d.add(String(260, 48, "A fluent answer cannot bypass authority or final-state evidence.",
                 fontName="Helvetica", fontSize=8.5, textAnchor="middle", fillColor=MUTED))
    d.add(String(260, 29, "Failed verification can retry only within explicit budgets.",
                 fontName="Helvetica", fontSize=8.5, textAnchor="middle", fillColor=MUTED))
    return d


def memory_figure() -> Drawing:
    d = Drawing(520, 235)
    _node(d, 12, 165, 95, 42, "QUESTION", "scoped query", ROYAL)
    for x, title, sub in ((140, "FTS", "exact terms"), (265, "HASH", "semantic fallback"),
                          (390, "NEURAL", "local embedding")):
        _node(d, x, 165, 105, 42, title, sub, OLIVE)
        _arrow(d, 107, 186, x, 186, MUTED)
    _node(d, 205, 93, 110, 46, "RANK + FILTER", "scope freshness trust", GOLD)
    for x in (192, 317, 442):
        _arrow(d, x, 165, 260, 139, MUTED)
    _node(d, 70, 20, 120, 44, "DNA TRAVERSAL", "bounded graph", colors.HexColor("#8564C8"))
    _node(d, 330, 20, 120, 44, "EVIDENCE SET", "provenance + conflict", OLIVE)
    _arrow(d, 245, 93, 150, 64, MUTED)
    _arrow(d, 190, 42, 330, 42, MUTED)
    d.add(String(260, 222, "HYBRID MEMORY AND DNA RETRIEVAL", fontName="Helvetica-Bold",
                 fontSize=10, textAnchor="middle", fillColor=BLACK))
    return d


def network_figure() -> Drawing:
    d = Drawing(520, 250)
    _node(d, 205, 175, 110, 44, "OPERATOR CORE", "authority + audit", GOLD)
    items = [
        (5, 90, "PUBLIC WEB", "read + research"),
        (135, 90, "BROWSER", "isolated profile"),
        (265, 90, "MCP", "declared tools"),
        (395, 90, "CONNECTORS", "scoped accounts"),
    ]
    for x, y, title, sub in items:
        _node(d, x, y, 120, 44, title, sub, ROYAL)
        _arrow(d, 260, 175, x + 60, y + 44, MUTED)
    _node(d, 70, 15, 160, 42, "AUTHORIZED REMOTE API", "telemetry + exact approval", OLIVE)
    _node(d, 290, 15, 160, 42, "BLOCKED PATHS", "cracking bypass attack", ALERT)
    _arrow(d, 455, 90, 180, 57, OLIVE)
    d.add(String(260, 235, "NETWORK AND REMOTE-OPERATION BOUNDARY",
                 fontName="Helvetica-Bold", fontSize=10, textAnchor="middle", fillColor=BLACK))
    return d


def _table(data: list[list[Any]], widths: list[float], *, header: bool = True,
           small: bool = False) -> Table:
    converted = []
    for r, row in enumerate(data):
        style_name = "table_header" if header and r == 0 else ("small" if small or r else "body")
        converted.append([
            item if hasattr(item, "wrap") else p(str(item), style_name)
            for item in row
        ])
    table = Table(converted, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#D9DEE6")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F6F8FA")]),
    ]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), PANEL),
                  ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
                  ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
    table.setStyle(TableStyle(style))
    return table


def _header_footer(canvas, doc) -> None:
    canvas.saveState()
    width, height = letter
    if doc.page > 1:
        canvas.setStrokeColor(colors.HexColor("#D8DDE5"))
        canvas.line(doc.leftMargin, height - 35, width - doc.rightMargin, height - 35)
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.setFillColor(GOLD)
        canvas.drawString(doc.leftMargin, height - 27, "HAYAT OPERATOR ARCHITECTURE")
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(width - doc.rightMargin, height - 27, "Evidence report - " + doc.evidence_date)
    canvas.setStrokeColor(colors.HexColor("#D8DDE5"))
    canvas.line(doc.leftMargin, 31, width - doc.rightMargin, 31)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(doc.leftMargin, 20, "Local-first, evidence-first, permissioned operation")
    canvas.drawRightString(width - doc.rightMargin, 20, f"Page {doc.page}")
    canvas.restoreState()


def _metric(value: Any, fallback: str = "-") -> str:
    return fallback if value is None else str(value)


def build(evidence: dict[str, Any], output: Path = OUTPUT) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output), pagesize=letter, rightMargin=.58 * inch, leftMargin=.58 * inch,
        topMargin=.62 * inch, bottomMargin=.52 * inch,
        title="HAYAT Architecture and Operator Report",
        author="HAYAT project",
        subject="Architecture, infrastructure, brain mesh, operator GUI, benchmarks, and safety",
    )
    doc.evidence_date = str(evidence.get("generated_at") or "date not recorded")[:10]
    story: list[Any] = []
    release = evidence.get("release", {})
    mesh = evidence.get("brain_mesh", {})
    benchmark = evidence.get("brain_benchmark", {}).get("benchmarks", {})
    security = evidence.get("security", {})
    memory = evidence.get("memory_integrity", {})
    knowledge = evidence.get("knowledge", {})
    runtime = evidence.get("runtime", {})
    alignment = evidence.get("learning", {}).get("alignment", {})

    story += [Spacer(1, .45 * inch), p("HAYAT", "title"),
              p("Operator Architecture and Infrastructure Report", "h1"),
              p("GUI operator surface, cooperating brain nodes, neural/DNA retrieval, "
                "network boundaries, logical decision controls, analytics, and verification", "subtitle"),
              Spacer(1, .15 * inch), system_context_figure(), Spacer(1, .16 * inch)]
    cover_metrics = [
        ["LIVE EVIDENCE", "VALUE"],
        ["Brain mesh", f"{'PASS' if mesh.get('passed') else 'FAIL'} - {mesh.get('nodes', 0)} nodes / {mesh.get('edges', 0)} contracts"],
        ["Regression", f"{release.get('tests_passed', 'unknown')} passed - {release.get('tests_failed', 'unknown')} failed - {release.get('tests_errors', 'unknown')} errors - {release.get('tests_skipped', 'unknown')} skipped"],
        ["Memory integrity", f"{memory.get('valid', 0):,} / {memory.get('checked', 0):,} valid"],
        ["Knowledge", f"{knowledge.get('claims', 0):,} claims - {knowledge.get('evidence', 0):,} evidence - {knowledge.get('unresolved_conflicts', 0)} unresolved conflicts"],
        ["Security", f"{'PASS' if security.get('passed') else 'ATTENTION'} - Defender {security.get('download_defense', {}).get('provider', 'unavailable')}"],
    ]
    story += [_table(cover_metrics, [1.55 * inch, 5.05 * inch]), Spacer(1, .12 * inch),
              p("Scope note: this report measures operational architecture. It does not claim "
                "consciousness, infallibility, zero malware risk, or a foundation model trained from scratch.", "callout"),
              PageBreak()]

    story += [p("1. Executive conclusions", "h1")]
    story += bullets([
        "HAYAT uses one shared runtime and one deterministic authority/evidence core across GUI, CLI, and voice.",
        f"The recorded mesh has {mesh.get('nodes', 'an unrecorded number of')} nodes; independent probes and team contracts check their integration.",
        "Models may propose and explain, but cannot approve tools or substitute fluent text for observed success.",
        "Hybrid memory combines exact, FTS, hashed-semantic, local-neural, entity, freshness, confidence, and DNA signals.",
        "Public web, isolated browser, MCP, and scoped connectors are supported; external mutation retains exact approval.",
        "Authorized satellite APIs can be integrated as high-consequence connectors; generic cracking, bypass, and attack paths are excluded.",
    ])
    story += [p("System context", "h2"), system_context_figure(),
              p("Figure 1. Every interaction surface converges on one runtime. External systems are adapters below the same authority and verifier.", "caption"),
              PageBreak()]

    story += [p("2. Brain mesh", "h1"),
              p("A brain node is a separately testable software responsibility. It is not a claim of a separate mind. "
                "The architecture prevents a language model, webpage, connector, or GUI control from becoming its own authority."),
              brain_mesh_figure(),
              p("Figure 2. Fourteen nodes and their principal data/control relationships.", "caption"),
              PageBreak(),
              p("Brain-node catalog", "h1"),
              p("Each node has one primary responsibility and is checked both independently and as part of the shared runtime.")]
    node_rows = [["NODE", "LAYER", "RESPONSIBILITY", "CRITICAL"]]
    for node in BrainMesh.NODES:
        node_rows.append([node.label, node.layer, node.responsibility, "yes" if node.critical else "optional/degraded"])
    story += [_table(node_rows, [.9 * inch, .8 * inch, 4.15 * inch, 1.05 * inch], small=True), PageBreak()]

    story += [p("3. Request lifecycle and truth", "h1"), lifecycle_figure(),
              p("Figure 3. The path is bounded and evidence-bearing from intent through memory.", "caption"),
              p("Logical decisions combine authority, evidence, safety, reversibility, and final-state truth. "
                "The owner can widen ordinary local convenience, but model/web instructions cannot disable protected boundaries."),
              p("Decision hierarchy", "h2")]
    story += bullets([
        "Live observation outranks memory.",
        "Verified, provenance-bearing knowledge outranks unsupported inference.",
        "Conflicted or stale facts are shown as such or refreshed.",
        "A technically possible action remains blocked when unauthorized or unsafe.",
        "Tool return success does not equal task success until the requested state is observed.",
    ])
    story += [p("Permanent boundaries", "h2")]
    boundary_rows = [
        ["SUPPORTED", "REQUIRES EXACT CONTROL", "NOT IMPLEMENTED"],
        ["Local apps, files, public research, licensed APIs", "External writes, logins, high-consequence remote commands", "Cracking, access bypass, malware, retaliation, unauthorized control"],
    ]
    story += [_table(boundary_rows, [2.25 * inch, 2.25 * inch, 2.4 * inch]), PageBreak()]

    story += [p("4. Memory, neural retrieval, and DNA", "h1"), memory_figure(),
              p("Figure 4. Candidate generation remains multi-path; trust and scope gates precede DNA expansion.", "caption"),
              p("Memory records and neural model weights are separate. Stored information becomes indexed text, hashes, local embeddings, "
                "entities, provenance, and graph associations. It does not silently rewrite the foundation model."),
              p("DNA traversal", "h2")]
    story += bullets([
        "Start from several high-ranking trusted seeds instead of one brittle match.",
        "Traverse indexed associations with bounded depth and fan-out.",
        "Combine relationship strength with lexical/neural relevance, project scope, confidence, freshness, and verified use.",
        "Reject secret-like or untrusted poisoned material from automatic association creation.",
        "Quarantine tamper mismatches instead of serving them as memory.",
    ])
    recall = evidence.get("learning", {}).get("recall_health", {})
    recall_rows = [
        ["MEMORY EVIDENCE", "VALUE"],
        ["SQLite integrity", _metric(recall.get("integrity"))],
        ["Healthy indexes", _metric(recall.get("healthy"))],
        ["Hashed vectors missing", _metric(recall.get("missing_hashed_vectors"), "0")],
        ["FTS rows missing", _metric(recall.get("missing_fts_rows"), "0")],
        ["Tamper records checked", f"{memory.get('checked', 0):,}"],
        ["Tamper mismatches", f"{len(memory.get('mismatches', [])):,}"],
    ]
    story += [_table(recall_rows, [2.3 * inch, 4.6 * inch]), PageBreak()]

    story += [p("5. Research and contradiction handling", "h1"),
              p("Search results are discovery data. Claims require a fetched source, matching URL and content hash, an exact supporting excerpt, "
                "source fitness, freshness, and explicit evidence status. Opposing same-scope claims remain separate and linked by conflict records."),
              p("Evidence store", "h2")]
    research_rows = [
        ["METRIC", "COUNT", "INTERPRETATION"],
        ["Sources", f"{knowledge.get('sources', 0):,}", "Retained public provenance"],
        ["Claims", f"{knowledge.get('claims', 0):,}", "Atomic assertions with status"],
        ["Evidence", f"{knowledge.get('evidence', 0):,}", "Claim-to-source support links"],
        ["Disputed claims", f"{knowledge.get('disputed_claims', 0):,}", "Visible uncertainty, not hidden"],
        ["Unresolved conflicts", f"{knowledge.get('unresolved_conflicts', 0):,}", "Must be zero for settled local reuse"],
        ["Superseded", f"{knowledge.get('superseded', 0):,}", "History preserved, not deleted"],
    ]
    story += [_table(research_rows, [1.55 * inch, .8 * inch, 4.55 * inch]),
              p("Standards and requirement questions prioritize complete normative statements. Cached answers are reused only when confidence, "
                "freshness, intent facet, and contradiction state still match."), PageBreak()]

    story += [p("6. Operator and network infrastructure", "h1"), network_figure(),
              p("Figure 5. Remote capability is adapter-based and scoped; blocked paths do not become tools.", "caption")]
    network_rows = [
        ["PATH", "WHAT IT CAN DO", "BOUNDARY"],
        ["Isolated browser", "Navigate, inspect, scroll, capture, download", "Public validation; no personal profile"],
        ["Browser bridge", "Read/scroll owner-opened active tab", "Authenticated localhost session"],
        ["Public research", "Search, fetch, crawl, API GET", "DNS, redirects, robots, rate/time/byte budgets"],
        ["MCP", "Discover and call declared tools", "Read/write split; exact approval for mutation"],
        ["Connectors", "Read or act on configured accounts", "Secret broker and scoped manifests"],
        ["Satellite/remote API", "Telemetry and allowlisted commands", "Future explicit licensed connector only"],
    ]
    story += [_table(network_rows, [1.15 * inch, 2.45 * inch, 3.3 * inch], small=True),
              p("A legitimate satellite connector would read telemetry first, use a target/command allowlist, simulate or dry-run when available, "
                "require exact owner approval for commands, verify the response, and preserve an audit trail. HAYAT has no generic radio, "
                "ground-station bypass, exploit, or credential-theft facility."), PageBreak()]

    story += [p("7. GUI Operator Center", "h1"),
              p("The GUI presents architecture as live evidence. A dedicated Operator view uses a scrollable three-column grid of node cards, "
                "readiness colors, node latency, a mesh summary, and Refresh, Self Check, and Benchmark controls."),
              p("Interaction design", "h2")]
    story += bullets([
        "Operator and Self Check are available as normal buttons and pinnable toolbox tiles.",
        "Checks run off the Tk UI thread; completion is painted back onto the main loop.",
        "Routine refresh avoids forcing a cold live-model inventory; the explicit benchmark measures it.",
        "The Operator Center uses the same runtime and registry as text and voice, so it cannot bypass authority.",
        "READY and CHECK reflect actual probe results; absent optional hardware is degraded rather than fabricated as active.",
    ])
    story += [p("Node-state semantics", "h2")]
    state_rows = [
        ["STATE", "MEANING", "RESPONSE"],
        ["READY", "Independent probe passed", "Available through normal authority"],
        ["CHECK", "Probe failed or optional dependency degraded", "Inspect evidence; no invented success"],
        ["MESH READY", "All critical nodes and team contracts passed", "Normal operation"],
        ["MESH ATTENTION", "A critical node or integration contract failed", "Diagnose before high-consequence work"],
    ]
    story += [_table(state_rows, [1.1 * inch, 3 * inch, 2.8 * inch]), PageBreak()]

    story += [p("8. Coordination benchmark", "h1"),
              p("Twenty iterations measure coordination functions only. Generation quality is deliberately outside this benchmark."),]
    bench_rows = [["OPERATION", "MEAN MS", "P95 MS", "COLD FIRST", "WARM MEAN", "MAX MS"]]
    for name in ("intent", "model_router", "memory_health", "tool_inventory", "team_contract"):
        row = benchmark.get(name, {})
        bench_rows.append([
            name.replace("_", " ").title(), f"{row.get('mean_ms', 0):.3f}",
            f"{row.get('p95_ms', 0):.3f}", f"{row.get('cold_first_ms', 0):.3f}",
            f"{row.get('warm_mean_ms', 0):.3f}", f"{row.get('max_ms', 0):.3f}",
        ])
    story += [_table(bench_rows, [1.5 * inch, .9 * inch, .8 * inch, 1.05 * inch, 1.05 * inch, .85 * inch], small=True),
              Spacer(1, 8),
              p("Interpretation: the first model-router call may refresh live model/hardware inventory and dominate the overall mean. "
                "Subsequent warm calls show the steady-state coordination cost. The Operator refresh checks availability without paying that cold probe."),
              p("This table is not a language benchmark. Model accuracy, throughput, vision, speech, and retrieval quality retain their separate finite evaluations."),
              PageBreak()]

    story += [p("9. Security, file admission, and self-defense", "h1")]
    defender = security.get("download_defense", {})
    security_rows = [
        ["CONTROL", "LIVE RESULT"],
        ["Dependency consistency", "pass" if security.get("pip_check", {}).get("passed") else "attention"],
        ["Tracked/untracked secret scan", "pass" if security.get("secret_scan", {}).get("passed") else "attention"],
        ["Antimalware provider", defender.get("provider", "unavailable")],
        ["Fail closed", str(defender.get("fail_closed", False))],
        ["Automatic execution", str(defender.get("automatic_execution", False))],
        ["Counterattack", str(defender.get("counterattack", False))],
    ]
    story += [_table(security_rows, [2.3 * inch, 4.6 * inch]),
              p("Browser, research, governed downloads, and drag/drop files are scanned before parsing. A scan failure blocks admission. "
                "Downloaded evidence is quarantined; owner files are left untouched but refused. Defensive response is block, quarantine, "
                "preserve evidence, alert, and recover - never retaliation."),
              p("No scanner proves every novel file harmless, and no app can remain trustworthy after full operating-system compromise. "
                "The architecture therefore layers type checks, archive limits, hashing, network validation, untrusted-content labels, no automatic execution, and least authority."),
              PageBreak()]

    story += [p("10. Verification and release evidence", "h1")]
    release_rows = [
        ["CHECK", "RESULT"],
        ["Automated regression", f"{release.get('tests_passed', 'unknown')} passed; {release.get('tests_failed', 'unknown')} failed; {release.get('tests_errors', 'unknown')} errors; {release.get('tests_skipped', 'unknown')} skipped; {release.get('seconds', 'unknown')} seconds"],
        ["Brain nodes independently", "PASS" if mesh.get("independent", {}).get("passed") else "FAIL"],
        ["Team contracts", "PASS" if mesh.get("team", {}).get("passed") else "FAIL"],
        ["Critical brain failures", ", ".join(mesh.get("independent", {}).get("critical_failures", [])) or "none"],
        ["Memory tamper integrity", f"{memory.get('valid', 0):,} / {memory.get('checked', 0):,} valid"],
        ["Learning alignment", alignment.get("status", "unknown")],
        ["Registered tools", f"{runtime.get('tools', 0):,}"],
        ["Zero-cost mode", runtime.get("zero_cost", {}).get("mode", "unknown")],
    ]
    story += [_table(release_rows, [2.1 * inch, 4.8 * inch]),
              p("A passing finite suite supports the stated contracts but does not prove all future inputs correct. Hardware, websites, models, and Windows state can change after this evidence date."),
              p("Reproduction", "h2")]
    story += bullets([
        "./.venv/Scripts/python.exe -m pytest -q",
        "./.venv/Scripts/python.exe scripts/doctor.py",
        "Inside HAYAT: /brain check",
        "Inside HAYAT: /brain benchmark",
        "Inside HAYAT: /security audit",
    ])
    story += [PageBreak(), p("11. Limitations and roadmap", "h1")]
    story += bullets([
        "HAYAT uses an existing pretrained foundation, recorded in config/models.yaml; identity instructions and adapters are distinct from foundation training.",
        "Brain-mesh timing measures coordination, not consciousness or general intelligence.",
        "Private fine-tuning remains gated until consent, clean data, isolated splits, a local checkpoint, baseline, candidate evaluation, rollback, and explicit approval exist.",
        "Public sites may block automation or require login; HAYAT does not bypass access controls.",
        "A satellite integration requires an actual documented service contract and cannot be a universal remote-control feature.",
        "OCR, speech, vision, extraction, and model output remain fallible; evidence and verification reduce but do not eliminate error.",
    ])
    story += [p("Recommended next work", "h2")]
    story += bullets([
        "Expand held-out task sets for each model and each brain node.",
        "Add connector-specific simulators and command allowlists before any high-consequence remote API.",
        "Add a bounded Operator event timeline using existing event IDs and sanitized summaries.",
        "Retune retrieval only when quality and latency benchmarks show a repeatable improvement.",
        "Promote model or adapter candidates only after regression, safety, contamination, and rollback gates pass.",
    ])
    story += [Spacer(1, 10), p("References", "h2")]
    references = [
        "Project architecture: docs/architecture.md",
        "Detailed operator report: docs/HAYAT-OPERATOR-ARCHITECTURE-REPORT.md",
        "Brain mesh implementation: app/brain_mesh.py",
        "Operator GUI: app/gui.py",
        "Live evidence: data/benchmarks/architecture/latest.json",
        "Microsoft Defender CLI: https://learn.microsoft.com/en-us/defender-endpoint/command-line-arguments-microsoft-defender-antivirus",
    ]
    story += bullets(references)

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return output


def main() -> int:
    if not EVIDENCE.is_file():
        raise SystemExit(
            f"Missing {EVIDENCE}. Run scripts/architecture_report_evidence.py after the full regression."
        )
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    written = build(evidence)
    print(json.dumps({"written": str(written), "bytes": written.stat().st_size}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
