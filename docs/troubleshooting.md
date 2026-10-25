# Troubleshooting

Start with:

```powershell
cd D:\AI
.\.venv\Scripts\python.exe scripts\doctor.py
.\.venv\Scripts\python.exe -m pip check
```

## I'm locked out of my own remote page

You should not be: loopback and your LAN subnet are trusted and never banned. If it happens (for example you connect from a new outside network in Anywhere mode and mistype the passphrase), open the **Security** window on the desktop and press **Lift ban**, or delete `data\security\intrusion-bans.json` and restart. Correct passphrases clear the score.

## A search seems stuck, or HAYAT changes the subject

Searches run in the background: HAYAT says it is still searching and posts the result as its own message when done. Ask **did you find something?** for the real status, **cancel the search** to stop it, or open **Searches** on the toolbar. If a lesson keeps answering your messages, say **stop lesson**; a lesson now pauses by itself when you talk about something else.

## Popups appear when I press Ctrl+Space

Before 2026-09-30, the push-to-talk hotkey also reached the focused application. HAYAT now swallows it while push-to-talk is on. If popups still appear, check that `voice.push_to_talk_exclusive` is `true` and that voice is running in push-to-talk mode.

## Hands-free keeps listening after I stop talking

Update to the current version: the noise floor now adapts to steady background noise, and each utterance is capped at `voice.microphone.max_utterance_seconds`. In a very noisy room, raise `voice.microphone.release_ratio` (for example to 0.3) or use push-to-talk.

## HAYAT answers in Arabic, or reads Arabic letter by letter

Say **English please**. Replies are English unless you asked for Arabic, and Arabic text is spoken by the Arabic voice (`python -m scripts.setup_arabic`), never spelled out by the English one.

## Something failed without an error message

**Symptom:** a feature quietly does nothing, such as no spoken reply or a camera light that stays on. **Check:** open `data\logs\hayat.log`. HAYAT writes an error it handled there, once a minute at most, with its traceback. For more detail, restart with `$env:HAYAT_LOG_LEVEL = "DEBUG"`.

## PowerShell script will not run

**Symptom:** execution-policy error. **Cause:** local policy blocks scripts. **Check/fix:**

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\start.ps1
```

Use process scope only; do not disable system policy.

## Python or virtual environment

**Symptom:** `python` missing or `.venv` import errors. **Check:** `py -0p`, `Get-Item .\.venv\Scripts\python.exe`. **Fix:** install a supported Python from Python.org if absent, then rerun `setup.ps1`. For a broken environment, preserve diagnostic output before recreating `.venv`.

## Ollama offline or model missing

```powershell
ollama list
Invoke-RestMethod http://localhost:11434/api/tags
```

Start Ollama, then use `/models`. Do not enable paid fallback. If a model is missing, inspect its size/license/RAM/VRAM fit before `ollama pull`.

## Out of VRAM or model too large

**Symptoms:** slow generation, load error, GPU OOM. **Check:** `/system`, `nvidia-smi`, `/models route TEXT`. Close unrelated GPU applications, use FAST mode or a smaller installed model. Do not download a larger model as a first fix.

## Microphone or transcription

```text
/voice devices
/voice status
/voice probe
```

```powershell
.\.venv\Scripts\python.exe -m app.voice.test_microphone
```

If `/voice probe` reports default input `-1`, Windows has exposed no recording endpoint and HAYAT correctly refuses to claim it is listening. Check Device Manager for an audio controller error, install only the matching OEM/chipset/audio package, reboot, and probe again. If an input exists but text is wrong, use the default array microphone, reduce noise, speak after activation, or adjust the local model/language. Raw recordings are not retained unless explicitly configured.

## Speaker/TTS

```powershell
.\.venv\Scripts\python.exe -m app.voice.test_speaker
```

Check `/voice devices`, output selection, Windows volume, and mute state. Push-to-talk interrupts speech by design.

## Camera unavailable

```text
/camera status
/camera devices
```

```powershell
.\.venv\Scripts\python.exe scripts\camera_hardware_check.py
```

Close applications already using the camera and check Windows privacy settings. `/camera off` releases the handle. OCR unavailable is distinct from camera unavailable.

## Browser or Playwright

**Symptom:** launch/browser executable error. **Check:** `/doctor`, then:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

Use only if the browser component is missing. Public research can still use HTTP extraction where appropriate.

## Research unavailable or internet offline

Run `/research-status` and `/health`. Verify DNS/HTTPS in PowerShell. The system falls back to cached/validated local knowledge and marks potentially stale information; it must not invent a fetched citation.

## Database locked

Stop duplicate AI processes and scheduled workers, wait for the active transaction, then rerun doctor. Do not delete SQLite files. Back them up while the assistant is stopped before repair.

## Disk low

Use `/storage status` and `/storage largest`. Clean only categories identified as disposable; the 300 GB reserve is intentional. Never mass-delete `data/memory` or `data/knowledge`.

## Strange answer, wrong tool, or false success

Use `/brain`, `/models route TEXT`, `/tasks`, `/events`, and `/eval quick`. Rephrase with explicit success criteria. A false action or success claim is a regression: preserve the task ID and turn it into a test. Do not merely increase model size.

## Troubleshooting flows

```text
Voice fails -> device detected? -> no: Windows privacy/device
                            -> yes: microphone test -> STT config -> agent

Camera fails -> PnP device? -> no: hardware/privacy
                        -> yes: another app using it? -> capture test -> vision/OCR

Agent fails -> doctor -> intent correct? -> plan/tool -> permission -> observed state -> verifier
```

**HAYAT doctor says the model is unavailable.** The doctor (`scripts/doctor.py`) now gives the reason. Usually HAYAT's own model server (port 11435), which starts with HAYAT, is simply not running: run the check while HAYAT is open.
