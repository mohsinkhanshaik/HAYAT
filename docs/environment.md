# Windows environment inventory

> Revalidated on 2026-08-17 by the runtime doctor. Hardware and installed software can change; rerun the listed commands for live truth.

## Advanced multimodal milestone

- Local API-cost mode: `ZERO-COST`; no paid/cloud fallback is enabled.
- Ollama models detected: `all-minilm:latest`, `qwen:latest`, `gemma4:latest`, `llama3:latest`, `qwen3.5:latest`.
- GPU: NVIDIA GeForce RTX 5060 Laptop GPU, 8151 MiB VRAM (7429 MiB free during doctor).
- System RAM: 42.6 GB total; 21.2 GB available during doctor.
- Displays: two (1920x1200 primary and 3840x2160 secondary).
- Camera: one Integrated Camera; real 1280x720 frame captured/released/deleted in 3016 ms.
- Default audio: Realtek microphone array and Realtek speakers detected.
- Project storage: about 5.55 GB used at the last scan; 3 TB project ceiling, dedicated 1 TB learning ceiling, and 300 GB physical free-space reserve.
- OCR: Windows Media OCR is ready; Python/OpenCV capture is installed; Tesseract is not installed.
- Regression/doctor: 985 automated tests passed for this milestone and the strict runtime doctor reported ready. Re-run both for live evidence after future changes.

Inventory captured on **2026-08-12** from the local machine. Commands were run from `D:\AI`; no software was assumed to be present.

## Operating system and hardware

| Component | Detected value |
|---|---|
| Windows | Microsoft Windows 11 Home, 64-bit, version 10.0.26200 (build 26200) |
| PowerShell | Windows PowerShell 5.1.26100.8737 (`powershell.exe`); PowerShell 7 (`pwsh`) not found |
| CPU | AMD Ryzen 9 9955HX, 16 physical cores / 32 logical processors |
| RAM | 42,603,331,584 bytes (about 39.7 GiB) |
| GPU | NVIDIA GeForce RTX 5060 Laptop GPU, 8,151 MiB VRAM, driver 610.62 |
| Secondary GPU | AMD Radeon(TM) Graphics |
| D: disk | 4,000,768,323,584 bytes total; 3,713,633,202,176 bytes free (about 3.38 TiB free) |

`nvidia-smi` was used for the NVIDIA VRAM value; the Windows CIM `AdapterRAM` field is not reliable for adapters with larger memory sizes.

## Developer tools

| Tool | Status | Version / path |
|---|---|---|
| Python | Installed | Python 3.14.2 at `C:\Python314\python.exe` |
| Python launcher | Installed | Python 3.14.2 at `C:\Windows\py.exe` |
| pip | Installed | pip 25.3 for Python 3.14 |
| Git | Installed | 2.55.0.windows.2 |
| Node.js | Installed | v24.18.0 |
| npm | Installed | 12.0.1 |
| Docker CLI | Installed | 29.1.3, build f52814d |
| Docker engine | Not reachable during inspection | Docker Desktop was not running or its engine was unavailable |

## Browsers

| Browser | Status | Version / path |
|---|---|---|
| Google Chrome | Installed | 151.0.7922.109 at `C:\Program Files\Google\Chrome\Application\chrome.exe` |
| Microsoft Edge | Installed | 151.0.4129.78 at `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe` |
| Mozilla Firefox | Not found | No executable detected in the usual install locations or on PATH |

## Local AI runtimes

### Ollama

Ollama 0.32.9 is installed at `C:\Users\mohsin khan\AppData\Local\Programs\Ollama\ollama.exe`.

Models reported by `ollama list`:

- `qwen:latest` (2.3 GB)
- `gemma4:latest` (9.6 GB)
- `llama3:latest` (4.7 GB)
- `qwen3.5:latest` (6.6 GB)

Live `/api/show` capability inspection reported vision support for both `qwen3.5:latest` and `gemma4:latest`. Gemma's recorded vision probe was faster, but on 2026-09-30 it described only a dark texture in a test clip where Qwen3.5 read the visible logo and text, so `qwen3.5:latest` is now the configured primary. The route fails closed if Ollama or the selected model is unavailable.

### LM Studio

LM Studio is installed at `C:\Users\mohsin khan\AppData\Local\Programs\LM Studio\LM Studio.exe`. Its `lms` CLI is available at `C:\Users\mohsin khan\.lmstudio\bin\lms.exe` (CLI commit `71bd99c`). At inspection time, LM Studio reported eight local models/assets totaling 66.58 GB, with `google/gemma-4-e4b` loaded. The exact local model inventory is machine state and should be re-detected with `lms ls` or the local OpenAI-compatible `/v1/models` endpoint.

## Implementation implications

- The project can use Ollama immediately, subject to its local server being active.
- LM Studio support should use its OpenAI-compatible endpoint and discover model IDs instead of hard-coding them.
- Playwright can use the installed Chrome or Edge channels; its own Chromium download is optional.
- Python 3.14 is new enough that dependencies must be verified by installing and importing them in the project virtual environment.
- Docker is optional for this project and is not required for the initial agent.

## Voice hardware and local speech stack

Voice inspection on **2026-08-12** found:

