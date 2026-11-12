# Licence audit (2026-11-08)

The owner's standing rule: HAYAT is built only from genuinely open-source software (OSI-approved
licences), at no cost, with no mandatory subscription, account, trial or telemetry. "Free to use" is
not the same as open source, so every licence below is the one the component itself declares.

Websites HAYAT reads (job boards, search engines, public feeds, research catalogues) are information
sources, not software HAYAT ships. Using them does not make HAYAT depend on proprietary software;
each one is read only where its robots.txt and terms allow (see [jobs](jobs.md#where-jobs-come-from-2026-11-08)
and [Global intelligence](global-intelligence.md)).

## Result

| Area | Result |
| --- | --- |
| Python packages (all six requirements files, 73 installed) | All declare an OSI-approved licence (MIT, BSD, Apache-2.0, PSF, LGPL, GPL, MPL-2.0, Unlicense, MIT-CMU). No paid package, no account needed. |
| Local engines in `runtime/` | Open source, except the NVIDIA CUDA runtime DLLs bundled with the GPU engines (see below). |
| Models | Apache-2.0 where a licence is recorded; one installed advisor model records none (see below). |
| Bundled data and imagery | Public domain or open licences, with sources and hashes in each folder's manifest. |
| Paid services | None. Keyed or subscription search APIs are blocked in code (`BraveSearch` refuses); job sources that put a key in the address are not used. |
| Telemetry | HAYAT sends none. Every reply says what left the computer ([outbound](../app/outbound.py)). |

Re-run the package check at any time: `.\.venv\Scripts\python.exe -m app.licence_audit` (exit code 1
if any installed package lacks an OSI-approved licence; `--write` refreshes the table below).
`tests/test_licence_audit.py` fails the test suite if a new dependency is not open source.

## Local engines (`runtime/`)

| Engine | Licence | Notes |
| --- | --- | --- |
| Ollama (model server) | MIT | The only required model server. LM Studio (free, not open source) is mentioned only as an optional alternative in one start-up hint; nothing needs it. |
| llama.cpp (`llama-export`) | MIT | Model conversion and export. |
| Colibri (`colibri-v2.0.0`) | Apache-2.0 | Bundles `cudart64_12.dll` (below). |
| Eclipse Temurin Java 21 | GPL-2.0 with Classpath Exception | For Ghidra and JADX. |
| Ghidra, JADX, WABT | Apache-2.0 | Reverse-engineering tools. |
| REA (morluto/rea) | MIT | Reverse-engineering engine. |
| FFmpeg | LGPL-2.1+ / GPL-2.0+ (build-dependent) | Audio and video. |
| VLC (libvlc) | LGPL-2.1+ / GPL-2.0+ | Video playback. |
| Node.js | MIT | yt-dlp's challenge solver. |
| Tesseract OCR | Apache-2.0 | Reading text in images. |
| Tor | BSD-3-Clause | Optional privacy route. |

**Flagged, outside the migration scope:** the NVIDIA CUDA runtime (`cudart64_12.dll`, and the CUDA
libraries Ollama uses) and the NVIDIA display driver are closed source, though free and redistributable.
No open-source alternative runs compute on an NVIDIA GPU under Windows; every engine also runs on
the CPU without them, more slowly. Windows itself, Windows OCR and Windows Hello are likewise
proprietary parts of the operating system that HAYAT uses through open-source wrappers (pywin32,
winsdk); each has a non-Windows or CPU path.

## Models

| Model | Licence | How it was checked |
| --- | --- | --- |
| HAYAT 35B-A3B (from HauhauCS/Qwen3.6-35B-A3B-Uncensored) | Apache-2.0 | `models/huggingface/hayat-35b-a3b-uncensored/LICENSE` and model card |
| hayat:latest / qwen3.5 (Ollama) | Apache-2.0 | `ollama show <model> --license` |
| HAYAT open 4B | Apache-2.0 | `models/huggingface/hayat-open-4b/LICENSE` |
| all-minilm, nomic-embed-text (embeddings) | Apache-2.0 | Ollama licence field / model card |
| Qwythos 9B (Qwen-based fine-tune, advisor only) | **not recorded** | `ollama show qwythos --license` is empty; its Qwen base is Apache-2.0, but the fine-tune's own terms are unverified. It never writes HAYAT's answers. Kept (the owner's rule: never remove uncensored models); flagged here for the owner to confirm. |

## Bundled data and imagery

| Folder | Licence | Source |
| --- | --- | --- |
| `app/assets/earth/` | Public domain | NASA Blue Marble and Black Marble 2012 (via Wikimedia Commons); Natural Earth countries |
| `app/assets/world-land.json` | Public domain | Natural Earth 1:110m land |
| `app/assets/brain/` | CC BY (see its CREDITS.md) | Sketchfab brain model, credited in the view |
| `app/assets/intelligence-map/vendor/` | BSD-2-Clause (Leaflet), Apache-2.0 (hls.js) | Licence files alongside |
| `data/places/places.csv` (installed on request) | CC BY 4.0 | GeoNames cities1000, from the reverse_geocoder package on PyPI |

## Python packages

<!-- packages:start -->
| Package | File | Version | Licence | Open source (OSI) | Source | What it is for |
| --- | --- | --- | --- | --- | --- | --- |
| httpx | requirements.txt | 0.28.1 | BSD-3-Clause | yes | https://github.com/encode/httpx | — |
| yt-dlp | requirements.txt | 2026.8.19 | Unlicense | yes | https://github.com/yt-dlp/yt-dlp | Public video extraction/downloading; default extras include the packaged YouTube JavaScript challenge solver. FFmpeg and Node are local engines. |
| python-vlc | requirements.txt | 3.0.21203 | LGPL-2.1-or-later | yes | https://wiki.videolan.org/PythonBinding | — |
| sgp4 | requirements.txt | 2.27 | MIT | yes | https://github.com/brandon-rhodes/python-sgp4 | Propagate public CelesTrak orbital elements for the ISS position. |
| fastapi | requirements.txt | 0.142.1 | MIT | yes | https://github.com/fastapi/fastapi | Authenticated local/mobile API and its background ASGI server. The mobile reply/status stream is implemented directly with FastAPI WebSockets. |
| uvicorn | requirements.txt | 0.54.0 | BSD-3-Clause | yes | https://uvicorn.dev/ | — |
| psutil | requirements.txt | 7.2.2 | BSD-3-Clause | yes | https://github.com/giampaolo/psutil | Resolves the real netmask for the selected LAN interface so mobile access is restricted to that subnet instead of every virtual/VPN adapter. |
| beautifulsoup4 | requirements.txt | 4.15.0 | MIT | yes | https://www.crummy.com/software/BeautifulSoup/bs4/ | — |
| defusedxml | requirements.txt | 0.7.1 | PSF-2.0 | yes | https://github.com/tiran/defusedxml | Encoding-aware rejection of DTDs/entities in public provider XML. |
| trafilatura | requirements.txt | 2.2.0 | Apache-2.0 | yes | https://trafilatura.readthedocs.io | Public-source OSINT profiles: offline article extraction after bounded retrieval. |
| pypdf | requirements.txt | 6.19.0 | BSD-3-Clause | yes | https://github.com/py-pdf/pypdf | — |
| fonttools | requirements.txt | 4.66.1 | MIT | yes | http://github.com/fonttools/fonttools | lets pypdf read PDFs whose fonts use CFF/Type1 encodings |
| reportlab | requirements.txt | 5.0.1 | BSD | yes | https://www.reportlab.com/ | Architecture/report generation and verification. ReportLab creates the vector PDF; pdfplumber provides an independent text/layout inspection path. |
| pdfplumber | requirements.txt | 0.11.10 | MIT | yes | https://github.com/jsvine/pdfplumber | — |
| PyYAML | requirements.txt | 6.0.3 | MIT | yes | https://pyyaml.org/ | — |
| python-dotenv | requirements.txt | 1.2.3 | BSD-3-Clause | yes | https://github.com/theskumar/python-dotenv | — |
| playwright | requirements.txt | 1.63.0 | Apache-2.0 | yes | https://github.com/Microsoft/playwright-python | — |
| Pillow | requirements.txt | 12.3.0 | MIT-CMU | yes | https://python-pillow.github.io | Window-handle captures prevent unrelated desktop apps contaminating GUI checks. |
| PyAutoGUI | requirements.txt | 0.9.54 | BSD | yes | https://github.com/asweigart/pyautogui | — |
| pyperclip | requirements.txt | 1.11.0 | BSD | yes | https://github.com/asweigart/pyperclip | Portable text clipboard adapter (native OS commands are selected by the package; lack of a graphical clipboard degrades without blocking startup). |
| pywinauto | requirements.txt | 0.6.9 | BSD-3-Clause | yes | https://github.com/pywinauto/pywinauto | Windows desktop UI automation. Windows-only (it needs pywin32/comtypes), so it is skipped on macOS/Linux -- app/windows/applications.py guards its import and th |
| pyserial | requirements.txt | 3.5 | BSD | yes | https://github.com/pyserial/pyserial | Wi-Fi CSI from an ESP32 on a USB serial port (app/csi.py). Pure Python. |
| APScheduler | requirements.txt | 3.11.3 | MIT | yes | https://github.com/agronholm/apscheduler | — |
| jsonschema | requirements.txt | 4.26.0 | MIT | yes | https://github.com/python-jsonschema/jsonschema | — |
| numpy | requirements.txt | 2.5.3 | BSD-3-Clause / 0BSD / MIT / Zlib / CC0-1.0 | yes | https://numpy.org | — |
| scipy | requirements.txt | 1.18.1 | BSD-3-Clause | yes | https://scipy.org/ | Invention workbench: least-squares fitting of formula constants (app/invention_sim.py) and the theory layer's symbolic proofs and unit linear algebra (app/inven |
| sympy | requirements.txt | 1.14.0 | BSD | yes | https://sympy.org | — |
| pyspellchecker | requirements.txt | 0.8.3 | MIT | yes | https://github.com/barrust/pyspellchecker | — |
| sounddevice | requirements.txt | 0.5.6 | MIT | yes | https://github.com/spatialaudio/python-sounddevice/ | — |
| faster-whisper | requirements.txt | 1.2.1 | MIT | yes | https://github.com/SYSTRAN/faster-whisper | — |
| pynput | requirements.txt | 1.8.2 | LGPL-3.0 | yes | https://github.com/moses-palmer/pynput | Portable global push-to-talk listener. It is loaded only when push-to-talk starts; headless servers continue to run without a desktop/input backend. |
| piper-tts | requirements.txt | 1.8.0 | GPL-3.0-or-later | yes | http://github.com/OHF-voice/piper1-gpl | Voice capture reliability, pre-roll, adaptive VAD, bounded normalization, and device probing use these existing local packages; no cloud speech or always-record |
| pystray | requirements.txt | 0.19.5 | LGPL-3.0 | yes | https://github.com/moses-palmer/pystray | — |
| openwakeword | requirements.txt | 0.6.0 | Apache-2.0 | yes | https://pypi.org/project/openwakeword | Optional low-power wake word (voice.wake_word.engine: openwakeword). Its models are downloaded on request by scripts/setup_wakeword.py, not here. |
| pytest | requirements.txt | 9.1.1 | MIT | yes | https://docs.pytest.org/en/latest/ | — |
| pytest-timeout | requirements.txt | 2.4.0 | MIT | yes | https://github.com/pytest-dev/pytest-timeout | Per-test timeouts. Without it a hung test blocks forever rather than failing: one GUI test opened a modal dialog nobody could answer and left a pytest process r |
| ruff | requirements.txt | 0.16.9 | MIT | yes | https://docs.astral.sh/ruff | — |
| opencv-python-headless | requirements.txt | 5.0.0.93 | Apache-2.0 | yes | https://github.com/opencv/opencv-python | — |
| prompt_toolkit | requirements.txt | 3.0.53 | BSD | yes | https://github.com/prompt-toolkit/python-prompt-toolkit | — |
| winsdk | requirements.txt | 1.0.0b10 | MIT | yes | https://github.com/pywinrt/python-winsdk | — |
| pywin32 | requirements.txt | 312 | PSF-2.0 | yes | https://github.com/mhammond/pywin32 | Windows APIs used for the clipboard, window handles, active-window tracking and process inspection: app/ imports win32api, win32clipboard, win32con, win32gui an |
| pytesseract | requirements.txt | 0.3.13 | Apache-2.0 | yes | https://github.com/madmaze/pytesseract | OCR. The Python side only drives the engine; the engine itself is a separate install (`winget install UB-Mannheim.TesseractOCR`), which lands in %LOCALAPPDATA%\ |
| tkinterdnd2 | requirements.txt | 0.6.3 | MIT | yes | https://github.com/Eliav2/tkinterdnd2 | Drag-and-drop of files onto the window. Bundles the tkdnd Tcl extension. Note the ordering constraint it brings: tkdnd initialises OLE2, which needs COM in a si |
| python-docx | requirements.txt | 1.2.0 | MIT | yes | https://github.com/python-openxml/python-docx | Document Intelligence Suite. These are runtime features, not optional test conveniences: dropped Word, Excel and PowerPoint files are extracted and indexed for  |
| openpyxl | requirements.txt | 3.1.5 | MIT | yes | https://openpyxl.readthedocs.io | — |
| python-pptx | requirements.txt | 1.0.2 | MIT | yes | https://github.com/scanny/python-pptx | — |
| pandas | requirements.txt | 3.0.6 | BSD | yes | https://pandas.pydata.org | — |
| lxml | requirements.txt | 6.1.3 | BSD-3-Clause | yes | https://lxml.de/ | — |
| mss | requirements.txt | 10.2.0 | MIT | yes | https://github.com/BoboTiG/python-mss | Secure connectors and extension/MCP bridges. The built-in zero-cost web MCP reuses MCP, HTTPX, BeautifulSoup, and the existing safe-retrieval stack. |
| keyring | requirements.txt | 25.7.0 | MIT | yes | https://github.com/jaraco/keyring | — |
| cryptography | requirements.txt | 50.0.1 | Apache-2.0 / BSD-3-Clause | yes | https://github.com/pyca/cryptography | Local mTLS remote access: X.509 CA/server/device certificates, encrypted PKCS#8 private keys, password-protected PKCS#12 device bundles, and CRLs. |
| websockets | requirements.txt | 17.1 | BSD-3-Clause | yes | https://github.com/python-websockets/websockets | — |
| mcp | requirements.txt | 2.2.0 | MIT | yes | https://modelcontextprotocol.io | — |
| watchdog | requirements.txt | 6.0.0 | Apache-2.0 | yes | https://github.com/gorakhargosh/watchdog | — |
| rapidfuzz | requirements.txt | 3.14.6 | MIT | yes | https://github.com/rapidfuzz/RapidFuzz | — |
| langid | requirements.txt | 1.1.6 | BSD | yes | https://github.com/saffsd/langid.py | Voice supervision and conditional multi-decode use Faster Whisper plus bounded standard-library control. Technical alias/sound-alike expansion and tamper digest |
| torch | requirements-perception.txt | 2.14.0 | Apache-2.0 / Apache-2.0 / BSD-2-Clause / BSD-3-Clause / BSL-1.0 / MIT | yes | https://pytorch.org | Optional CPU person detection, matching the versions verified on this host. Windows OCR/accessibility dependencies remain in requirements.txt. |
| torchvision | requirements-perception.txt | 0.29.0 | BSD | yes | https://github.com/pytorch/vision | — |
| ImageHash | requirements-image-tools.txt | 4.3.2 | BSD-2-Clause | yes | https://github.com/JohannesBuchner/imagehash | Optional local image toolkit. Windows x64 wheels for CPython 3.12-3.15. Install with scripts/setup_image_tools.py --install; existing base dependencies are reus |
| ExifRead | requirements-image-tools.txt | 3.5.1 | BSD | yes | https://github.com/ianare/exif-py | — |
| PyWavelets | requirements-image-tools.txt | 1.10.0 | MIT / BSD-3-Clause | yes | https://github.com/PyWavelets/pywt | — |
| python-dotenv | requirements-training.txt | 1.2.3 | BSD-3-Clause | yes | https://github.com/theskumar/python-dotenv | Optional, local-only HAYAT adapter-training environment. Install inside an isolated environment. Production training still requires the dataset/evaluation gate; |
| PyYAML | requirements-training.txt | 6.0.3 | MIT | yes | https://pyyaml.org/ | — |
| psutil | requirements-training.txt | 7.2.2 | BSD-3-Clause | yes | https://github.com/giampaolo/psutil | — |
| torch | requirements-training.txt | 2.14.0 | Apache-2.0 / Apache-2.0 / BSD-2-Clause / BSD-3-Clause / BSL-1.0 / MIT | yes | https://pytorch.org | — |
| transformers | requirements-training.txt | 5.17.0 | Apache-2.0 | yes | https://github.com/huggingface/transformers | — |
| peft | requirements-training.txt | 0.20.0 | Apache-2.0 | yes | https://github.com/huggingface/peft | — |
| trl | requirements-training.txt | 1.14.0 | Apache-2.0 | yes | https://github.com/huggingface/trl | — |
| bitsandbytes | requirements-training.txt | 0.50.2 | MIT | yes | https://github.com/bitsandbytes-foundation/bitsandbytes | — |
| datasets | requirements-training.txt | 5.0.1 | Apache-2.0 | yes | https://github.com/huggingface/datasets | — |
| accelerate | requirements-training.txt | 1.15.0 | Apache-2.0 | yes | https://github.com/huggingface/accelerate | — |
| safetensors | requirements-training.txt | 0.8.0 | Apache-2.0 | yes | https://github.com/huggingface/safetensors | — |
| coqui-tts | requirements-voice-cloning.txt | 0.27.5 | MPL-2.0 | yes | https://github.com/idiap/coqui-ai-TTS | Optional local XTTS voice matching. Install into an environment supported by Coqui/PyTorch; this file is deliberately outside portable bootstrap defaults. A sep |
<!-- packages:end -->
