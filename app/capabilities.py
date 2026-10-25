"""Everything HAYAT can do, in plain words, with the tools behind each part.

One catalog serves three readers:

* a beginner, who sees each area, what it is for, phrases to try, and
  whether it is ready or needs a setup step (the "What can HAYAT do?" window);
* HAYAT itself, which answers "what can you do?", "help" and "can you ...?"
  from this catalog and the live registry instead of guessing; and
* the planner, which uses the same words to offer the right tools, so every
  registered tool is reachable from ordinary language.

Every registered tool belongs to an area. A test fails if one does not, so a
tool added later cannot become a power HAYAT has but does not know about.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from typing import Any
from app.revenue_catalog import CATALOG as REVENUE_TOOLS


@dataclass(frozen=True)
class Capability:
    key: str
    title: str
    icon: str
    summary: str
    how: str
    examples: tuple[str, ...]
    #: Exact tool names, or prefixes ending in "*".
    tools: tuple[str, ...] = ()
    #: Words people use for this area; whole words or short phrases.
    keywords: tuple[str, ...] = ()
    #: A window that helps with this area: a HayatGUI method name, "view:<name>" or "studio:<name>".
    opens: str = ""
    opens_label: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


CATALOG: tuple[Capability, ...] = (
    Capability('networking', 'Private networking', 'N',
               'Organize contacts privately, compare stated expertise with your goals, prepare unsent introductions and track follow-ups.',
               'Open Studios > Private networking > Find professional contacts for public name/organization sources and team routes. '
               'Select deep for broader bounded discovery, or Public web through Tor for a required local Tor route. '
               'Add facts you know or public professional sources; record contact consent, '
               'inspect matching evidence and review a draft. Nothing is sent automatically.',
               ('Show my private networking contacts', 'Find saved contacts who can help with Python projects',
                'Draft an introduction to a saved contact'),
               tools=('networking_*', 'public_contact_search'), keywords=('networking', 'saved contacts', 'collaboration', 'introductions',
                                                 'outreach', 'follow-ups', 'friendship', 'professional network',
                                                 'professional contacts', 'team contacts', 'directory'),
               opens='_open_networking', opens_label='Private networking'),
    Capability('replica', 'Replica app studio', 'R',
               'Plan and generate original app code with HAYAT, then run local parity, image, contrast, review, brand and listing checkers.',
               'Open Studios > Replica app studio. Choose a project, prepare a workflow and inspect missing prerequisites. '
               'Generate bounded slices with HAYAT, then verify the generated app; deploy prepares a checklist.',
               ('Replica workflows', 'Prepare replica recon in data/work/replica-app',
                'Run replica recon in data/work/replica-app: Plan an original offline task list'),
               tools=('replica_*',), keywords=('replica', 'app studio', 'clone app', 'feature parity', 'brand sweep'),
               opens='_open_replica_workspace', opens_label='Replica app studio'),
    Capability('invention', 'Invention and executive reasoning', '◇',
               'Develop testable ideas with goals, a skeptical critic and saved experiment rounds, then work the '
               'formula out and simulate it.',
               'Open Invention, create a goal and optionally supply numeric acceptance cases, then run bounded cycles. '
               'With cases, Work it out fits standard shapes without a model, unknown constants in a proposal are '
               'fitted, and Simulate sweeps each input, ranks what matters most and runs a Monte Carlo of every case.',
               ('Create invention project: design a low-cost water monitor', 'List invention projects',
                'Prefrontal cortex status', 'Dual brain status'),
               tools=('invention_*', 'prefrontal_cortex_status', 'dual_brain_status'),
               keywords=('invention', 'invent', 'prototype', 'prefrontal', 'dual brain', 'simulate', 'simulation',
                         'work it out', 'fit a formula', 'sensitivity', 'monte carlo'),
               opens='view:invention', opens_label='Invention Workbench'),
    Capability('learning_atlas', 'Learning atlas', '◈',
        'Explore twelve fields, find prerequisite gaps, and track your assessed understanding and review dates.',
        'Open Studios → Learning atlas. Select a topic, prepare a lesson or quiz, then press Send.',
        ('Show learning roadmap', 'Show learning atlas', 'Help me understand everything'),
        keywords=('roadmap', 'education', 'understanding', 'understand everything', 'curriculum', 'learning atlas'),
        opens='_open_learning_atlas', opens_label='Learning atlas'),
    Capability(
        'public_intelligence', 'Global intelligence workspace', '🌍',
        'Public feeds, about 37,200 cameras with video in chat, maps, weather, alerts, launches, satellites and ISS passes, fires, satellite images, directions and bike share.',
        'Ask "Play live camera in Delaware in chat" for a public stream, then "Pause camera", "Next camera" or "Stop camera". After "Find public cameras in London", say "play" or "play the second one". Dead streams are skipped; London plays clips and New York refreshing stills, labelled as not live. Global intelligence also provides maps, publisher directories, weather and retained evidence. Everyday questions work too: planes over a city, rain tomorrow, earthquakes or storms near a place, space weather, a world brief, and watching a place for a hazard. Also: "next rocket launch", "when can I see the ISS?", "radar over Chicago", "satellite image of Dubai", "fire hotspots near Los Angeles", "directions from Detroit to Ann Arbor", "bike share near Paris", "what plane is UAE1".',
        ('Play live camera in Delaware in chat', 'Next camera', 'Stop camera', 'Show global intelligence', 'Open world atlas', 'Find public cameras in London', 'Which planes are flying over Dubai?', 'Will it rain in Lahore tomorrow?', 'Any earthquakes near Tokyo right now?', 'Give me a world brief', 'Watch Istanbul for earthquakes', 'Show camera directories', 'Recall intelligence knowledge about floods', 'Analyze intelligence in Europe', 'Refresh intelligence feeds',
         'What new sources did you find?', 'Discover new camera sources', 'When is the next rocket launch?', 'When can I see the ISS?',
         'Show me the weather radar over Chicago', 'Satellite image of Dubai', 'Any fire hotspots near Los Angeles?',
         'Directions from Detroit to Ann Arbor', 'Bike share near Paris', 'What plane is UAE1?', 'Deep look at Denver',
         'Deep analysis of Chicago', 'Which fires are growing near Los Angeles?'),
        tools=('intelligence_*', 'discovery_*', 'space_launches', 'satellites_overhead', 'satellite_pass', 'aircraft_info', 'weather_map',
               'satellite_image', 'fire_hotspots', 'fire_clusters', 'route_directions', 'bike_share', 'traffic_count'), keywords=('global intelligence', 'discovery', 'new sources', 'discover', 'intelligence feeds', 'intelligence alerts',
            'osiris', 'earthquake', 'natural events', 'space weather', 'watch region', 'zoom', 'pan', 'world situation', 'event revisions', 'cctv', 'webcam', 'camera', 'street map', 'world atlas', 'weather', 'source recall', 'planes', 'flights', 'air traffic', 'forecast', 'rain', 'storm', 'hurricane', 'wildfire', 'flood', 'world brief',
            'rocket', 'launch', 'satellite', 'iss', 'space station', 'radar', 'directions', 'route', 'bike share', 'fire hotspots', 'traffic',
            'deep look', 'sitrep', 'situation report', 'growing fires'),
        opens='_open_intelligence', opens_label='Global intelligence',
    ),
    Capability(
        'markets', 'Markets & Trade Center', '📈',
        'Crypto and stocks streamed live, charts with expected ranges and a measured track record, world events by industry, '
        'alerts. Read-only: it never trades.',
        'Ask "bitcoin price", "show me the ETH chart for the week", "biggest crypto gainers today", "bitcoin fees" or "eth gas", '
        '"add solana to my watchlist", or "alert me when bitcoin goes above 90000". Open the Trade Center for the full market. '
        'Stocks too: "apple stock price", "how are stocks doing". "Bitcoin forecast" gives expected ranges with HAYAT’s own '
        'track record, never a promise. "Will bitcoin go up next year" or "bitcoin possibilities" measures every period from '
        '1 hour to 1 year; "compare my watchlist over a month" ranks markets. "Which signals work for bitcoin" tests ten '
        'signals on its own past and says which survive the luck test; "what drives bitcoin" shows what it moves with and '
        'splits today’s move. The Trade Center has a 3D view of markets, odds and links. "Which markets are affected by '
        'today’s events" lines events up with industries. '
        '"Buy", "sell", "swap" or "send" crypto or stocks is always stopped and explained.',
        ('Bitcoin price', 'How much is ETH?', 'Show me the bitcoin chart', 'Biggest crypto gainers today', 'Bitcoin order book',
         'Bitcoin fees', 'ETH gas', 'Web3 stats', 'Add solana to my watchlist', 'Alert me when bitcoin goes above 90000',
         'Apple stock price', 'How are stocks doing?', 'Bitcoin forecast', 'Which markets are affected by today’s events?',
         'What are the chances bitcoin goes up this week?', 'Why is NVDA down?', 'How is world trade?',
         'When is the next fed meeting?', 'Any rumors about NVDA?', 'Will bitcoin go up next year?',
         'Compare my watchlist over a month', 'Which signals work for bitcoin?', 'What drives bitcoin?'),
        tools=('market_*',), keywords=('crypto', 'bitcoin', 'btc', 'ethereum', 'eth', 'coin', 'token', 'price', 'chart',
            'order book', 'trade center', 'trading', 'market', 'watchlist', 'price alert', 'gas', 'fees', 'mempool', 'web3',
            'blockchain', 'solana', 'dogecoin', 'stock', 'stocks', 'shares', 'forecast', 'outlook', 'prediction', 'industry',
            'sector', 'live price', 'scenarios', 'chances', 'odds', 'probability', 'analysis', 'why is', 'world trade',
            'shipping', 'chokepoint', 'trade routes', 'fed meeting', 'fomc', 'economic calendar', 'earnings', 'jobs report',
            'rumor', 'rumors', 'rumour', 'rumours', 'market news', 'crypto news', 'headlines', 'possibilities', 'time period',
            'long term', 'next year', 'next month', 'signals', 'backtest', 'predictable', 'correlated', 'correlation',
            'drives', 'what moves', '3d'),
        opens='_open_markets', opens_label='Trade Center',
    ),
    Capability(
        'accounts', 'Accounts & sign-in', '🔑',
        'Connect your own Google, Microsoft, GitHub, LinkedIn, Notion and Slack accounts, then read your mail, calendar, files, '
        'repositories and channels. Sending or creating anything waits for your confirmation.',
        'Open Studios → Accounts & sign-in and pick a way to sign in, easiest first: a code you approve on the provider’s '
        'page, your browser, or a pasted token. HAYAT never sees your password. Then ask "check my gmail", "my outlook '
        'calendar", "my github notifications", "search notion for invoices" or "which accounts are connected?".',
        ('Check my gmail', 'Which accounts are connected?', 'My outlook calendar', 'My github notifications',
         'Search notion for invoices', 'Connect my google account'),
        tools=('account_*',), keywords=('account', 'accounts', 'sign in', 'sign-in', 'login', 'log in', 'connect', 'gmail',
            'outlook', 'google calendar', 'onedrive', 'google drive', 'github', 'notion', 'slack', 'linkedin', 'webhook', 'oauth',
            'token', 'inbox'),
        opens='_open_accounts', opens_label='Accounts & sign-in',
    ),
    Capability(
        'jobs', 'Jobs & applications', '💼',
        'Find jobs on public boards, read your CV, score each fit with the reasons, prepare applications on its own, track '
        'them, and work out what to learn to reach more of them. Sending waits for your confirmation.',
        'Say "read my CV from C:\\path\\cv.pdf" (or use Studios → Jobs), then "find data analyst jobs", "show my job matches", '
        '"show job 12", "what should I learn", "find hidden jobs at Stripe", "what are my chances", "prepare an application for '
        'job 12" and "send my application for job 12". Say "I will relocate" to keep on-site roles elsewhere. Every few hours '
        'HAYAT searches on its own and tells you about strong matches; it never sends an application without your confirmation.',
        ('Find python jobs', 'Show my job matches', 'What should I learn', 'Find hidden jobs at Stripe',
         'What are my chances', 'Prepare an application for job 3'),
        tools=('job_*',), keywords=('jobs', 'career', 'cv', 'resume', 'résumé', 'job application', 'job applications',
            'my applications', 'apply for', 'apply to job', 'cover letter', 'vacancy', 'vacancies', 'hiring', 'recruiter',
            'job matches', 'job search', 'remote jobs', 'what to learn', 'what should i learn', 'skills to learn',
            'learning plan', 'skill gap', 'hidden jobs', 'careers page', 'company jobs', 'my chances', 'response rate',
            'reply rate', 'my odds', 'relocate', 'willing to relocate'),
        opens='_open_jobs', opens_label='Jobs',
    ),
    Capability(
        'web3', 'Web3 explorer & agentic web', '⛓',
        'Blockchain networks, blocks, any address, transaction or ENS name, DeFi totals, and how ready a website is '
        'for AI agents (Web 4.0). Read-only: nothing is signed or sent.',
        'Ask "look up vitalik.eth", paste a Bitcoin or Ethereum address or transaction hash, "latest bitcoin blocks", '
        '"bitcoin hashrate", "lightning network", "layer 2 gas", "how much is locked in DeFi" or "biggest stablecoins". '
        'For the agentic web: "is stripe.com ready for AI agents?" or "does nytimes.com block AI crawlers?". '
        'Open the Web3 explorer for networks, live blocks, the explorer, DeFi and the agent-web check.',
        ('Look up vitalik.eth', 'Watch vitalik.eth', 'Latest bitcoin blocks', 'Bitcoin hashrate', 'Lightning network', 'Layer 2 gas',
         'How much is locked in DeFi?', 'Biggest stablecoins', 'Is stripe.com ready for AI agents?',
         'Does nytimes.com block AI crawlers?'),
        tools=('web3_*', 'web4_*'), keywords=('web3', 'web 3.0', 'web 4.0', 'web4', 'agentic web', 'explorer', 'address',
            'address balance', 'watch address', 'watched addresses', 'transaction', 'tx', 'ens', 'block', 'blocks', 'hashrate', 'mining', 'lightning', 'layer 2', 'l2',
            'optimism', 'zksync', 'defi', 'tvl', 'stablecoin', 'llms.txt', 'ai crawlers', 'gptbot', 'agent card', 'mcp'),
        opens='_open_web3', opens_label='Web3 explorer',
    ),
    Capability(
        "chat", "Talk and get answers", "💬",
        "Ask anything in your own words. HAYAT answers with an AI model that runs on this computer.",
        "Type in the box at the bottom and press Send, or press Enter.",
        ("Explain how compound interest works, simply",
         "Help me write a polite message asking for a day off",
         "Give me three ideas for a birthday present for my dad"),
        keywords=("explain", "answer", "question", "write", "summarise", "summarize", "chat", "talk", "advice",
                  "idea", "ideas", "help me write"),
    ),
    Capability(
        "voice", "Talk out loud", "🎙",
        "Speak to HAYAT and hear it answer, with push-to-talk or hands-free listening.",
        "Press Start voice below the chat. Hold Ctrl+Space to talk, or turn on hands-free in Voice settings.",
        ("Start voice", "Talk to me in Arabic", "Stop listening"),
        keywords=("voice", "speak", "microphone", "mic", "listen", "hear", "out loud", "say it"),
        opens="studio:voice", opens_label="Voice settings",
    ),
    Capability(
        "research", "Search the internet", "🔎",
        "Look things up on the web, read pages and papers, and report back with sources you can check.",
        "Ask a question about something current, or start with \"Research ...\". Searches run in the "
        "background and report back when done.",
        ("Research the best laptop under 800 dollars",
         "What is the latest version of Python?",
         "Find research papers about sleep and memory"),
        tools=("web_search", "wide_web_search", "onion_research", "onion_navigate", "tor_web_research", "research_browser_handoff", "research_topic", "public_web_fetch", "public_api_get", "public_download", "bounded_crawl",
               "capture_web_page", "github_research", "open_source_search", "paper_research", "learn_public_document",
               "public_document_learning_status", "track_public_source", "check_public_source_change",
               "public_source_profile", "public_person_research", "public_data_sources", "public_data_search", "public_indicator_read",
               "mcp_zero_cost_web_research"),
        keywords=("research", "search", "internet", "web", "online", "latest", "news", "look up", "find out",
                  "paper", "papers", "github", "source", "sources", "current", "today"),
        opens="_open_searches", opens_label="Searches",
    ),
    Capability(
        "memory", "Remember and recall", "🧠",
        "Keep what you tell it and what it learns, bring it back when it matters, and keep a running understanding "
        "of what you are doing, what worked before and what is likely next.",
        "Tell it something to remember, or ask what it knows. Ask \"what am I working on?\" or \"where did we leave "
        "off?\". Open Memory in the left bar to browse.",
        ("Remember that my sister's birthday is on 12 May",
         "What do you remember about my project?",
         "What do you know about solar panels, and where did you learn it?",
         "Where did we leave off?", "What have you learned from mistakes?"),
        tools=("recall_memory_page", "knowledge_search", "knowledge_conflicts", "explain_knowledge",
               "export_knowledge", "import_knowledge", "situation_report"),
        keywords=("remember", "memory", "recall", "know", "knowledge", "forget", "remind me what",
                  "leave off", "working on", "situation", "learned from", "next"),
        opens="_open_knowledge", opens_label="Knowledge Library",
    ),
    Capability(
        "files", "Files and documents", "📁",
        "Read, write, find and organise files, and make PDF or Word documents.",
        "Name the file or folder, or drag a file onto the window for HAYAT to read.",
        ("Make a PDF of my meeting notes",
         "What's in my Downloads folder?",
         "Find files with 'invoice' in the name"),
        tools=("list_folder", "read_file", "write_file", "append_file", "create_folder", "copy_file", "move_file",
               "delete_file", "search_files", "file_exists", "get_file_info", "write_pdf", "write_docx",
               "open_project_folder", "download_public_file", "read_editable_file", "save_edited_file", "edit_file", "write_spreadsheet", "write_presentation", "read_document_for_edit", "read_spreadsheet_for_edit", "edit_document_text", "edit_spreadsheet_cells"),
        keywords=("file", "files", "folder", "document", "pdf", "word", "docx", "save", "read", "rename",
                  "download", "copy", "move", "delete"),
    ),
    Capability(
        "text_analysis", "Analyze text", "🔤",
        "Measure a text's readability, key words, tone and parts of speech, from pasted text or a file, on this computer.",
        "Say \"sentiment of: …\", \"how readable is notes.md\" or \"keywords in report.txt\". \"Set up language tools\" "
        "downloads NLTK's language data once (about 22 MB) so every part is exact.",
        ("Sentiment of: I love this phone but the battery is awful", "How readable is docs/network.md",
         "Keywords in notes.txt", "Set up language tools"),
        tools=("text_analyze", "text_tools_setup"),
        keywords=("sentiment", "readability", "readable", "keywords", "tone", "word count", "text analysis", "nltk"),
    ),
    Capability(
        "media", "Make images and videos", "🎨",
        "Create images, download and play videos in chat, edit clips, and create local preview links.",
        "Describe what you want, or press Create image. Everything runs on this computer.",
        ("Create an image of a sunset over mountains",
         "Make a short video of ocean waves",
         "Describe what happens in this video",
         "Play this YouTube video, then make three clips from it",
         "Trim the second clip from 5 to 20 seconds",
         "Create a link for that video"),
        tools=("generate_image", "generate_video", "image_generation_status", "image_generation_history",
               "video_generation_history", "image_gallery", "image_preview", "rate_generated_media", "video_analyze",
               "video_tools_status", "inspect_video_link", "download_video", "analyze_video_clips",
               "search_play_video", "create_video_clip", "make_video_clips", "video_preview", "play_video", "assess_video", "video_player_control", "edit_video_clip", "share_video_result",
               "media_quality_learning_status"),
        keywords=("image", "picture", "photo", "draw", "art", "logo", "wallpaper", "video", "animation", "gallery",
                  "clips", "clipping", "viral", "reels", "shorts", "youtube"),
        opens="view:gallery", opens_label="Gallery",
    ),
    Capability(
        "senses", "See through the camera and screen", "👁",
        "Look through the webcam or at your screen and say what is there, including reading text.",
        "Ask it to look, or press Eye. The camera only turns on when you ask, and never in privacy mode.",
        ("Turn on the camera and tell me what you see",
         "What's on my screen right now?",
         "Read the text in the picture I copied",
         "Read the text in this screenshot",
         "Describe the image D:/photos/receipt.png"),
        tools=("camera_*", "my_camera_*", "screen_read*", "people_status", "detect_people", "display_status", "display_screenshot", "describe_screen", "describe_image",
               "clipboard_analyze_image", "read_text_from_image", "ocr_status", "enhance_low_light",
               "sensory_fusion_status", "move_active_window_to_display"),
        keywords=("camera", "webcam", "see", "look", "screen", "screenshot", "display", "monitor", "eye", "watch",
                  "image", "picture", "photo", "ocr", "read text", "scan", "extract text",
                  "night vision", "low light", "brighten", "dark photo", "too dark"),
    ),
    Capability(
        'image_files', 'Image files and source evidence', '▧',
        'Inspect technical image metadata, compare local duplicates and verify exact copies on public source pages.',
        'Open Studios → Local image tools for offline inspection/comparison. For publication evidence, use Searches → '
        'Image research, or provide a quoted local image path and a public page in chat. Hash discovery checks '
        'Wikimedia Commons only; it is not an exhaustive web search. Photos are not uploaded or used to identify people.',
        ('Check local image toolkit', 'Inspect local image "D:/photos/source.png"',
         'Compare local images "D:/photos/source.png" and "D:/photos/copy.png"',
         'Find image copies on the web for "D:/photos/source.png"'),
        tools=('local_image_inspect', 'local_image_compare', 'local_image_toolkit_status',
               'image_source_search', 'image_source_check_page'),
        keywords=('local image', 'local images', 'local image toolkit', 'image tools', 'image copies', 'image sources',
                  'duplicate images', 'image metadata', 'phash', 'exif'),
        opens='_open_local_image_tools', opens_label='Open local image tools',
    ),
    Capability(
        "owner_check", "Confirm it's really you", "🔐",
        "Ask Windows Hello to check your fingerprint, face or PIN before a sensitive step. Windows keeps the "
        "biometric and answers only yes or no; HAYAT never sees it, and only you are enrolled.",
        "Say the word, or HAYAT asks at the moment it matters. If Hello isn't set up, it tells you how to add it "
        "in Windows Settings. It confirms you are present; it never looks anyone up.",
        ("Confirm it's me with Windows Hello",
         "Is Windows Hello set up on this machine?",
         "Prove it's me before a sensitive step"),
        tools=("verify_owner_present", "owner_verification_status"),
        keywords=("windows hello", "verify me", "confirm it's me", "confirm its me", "owner check", "prove it's me",
                  "fingerprint", "face unlock", "pin", "biometric", "am i verified"),
    ),
    Capability(
        "people_recognition", "Recognise people you enrol", "🧑",
        "Recognise only the people you enrol with their consent, in your own photos, and flag faces you haven't "
        "enrolled. Sensitive templates use Windows account encryption; close ties stay unnamed. Nobody is looked up online.",
        "Open Studios → Enrolled people (local), choose a local image and explicitly confirm consent before enrollment. "
        "Compare against your gallery; cosine scores are not identity probabilities. Forget removes local templates. "
        "Needs verified OpenCV models ('set up face recognition' downloads them manually).",
        ("Enroll Sam's face for recognition",
         "Which of my enrolled people is in this picture?",
         "Who is enrolled for face recognition?",
         "Forget the enrolled person Sam",
         "Set up face recognition"),
        tools=("face_enroll", "face_recognize", "face_list_enrolled", "face_forget", "face_clear_all",
               "face_recognition_status", "face_models_setup"),
        keywords=("face recognition", "recognise face", "recognize face", "enroll", "enrol", "enrolled people",
                  "who is in this photo", "face gallery", "forget person",
                  "set up face recognition", "face models"),
    ),
    Capability(
        "computer", "Use apps and the computer", "🖱",
        "Open and close programs, read a window, press its buttons and menus by name, type into its boxes, use "
        "the clipboard while you watch, list the devices plugged in, and find where the computer is.",
        "Say which app and what to do. Risky actions ask you first. Location comes from Windows location, or "
        "roughly from your internet address, and it always says which.",
        ("Open Notepad and write a shopping list", "Open Chrome", "Click the Save button in notepad",
         "What buttons are in the calculator app?", "Copy 'see you at 5' to the clipboard",
         "List my devices", "Where am I right now?"),
        tools=("computer_*", "ui_read", "ui_elements", "ui_click", "ui_type", "launch_application", "close_application",
               "discover_applications", "application_state", "notepad_edit", "clipboard_read_text", "clipboard_write_text", "clipboard_status",
               "list_devices", "device_location"),
        keywords=("open", "launch", "app", "application", "program", "click", "type", "mouse", "keyboard",
                  "notepad", "chrome", "clipboard", "close", "devices", "device", "usb", "bluetooth",
                  "plugged in", "location", "located", "loaction", "where am i", "where are we", "gps"),
    ),
    Capability(
        "wifi_sensing", "Wi-Fi sensing (CSI)", "📶",
        "Connect a Wi-Fi channel state information (CSI) device, check it is live, record it, and sense movement.",
        "Plug in an ESP32 running ESP32-CSI-Tool or esp-csi and say \"connect CSI\", or send CSI over UDP from "
        "your own network. Windows gives no CSI from the built-in card, so without a device it reads signal "
        "strength and says so. Calibrate once in a still room for dependable movement answers. Add \"with the "
        "camera\" to cross-check movement; the camera opens only for that check. Studios > Wi-Fi sensing shows it "
        "live.",
        ("Connect CSI", "Is CSI connected?", "Is anyone moving? Check the Wi-Fi sensing",
         "Is anyone moving? Check Wi-Fi sensing with the camera too", "Calibrate CSI", "Show CSI recordings"),
        tools=("csi_*", "sensing_*"),
        opens="_open_sensing", opens_label="Wi-Fi sensing",
        keywords=("csi", "channel state", "channel state information", "wifi sensing", "wi-fi sensing", "esp32",
                  "subcarrier", "rssi", "motion sensing", "presence"),
    ),
    Capability(
        "home_network", "Home network & router", "📡",
        "See the Wi-Fi router you are connected to, its signal and health, nearby Wi-Fi networks, and the devices on your network.",
        "Ask about your router or internet. HAYAT reads Windows and the router's own status page (only the router, "
        "never a password) and changes nothing. Device names need the IEEE maker list, downloaded once when you ask.",
        ("Check my router", "Who is on my wifi?", "Show nearby wifi networks", "How is my internet?",
         "Update device vendor names"),
        tools=("network_*",),
        keywords=("router", "gateway", "modem", "5g signal", "wifi network", "wifi channel", "internet connection",
                  "who is on my wifi", "devices on my network", "lan", "ssid", "latency", "ping"),
        opens="_open_router", opens_label="Router & home network",
    ),
    Capability(
        "browser", "Browse websites for you", "🌐",
        "Open web pages in its own browser, read them, click through, fill in fields, and take screenshots.",
        "Give it a website address and what to do there. Logging in and paying stay with you.",
        ("Go to wikipedia.org and read the main page", "Open youtube.com in the browser",
         "Take a screenshot of example.com"),
        tools=("browser_*",),
        keywords=("website", "browser", "page", "url", "site", "tab", "web page", "scroll"),
    ),
    Capability(
        "act_online", "Act for you online, with your OK", "✅",
        "Fill in sign-up, registration and application forms, and publish your website, only after you confirm.",
        "Ask it to fill a form or publish your site. You see every value and a screenshot first, type any password "
        "yourself, and confirm. Then it acts once, checks the result, and you say whether it is right.",
        ("Fill in the contact form on example.com with my name Mohsin and email me@example.com",
         "Publish my business website", "Show pending actions"),
        tools=("form_fill_prepare", "publish_website_prepare", "pending_actions"),
        keywords=("publish", "register", "registration", "sign up", "signup", "fill", "form", "submit", "apply",
                  "application", "put online", "go live", "confirm", "pending", "online form"),
        opens="_show_confirmation", opens_label="Pending confirmations",
    ),
    Capability(
        "code", "Write and run code", "💻",
        "Write programs, run Python, PowerShell and CMD commands, and explain errors.",
        "Ask for a program, or start with \"Run PowerShell:\" or \"Run CMD:\" for an exact command.",
        ("Write a Python program that renames photos by date", "Run PowerShell: Get-Date",
         "Explain this error: ModuleNotFoundError: No module named 'requests'"),
        tools=("terminal", "powershell", "python_*", "sandbox_run_python", "code_*", "lab_*"),
        keywords=("code", "program", "python", "script", "run", "command", "powershell", "terminal", "cmd", "error",
                  "debug", "traceback"),
    ),
    Capability(
        "messages", "Messages and email", "✉",
        "Keep contacts, and send email or WhatsApp messages from your own accounts.",
        "Save a contact first, then ask HAYAT to send. It only sends when you ask it to.",
        ("Save Ahmed's email as ahmed@example.com", "Email Ahmed that the report is ready",
         "Send a WhatsApp to mom saying I'm on my way"),
        tools=("email_send", "whatsapp_send", "contact_*"),
        keywords=("email", "mail", "whatsapp", "message", "send", "contact", "contacts", "text"),
        opens="_open_business", opens_label="Email setup (Business)",
    ),
    Capability(
        "location_privacy", "Keep your location private", "🛰",
        "See who can learn where you are, and keep HAYAT from giving it away.",
        "HAYAT names places on this computer, holds messages and web requests that would send your precise location, "
        "strips GPS from photos it sends, and rounds positions it sends to public lookups. The check lists apps and "
        "websites allowed your location, what your internet address reveals and photos with GPS.",
        ("Who can see my location?", "Set up private place names", "Remove the location from holiday.jpg",
         "Can anyone track my location?"),
        tools=("location_privacy_check", "location_places_setup", "location_share_once", "photo_location_strip",
               "location_open_setting"),
        keywords=("location privacy", "who can see my location", "track my location", "hide my location",
                  "private place names", "gps from photo", "geotag", "location from photo"),
        opens="_open_location_privacy", opens_label="Location privacy",
    ),
    Capability(
        "reverse_engineer", "Reverse engineer a binary or app", "🔬",
        "Take a program apart and see how it works, all on this computer.",
        "HAYAT uses REA (an open-source reverse-engineering engine) to open a target you own or are authorised to "
        "examine and report what it finds with evidence: native executables, Go/Rust/WASM/.NET/Android, JavaScript "
        "and Electron apps, firmware and captures. Ask it to open a file, then list the functions or strings, "
        "decompile one function, search, or trace a feature. Opening, extracting, running or editing notes wait for "
        "your confirmation; plain inspection does not.",
        ("Reverse engineer C:\\apps\\thing.exe", "What's inside app.asar", "List the functions", "Decompile main",
         "Show me the strings", "Reverse-engineering tools"),
        tools=("reverse_engineer_status", "reverse_engineer_tools", "reverse_engineer_overview", "reverse_engineer_read",
               "reverse_engineer_act", "reverse_engineer_link"),
        keywords=("reverse engineer", "reverse engineering", "decompile", "disassemble", "pseudocode", "binary",
                  "executable", "malware", "apk", "electron", "asar", "dll", "firmware", "strings in", "take apart"),
        opens="_open_reverse_engineer", opens_label="Reverse engineering",
    ),
    Capability(
        "privacy_masks", "Masks: Tor and a masked browser", "🎭",
        "Layers that hide where your connection comes from, run by HAYAT itself: Tor and a masked browser.",
        "Tor covers HAYAT's own web reading: set it up once (21 MB), then mask HAYAT; it is remembered. The masked "
        "browser is your own Edge or Chrome in a separate profile through that Tor, for your browsing. No VPN app is "
        "installed; one that is connected is still checked for DNS and IPv6 leaks.",
        ("How untrackable am I?", "Mask HAYAT with Tor", "Open masked browser", "Set up Tor"),
        tools=("privacy_masks_status", "tor_setup", "hayat_mask_on", "hayat_mask_off", "masked_browser_open"),
        keywords=("tor", "mask", "untrackable", "leak test", "dns leak", "ipv6 leak", "masked browser", "browse anonymously",
                  "anonymous", "hide my ip", "vpn"),
        opens="_open_location_privacy", opens_label="Location privacy & masks",
    ),
    Capability(
        "email_check", "Check an email address's setup", "🛡",
        "Check an address's mail provider, SPF, DMARC and DKIM, and whether it is printed on its website.",
        "Give the address. An address has no location and its holder is never traced; for your own address "
        "(\"check my email address ...\") the check also says where you are now.",
        ("Check my email address me@mydomain.com", "Check the mail setup of sales@example.com",
         "Is my email me@mydomain.com set up correctly?"),
        tools=("email_address_check",),
        keywords=("spf", "dmarc", "dkim", "mx record", "deliverability", "spam folder", "mail setup",
                  "check my email", "set up correctly", "where is my email"),
    ),
    Capability(
        "money", "Earn and manage money", "💰",
        "Start a business or service, price it, get a website and marketing kit, find clients, invoice them, "
        "prepare digital products for sale, and track actual income and withdrawal records.",
        "Open Business → Products to create and refine deliverables, build a ZIP, and verify delivery. "
        "Say \"Help me start a business\", or open Business and its Launch tab. Or just ask about invoices, clients, "
        "hours, leads or your wallet. In chat, start with Customer NAME: their inquiry, then ask for the next customer action.",
        ("Create an invoice for Acme for 3 hours at 40 dollars", "Show my wallet",
         "Find freelance gigs for logo design and track them as leads", "Help me start a business",
         "Make a website for my business", "Calculate my hourly rate for 2000 dollars a month at 20 hours a week",
         "Show my income streams", "Show my business brief", "Do a deep review of my business", "When will I get paid?",
         "Add 5000 to my wallet from HDFC savings", "Create a payment QR for INV-2026-0001",
         "What should I do with my money?", "Can I afford a 300 dollar laptop?", "Set aside 25% of income for tax",
         "Find solar installers in Austin, Texas and make a lead list"),
        tools=("business_*", "client_*", "customer_*", "time_*", "quote_*", "invoice_*", "expense_*", "lead_*", "income_*",
               "wallet_*", "product_*", "venture_*", "service_*", "rate_calculator", "landing_page_create",
               "marketing_kit_create", "find_prospects", "money_method*", "payment_request"),
        keywords=("money", "earn", "income", "invoice", "invoices", "client", "clients", "customer", "prospect", "prospects", "lead list", "leads", "inquiry", "business", "wallet",
                  "withdraw", "withdrawal", "sell", "product", "freelance", "gig", "expense", "profit", "quote",
                  "payment", "paid", "start a business", "side hustle", "service", "services", "launch", "marketing",
                  "rate", "pricing", "website for my business", "make money", "cash flow", "late payers", "hourly rate",
                  "pipeline", "earning opportunities", "deep review", "add money", "upi", "payment link", "payment qr",
                  "bank account", "money methods", "money decisions", "emergency fund", "hayat wallet", "set aside",
                  "can i afford"),
        opens="_open_business", opens_label="Business",
    ),
    Capability(
        "portfolio", "Portfolio & net worth (USD)", "🏦",
        "Everything you own in US dollars: cash, stocks and crypto at your own bank, broker and exchange, valued "
        "live, with gains and net worth by day. HAYAT holds and trades nothing.",
        "Record what you hold, like \"I have 2500 USD in my Chase account\" or \"I bought 10 AAPL shares at 180 on "
        "Schwab\", then say \"show my portfolio\". A public Bitcoin or Ethereum address can be read from the chain: "
        "\"add address bc1… to my portfolio\". Or open Portfolio & net worth from the Studios menu.",
        ("Show my portfolio", "What is my net worth?", "I hold 0.25 BTC at 58000 on Coinbase",
         "I have 2500 USD in my Chase account", "I sold 2 AAPL at 210"),
        tools=("portfolio_*",),
        keywords=("portfolio", "net worth", "holdings", "my stocks", "my crypto", "how much am i worth", "assets",
                  "cost basis", "unrealised", "unrealized"),
        opens="_open_portfolio", opens_label="Portfolio & net worth",
    ),
    Capability(
        "growth", "Freelance, sales and revenue workflows", "📈",
        "Find public opportunities, score supplied evidence, build local drafts and bundles, track experiments and report recorded revenue.",
        "Give HAYAT your actual skills, brief, source URLs or measurements. Drafts stay local; outbound campaigns require review and email setup.",
        ("Find freelance jobs for Python automation", "Show my revenue dashboard", "Show my sales pipeline",
         "Find income ideas for Python and spreadsheets", "Show Osiris playbook"),
        tools=tuple(tool.name for tool in REVENUE_TOOLS) + ('revenue_tool_catalog',),
        keywords=("freelance", "sales", "pipeline", "revenue", "outreach", "proposal", "retainer", "affiliate",
                  "subscription", "sponsorship", "conversion", "profitability", "lead", "pricing", "experiment",
                  "osiris", "income ideas", "income plan", "income actions", "unit economics", "sales funnel"),
        opens="_open_revenue", opens_label="Revenue workspace",
    ),
    Capability(
        "languages", "Learn languages", "🗣",
        "Learn to read, write, listen to and speak Arabic, read the Quran with Roman-English transliteration, and "
        "chat or hear replies in other languages.",
        "Say \"Teach me Arabic\", or open Learn a language from the Studios menu. Say \"Read Surah Al-Fatiha\" to "
        "read the Quran from the published text, then \"continue quran\".",
        ("Teach me Arabic", "Talk to me in Arabic", "Which languages can you speak?",
         "Read Surah Al-Fatiha in Roman English", "Continue quran"),
        tools=("quran_read", "quran_continue"),
        keywords=("arabic", "language", "languages", "learn arabic", "translate", "teach me", "lesson", "speak in",
                  "quran", "surah", "ayah", "transliteration", "roman english"),
        opens="_open_learn", opens_label="Learn a language",
    ),
    Capability(
        "software", "Install apps and add-ons", "📦",
        "Find and install programs, Python packages and AI models, after you say yes.",
        "Ask for the app by name. HAYAT shows what it will install and waits for your yes.",
        ("Install VLC", "Is numpy installed?", "Find a smaller AI model for coding"),
        tools=("install_package", "uninstall_package", "check_package", "package_status", "find_software",
               "install_software", "find_model", "pull_model"),
        keywords=("install", "uninstall", "software", "package", "winget", "model", "models", "add-on", "update"),
    ),
    Capability(
        "connect", "Connect other services", "🔌",
        "Link services such as GitHub through connectors and MCP servers, with keys kept in the Windows "
        "credential store.",
        "Ask what connectors are available, then enable the one you want.",
        ("Show available connectors", "List my MCP servers", "Is any secret stored for my connectors?"),
        tools=("connector_*", "mcp_enable_server", "mcp_read_tool", "mcp_register_server", "mcp_servers", "mcp_tools",
               "mcp_write_tool", "secret_broker_status"),
        keywords=("connect", "connector", "connectors", "integration", "account", "mcp", "notion", "service"),
    ),
    Capability(
        "automation", "Automate and remind", "⏰",
        "Set rules that react to events, and schedule jobs to run later or every day.",
        "Start with \"Every ...\", \"Remind me ...\" or \"When ...\".",
        ("Remind me every day at 9 to drink water", "Show my automation rules",
         "When a download finishes, tell me"),
        tools=("add_proactive_rule", "proactive_rules"),
        keywords=("remind", "reminder", "schedule", "every", "daily", "automate", "automation", "rule", "when"),
    ),
    Capability(
        "health", "Check itself and stay healthy", "🩺",
        "Check its own parts, models and resources, explain its tools, and back up its data.",
        "Ask for a self check, or open Health or Brain 5D.",
        ("Run a self check", "What tools do you have?", "Back up your database", "What can't you do?",
         "How do you work?", "Which tool would you use to make an invoice?"),
        tools=("system_status", "get_agent_state", "explain_my_tools", "audit_tool_contracts", "brain_mesh_*", "metacognition_status",
               "incident_snapshot", "create_database_backup", "model_delegation_map", "my_capabilities", "about_hayat"),
        keywords=("health", "status", "check", "backup", "back up", "diagnose", "brain", "self check", "tools",
                  "capabilities", "working"),
        opens="view:operator", opens_label="Brain 5D",
    ),
    Capability(
        "learning", "Keep learning", "📚",
        "Learn new topics in the background while you are away, and review what it learned.",
        "Ask it to learn about something, or ask what it learned today.",
        ("What did you learn today?", "Learn about electric cars in the background",
         "Show your learning plan"),
        tools=("adaptive_learning_curriculum", "queue_adaptive_learning", "run_adaptive_review"),
        keywords=("learn", "learning", "study", "improve", "background", "curriculum", "review"),
    ),
)

BY_KEY = {capability.key: capability for capability in CATALOG}

#: Asking for the whole picture rather than about one ability.
OVERVIEW_QUESTION = re.compile(
    r"(?i)^(?:please\s+)?(?:what\s+(?:can|could)\s+you\s+do|what\s+are\s+you\s+(?:capable\s+of|able\s+to\s+do)|"
    r"what\s+(?:are|is)\s+your\s+(?:capabilities|abilities|features|powers|skills)|help|help\s+me|"
    r"show\s+me\s+what\s+you\s+can\s+do|how\s+(?:do|can)\s+i\s+use\s+you|what\s+can\s+i\s+ask(?:\s+you)?|"
    r"i(?:'?m|\s+am)\s+(?:new|a\s+beginner)(?:\s+here)?|getting\s+started|where\s+do\s+i\s+start|what\s+do\s+you\s+do)"
    r"(?:\s+(?:here|hayat|for\s+me|exactly))?[\s?.!]*$")


def _matches(pattern: str, name: str) -> bool:
    return name.startswith(pattern[:-1]) if pattern.endswith("*") else name == pattern


def tools_in(capability: Capability, names: list[str] | set[str]) -> list[str]:
    return sorted(name for name in names if any(_matches(pattern, name) for pattern in capability.tools))


def classify(names: list[str] | set[str]) -> tuple[dict[str, list[str]], list[str]]:
    """(tools per capability, tools that belong to no capability)."""
    grouped = {capability.key: tools_in(capability, names) for capability in CATALOG}
    placed = {name for tools in grouped.values() for name in tools}
    return grouped, sorted(set(names) - placed)


def match(text: str, limit: int = 3) -> list[Capability]:
    """The areas a request or question is about, best first, by the words people use."""
    lowered = " " + " ".join(re.findall(r"[a-z0-9'@.-]+", (text or "").lower())) + " "
    scored = []
    for capability in CATALOG:
        score = 0
        for word in capability.keywords:
            if f" {word} " in lowered or (len(word) > 4 and f" {word}" in lowered):
                score += 2 if " " in word else 1
        if score:
            scored.append((score, CATALOG.index(capability), capability))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [capability for _score, _index, capability in scored[:limit]]


def readiness(capability: Capability, runtime: Any) -> tuple[str, str]:
    """("ready" | "setup" | "off", a plain note on what to do). Cheap: no network, no model call."""
    authority = getattr(runtime, "authority", None)
    privacy = bool(getattr(authority, "privacy_mode", False))
    key = capability.key
    if key == 'invention':
        if getattr(runtime, 'invention', None) is None:
            return 'off', 'The invention workbench is unavailable in this runtime.'
        if getattr(runtime, 'model', None) is None:
            return 'setup', 'Saved projects are available. Start a local HAYAT model to generate and review proposals.'
        return 'ready', 'Create a goal in Invention and run bounded research and experiment cycles.'
    if key == "chat":
        model = getattr(runtime, "model", None)
        if model is None:
            return "setup", "No AI model is running. Start Ollama (setup does this), then restart HAYAT."
        return "ready", f"Using {getattr(model, 'model', None) or 'the local model'}."
    if key == "replica":
        if getattr(runtime, "model", None) is None:
            return "setup", "Workflow preparation and offline checkers are ready. Start HAYAT's model to generate app files."
        return "ready", "Uses HAYAT's configured model. Generated apps still need actual tests before deployment."
    if key == "voice":
        voice = getattr(runtime, "voice", None)
        if voice is None:
            return "off", "Voice is not available in this session."
        return "ready", "Press Start voice to begin."
    if key == "senses":
        if privacy:
            return "off", "Privacy mode is on, so the camera and screen are not used. Turn it off to use them."
        return "ready", "The camera only turns on when you ask."
    if key == "messages":
        email = getattr(runtime, "email", None)
        if email is not None:
            try:
                status = email.status()
            except Exception:
                status = {"ready": False}
            if not status.get("ready"):
                return "setup", ("WhatsApp works if WhatsApp Desktop is installed. For email, open Business and "
                                 "press Email setup.")
        return "ready", "It only sends when you ask it to."
    if key == "money":
        business = getattr(runtime, "business", None)
        if business is None:
            return "off", "The business books are not available in this session."
        try:
            payout = business.payout()
        except Exception:
            payout = {"configured": True}
        if not payout.get("configured"):
            return "ready", "Ready. Set up a withdrawal method in Business, Wallet, whenever you like."
        return "ready", "Ready."
    if key == "code":
        if shutil.which("docker") is None:
            return "ready", "Ready. The isolated sandbox needs Docker Desktop, which is not installed."
        return "ready", "Ready."
    if key == "act_online":
        publisher = getattr(runtime, "publisher", None)
        if publisher is not None and not publisher.ready():
            return "ready", ("Forms are ready. To publish your website, connect Netlify in Business, Launch, Publishing "
                             "setup. Every action waits for your confirmation.")
        return "ready", "Every action waits for your confirmation."
    if key in {"research", "browser"}:
        return "ready", "Needs an internet connection."
    if key == "software":
        return "ready", "Nothing is installed without your yes."
    if key == "connect":
        return "ready", "Each service needs its own one-time setup."
    return "ready", "Ready."


def describe(capability: Capability, runtime: Any = None, names: list[str] | None = None) -> dict[str, Any]:
    names = names if names is not None else (sorted(runtime.registry.names()) if runtime is not None else [])
    state, note = readiness(capability, runtime) if runtime is not None else ("ready", "")
    return {"key": capability.key, "title": capability.title, "icon": capability.icon,
            "summary": capability.summary, "how": capability.how, "examples": list(capability.examples),
            "state": state, "note": note, "tools": tools_in(capability, names), "opens": capability.opens_label}


def overview(runtime: Any) -> str:
    """The plain answer to "what can you do?"."""
    names = sorted(runtime.registry.names())
    lines = [f"Here is everything I can do on this computer ({len(names)} tools behind it). "
             "Just ask in your own words.", ""]
    for capability in CATALOG:
        state, note = readiness(capability, runtime)
        flag = "" if state == "ready" else ("  [needs setup]" if state == "setup" else "  [off]")
        lines.append(f"{capability.icon} {capability.title}{flag}: {capability.summary}")
        lines.append(f"    Try: \"{capability.examples[0]}\"")
        if state != "ready":
            lines.append(f"    {note}")
    lines += ["", "Open Help, What can HAYAT do?, to see every area with more examples and a Try button. "
              "Ask \"can you ...?\" about anything and I'll check what I actually have."]
    return "\n".join(lines)


def can_you(question: str, runtime: Any) -> str | None:
    """Answer "can you X?" from the catalog, or None when no area matches."""
    found = match(question, limit=2)
    if not found:
        return None
    names = sorted(runtime.registry.names())
    lines = []
    for capability in found:
        state, note = readiness(capability, runtime)
        tools = tools_in(capability, names)
        opening = "Yes." if state == "ready" else ("Yes, after a setup step." if state == "setup" else "Not right now.")
        lines.append(f"{opening} {capability.icon} {capability.title}: {capability.summary}")
        lines.append(f"  How: {capability.how}")
        lines.append("  Try: " + " | ".join(f"\"{example}\"" for example in capability.examples[:2]))
        if note and note != "Ready.":
            lines.append(f"  {note}")
        if tools:
            # The tools for what was actually asked first: "invoices" lists invoice_* before business_*.
            words = {word.rstrip("s") for word in re.findall(r"[a-z]{3,}", question.lower())}
            tools = sorted(tools, key=lambda name: (-sum(word in name for word in words), name))
            lines.append(f"  Behind it: {len(tools)} tool{'s' if len(tools) != 1 else ''} "
                         f"({', '.join(tools[:5])}{', ...' if len(tools) > 5 else ''}).")
        lines.append("")
    return "\n".join(lines).rstrip()


def register_capability_tools(registry: Any, runtime_supplier: Any) -> None:
    """Let HAYAT read its own catalog while planning, not only when asked."""

    def my_capabilities(area: str = "") -> dict[str, Any]:
        runtime = runtime_supplier()
        names = sorted(registry.names())
        if area:
            chosen = BY_KEY.get(area) or next(iter(match(area, limit=1)), None)
            if chosen is None:
                return {"found": False, "areas": [capability.key for capability in CATALOG]}
            return {"found": True, **describe(chosen, runtime, names)}
        return {"areas": [describe(capability, runtime, names) for capability in CATALOG],
                "tool_count": len(names)}

    registry.register(
        "my_capabilities",
        "Read the plain-language catalog of everything this assistant can do: each area, what it is for, "
        "example requests, whether it is ready or needs setup, and the tools behind it. Pass an area or a few "
        "words to get one area. Use it to answer questions about your own abilities and to pick tools.",
        {"type": "object", "properties": {"area": {"type": "string"}}, "required": [], "additionalProperties": False},
        "low", "system_read", my_capabilities,
    )


#: Common assistant abilities HAYAT may be assumed to have, and the tool-name
#: fragments that would provide them. Asked about breakfast, HAYAT offered to
#: "check your calendar or emails for clues" -- it has no tool for either.
_COMMONLY_ASSUMED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("read the owner's email inbox", ("inbox", "email_read", "read_email", "imap", "mail_read")),
    ("read or change a calendar", ("calendar",)),
    ("make or answer phone calls", ("phone_call", "call_phone", "voip")),
    ("read text messages or WhatsApp chats", ("whatsapp_read", "sms_read", "read_messages")),
    ("track the owner's location history", ("location_history",)),
)


#: How each commonly assumed ability is named in a reply, so a true "I can't
#: read your inbox" is never mistaken for a false denial (app.tools.introspection).
_ASSUMED_SUBJECTS = {
    "read the owner's email inbox": r"\b(?:inbox|e-?mails?|mail\s+app|gmail|outlook)\b",
    "read or change a calendar": r"\b(?:calendar|appointments?|schedule)\b",
    "make or answer phone calls": r"\b(?:phone\s+calls?|call\s+(?:him|her|them|you)|voicemails?)\b",
    "read text messages or WhatsApp chats": r"\b(?:text\s+messages?|sms|whatsapp\s+(?:chats?|messages?)|dms?)\b",
    "track the owner's location history": r"\blocation\s+history\b",
}


def missing_abilities(names: list[str] | set[str]) -> list[str]:
    """Commonly assumed abilities this registry genuinely lacks."""
    names = list(names)
    return [label for label, fragments in _COMMONLY_ASSUMED
            if not any(fragment in name for name in names for fragment in fragments)]


def about_missing_ability(text: str, names: list[str] | set[str]) -> bool:
    """Whether a sentence is about something HAYAT really cannot do here."""
    return any(re.search(_ASSUMED_SUBJECTS[label], str(text or ""), re.IGNORECASE)
               for label in missing_abilities(names) if label in _ASSUMED_SUBJECTS)


def abilities_note(names: list[str] | set[str]) -> str:
    """One paragraph for the reply writer: what HAYAT can offer, and what it must never offer."""
    names = list(names)
    missing = [label for label, fragments in _COMMONLY_ASSUMED
               if not any(fragment in name for name in names for fragment in fragments)]
    note = ("Offer follow-up actions only within HAYAT's real abilities: "
            + "; ".join(capability.title for capability in CATALOG) + ". ")
    if missing:
        note += "HAYAT has no tool to " + ", ".join(missing) + ", so never offer or claim those. "
    return note
