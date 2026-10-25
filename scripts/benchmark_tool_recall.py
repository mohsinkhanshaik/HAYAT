"""Does the right tool reach the planner's shortlist when a request is worded naturally?

The planner shows the brain only a shortlist of HAYAT's ~400 tools. If the
right tool is not on that list, no amount of model intelligence can choose it.
This measures shortlist recall on everyday phrasings that avoid the tools'
own names, and the shortlist size (smaller is cheaper and less distracting).

    .\\.venv\\Scripts\\python.exe scripts/benchmark_tool_recall.py [--out data/benchmarks/tool_selection/recall.json]

A case passes when any one of its acceptable tools is on the shortlist.
Builds the runtime without a model, background services or the owner's
memory database.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: (request, acceptable tools). Worded the way people talk, not the way tools are named.
CASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("turn this recording into a few short highlights", ("make_video_clips", "clip_generator", "create_video_clip")),
    ("how many hours of work haven't I charged anyone for yet", ("time_unbilled",)),
    ("who still hasn't paid me", ("invoice_list",)),
    ("remind Sarah politely that her bill is overdue", ("payment_reminder",)),
    ("any quakes or storms near Tokyo right now", ("intelligence_around", "intelligence_event", "intelligence_alerts")),
    ("show me live traffic cameras near the Golden Gate bridge", ("intelligence_cameras", "intelligence_best_camera")),
    ("which planes are flying over Dubai", ("intelligence_aircraft",)),
    ("what text did I just grab with ctrl+c", ("clipboard_read_text",)),
    ("put the meeting notes in a word document", ("write_docx",)),
    ("make a slide deck about our third quarter results", ("write_presentation",)),
    ("back up the database before I change anything", ("create_database_backup",)),
    ("is my computer healthy, check cpu and memory", ("system_status",)),
    ("find a program on this pc for editing photos", ("discover_applications", "find_software")),
    ("get VLC on this machine", ("install_software",)),
    ("is the requests library already there for python", ("check_package",)),
    ("what am I working on lately", ("situation_report",)),
    ("draw a cat astronaut", ("generate_image",)),
    ("tell mom on whatsapp that I'm running late", ("whatsapp_send",)),
    ("mail the report to Ali", ("email_send",)),
    ("save Ahmed's number 0501234567", ("contact_save",)),
    ("I'm starting work on the Acme job now, clock me in", ("time_timer_start",)),
    ("which of my customers actually make me money", ("client_profitability",)),
    ("write a proposal for the website redesign job", ("generate_proposal", "proposal_builder")),
    ("check how well example.com shows up on google", ("seo_audit", "google_profile_audit")),
    ("find me remote python gigs", ("find_freelance_jobs",)),
    ("what's on my second screen", ("display_screenshot",)),
    ("look through the webcam and tell me what you see", ("camera_observe", "camera_capture")),
    ("read me surah al fatiha", ("quran_read",)),
    ("how well are your parts working together", ("brain_mesh_status", "brain_mesh_self_check")),
    ("how reliable are your tools", ("explain_my_tools",)),
    ("tell me when the EU AI act page changes", ("track_public_source", "check_public_source_change")),
    ("try this python snippet somewhere safe", ("sandbox_run_python",)),
    ("is it going to rain in Lahore", ("intelligence_weather",)),
    ("find the cheapest used iPhone 13", ("price_comparison_engine",)),
    ("set up a weekly email for my bakery's customers", ("newsletter_builder",)),
    ("where am I right now", ("device_location",)),
    ("what's plugged into this pc", ("list_devices",)),
    ("what happened near Karachi over the last week", ("intelligence_history", "intelligence_around")),
    ("get the newest qwen model onto this machine", ("pull_model", "find_model")),
    ("show me the pictures you made yesterday", ("image_gallery", "image_generation_history")),
    ("note down that I spent 40 dollars on printer ink", ("expense_record",)),
    ("what would make my landing page convert better", ("conversion_optimizer",)),
)


#: Written after the index was built and measured once, never tuned against:
#: the honest estimate of how it does on wording nobody planned for.
HELD_OUT: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("how is the air traffic over London looking", ("intelligence_aircraft",)),
    ("will it be windy in Chicago tomorrow", ("intelligence_weather",)),
    ("what did I copy a moment ago", ("clipboard_read_text",)),
    ("knock out a quick PowerPoint on solar panels", ("write_presentation",)),
    ("paint me a sunset over the sea", ("generate_image",)),
    ("text my brother on whatsapp that dinner is at 8", ("whatsapp_send",)),
    ("stop the clock on the Acme job", ("time_timer_stop",)),
    ("who owes me money right now", ("invoice_list",)),
    ("log a 15 dollar taxi ride as a business cost", ("expense_record",)),
    ("snap a photo with the webcam", ("camera_capture", "camera_observe")),
    ("what's showing on my other monitor", ("display_screenshot",)),
    ("put Firefox on this computer", ("install_software",)),
    ("is numpy installed", ("check_package",)),
    ("find contract work as a data analyst", ("find_freelance_jobs",)),
    ("turn this long video into tiktok-sized pieces", ("make_video_clips", "clip_generator", "create_video_clip")),
    ("which clients bring in the most profit", ("client_profitability",)),
    ("look up Sara's email address in my contacts", ("contact_find",)),
    ("any wildfires or floods near Sydney", ("intelligence_around", "intelligence_event", "intelligence_alerts")),
    ("what's my machine's ram and disk looking like", ("system_status",)),
    ("show the images you generated last week", ("image_gallery", "image_generation_history")),
)


def build_planner():
    import os

    # Every database under data/ (memory, events, authority, tool history) goes to a
    # throwaway folder, exactly as in the test suite; the owner's files are never opened.
    os.environ.setdefault("HAYAT_TEST_SANDBOX", tempfile.mkdtemp(prefix="tool-recall-"))
    import tests.conftest  # noqa: F401  (installs the sqlite redirection)
    import app.runtime as runtime_module
    from app.agent.planner import Planner

    runtime = runtime_module.build_runtime(use_model=False, start_background_services=False,
                                           interactive_approvals=False)
    return runtime, Planner(runtime.registry)


def evaluate(planner, cases=CASES) -> dict:
    names = set(planner.registry.names())
    details, sizes = [], []
    for request, acceptable in cases:
        present = [tool for tool in acceptable if tool in names]
        shortlist = set(planner._candidate_tools(request))
        sizes.append(len(shortlist))
        details.append({"request": request, "acceptable": present,
                        "found": sorted(shortlist & set(present)), "shortlist_size": len(shortlist),
                        "passed": bool(shortlist & set(present))})
    passed = sum(item["passed"] for item in details)
    return {"cases": len(details), "recall": round(passed / len(details), 3),
            "median_shortlist": sorted(sizes)[len(sizes) // 2], "max_shortlist": max(sizes),
            "registered_tools": len(names), "misses": [item["request"] for item in details if not item["passed"]],
            "details": details}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    runtime, planner = build_planner()
    try:
        report = {"tuning": evaluate(planner), "held_out": evaluate(planner, HELD_OUT)}
    finally:
        runtime.close()
    summary = {part: {key: value for key, value in result.items() if key != "details"}
               for part, result in report.items()}
    print(json.dumps(summary, indent=2))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