| Component | Detected value |
|---|---|
| Default microphone | Microphone Array (Realtek(R) Audio) |
| Default speaker | Speakers (Realtek(R) Audio) |
| Additional output | U28E590 (NVIDIA High Definition Audio) |
| Windows voices | Microsoft David Desktop; Microsoft Zira Desktop |
| FFmpeg | Installed through the WinGet link |
| Local STT | faster-whisper 1.2.1, CTranslate2 4.8.1, ONNX Runtime 1.28.0 |
| Audio capture/playback | sounddevice 0.5.5, NumPy 2.5.2 |
| Tray | pystray 0.19.5 |

The default microphone captured 0.75 seconds at 16 kHz. A full speaker-to-air-to-microphone test spoke the filesystem command, captured 5.95 seconds, transcribed **“Create a text file called voice-test.txt containing hello from my AI.”** in 1,472 ms, created the exact path reported by the current action, verified `hello from my ai`, and issued the concise TTS response. Audible quality cannot be judged programmatically, but synthesis and selected-device playback completed without a driver or API error.

The configured `qwen3.5:latest` route also analyzed a real 1920×1200 Windows screenshot and returned a non-empty description containing the visible AI workspace and Notepad text. Independent image inspection confirmed those main elements, although the model mislabeled the Codex interface as a similar development environment; vision is operational but not assumed infallible.

## Internet research verification

Research inspection on **2026-08-13** confirmed outbound HTTPS, DNS, public HTML, public JSON APIs, DuckDuckGo HTML search, Bing RSS fallback, and Playwright rendering. A real fast research run performed two searches, attempted five pages, retrieved five official Python sources (292,149 bytes), produced citations, and persisted knowledge in about 3.0 seconds. The official GitHub API returned the `python/cpython` repository and README. A same-domain documentation crawl retrieved three bounded pages with no errors.

The project environment now includes Beautiful Soup 4.15.0 and pypdf 6.15.0 for structured HTML and PDF extraction. Network availability, public provider markup, robots policies, and rate limits remain external runtime conditions and must be re-detected rather than assumed.

The 2026-08-13 knowledge-engine verification also inspected `python/cpython` through the official GitHub API (including tags/releases, tree, documentation, and 20 workflow entries), parsed all 15 pages of the public “Attention Is All You Need” arXiv PDF, and confirmed opt-in content-hash monitoring against `https://example.com/`. The paper contains no explicit limitations section, so that field correctly remains unavailable rather than being inferred.

## Intelligence, voice, Windows, and storage revalidation

On **2026-08-13**, the unified request boundary passed 100 representative intent cases with 100% intent accuracy, 0% false actions, 0% unnecessary planning, and 0% wrong/missing tool routing. The common-tool benchmark passed 12/12, the deliberately contradictory verifier benchmark detected 6/6 false-success states, and the integrated behavior benchmark passed 10/10 categories.

The 2026-08-13 physical speaker-to-air-to-default-microphone recheck captured 5.95 seconds at a 0.970 peak and completed transcription in 1,693 ms. It recognized the requested phrase `my AI` as `Maya`; the agent correctly created and verified the resulting file, but the strict exact-content hardware check failed. This is recorded as a real speech-recognition limitation, not a passing result. The deterministic transcript-to-agent voice test passed, including local Windows SAPI playback. A separate disposable-window check wrote and read back two Notepad lines, detected the unsaved dialog, discarded only after an explicit test choice, closed the exact target handle, and preserved the pre-existing Notepad window.

The 2026-08-13 model benchmark measured four installed Ollama models. Generation ranged from 41.72 to 72.53 tokens/second, and observed loaded VRAM ranged from 3,226,342,521 to 5,490,081,790 bytes. The mode benchmark measured 0.009 ms mean deterministic routing, 33.945 ms hybrid-memory retrieval, 11.837 seconds FAST local conversation, and 13.161 seconds DEEP local conversation. These are point-in-time measurements; model loading and current GPU state materially affect them.

The neural phase installed the official Ollama `all-minilm:latest` embedding-only model (about 46 MB download). Live capability inspection reported embedding support, 384 dimensions, and 512-token context. A loaded resource probe observed about 26.7 MB VRAM and no CPU spill reported by Ollama. Its 100-case hybrid retrieval MRR was 0.8942. Model warmth probes measured qwen3.5 at 9.237 seconds cold/0.485 seconds warm and llama3 at 6.337/0.356 seconds.

The runtime doctor reported all inspected subsystems ready. Storage, disk, and network values are point-in-time observations and should be rechecked with `/doctor` or `/storage`.

## 2026-08-17 durable-learning and desktop revalidation

The governed project ceiling is 3 TB, including a dedicated 1 TB learning category, while preserving 300 GB of physical free space. At revalidation, the project used about 5.55 GB and learning used about 11.7 MB. A real restricted background cycle completed public research and persisted claim/evidence records; the queue then reported learned/pending/failed states across restart. Expired research cache cleanup removed 114 rows while deleting zero evidence.

The desktop GUI constructed and ran at 1120×760 with the microphone and learner disabled for the smoke test, then shut down cleanly. The Windows workstation was locked, so screen capture returned black and visual appearance was not claimed as hardware-verified. Voice hardware results remain the earlier measured results above.
