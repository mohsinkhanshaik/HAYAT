# Voice assistant

The current interface is Voice V2: local Faster Whisper and Piper, shared text/voice context, push/wake/continuous modes, interruption while speaking, echo rejection, device switching, concise source-aware responses, exact-action spoken approvals, and an unattended-operation supervisor. The default wake phrase is `hey hayat`; push-to-talk remains the reliable privacy-preserving default.

The desktop window asks **Start voice for this session?** on every launch.
The microphone stays off until **Allow voice** is pressed. **Not now**, Escape,
and closing the dialog leave it off; the Mic button can start it later.
Choosing voice-only or combined mode at the command line is itself an explicit
request to start the listener.

## 24/7 reliability model

The listener treats microphone, device, and STT failures as recoverable. It records the failure, re-probes configured/default devices, and retries with bounded exponential backoff (0.25 to 30 seconds by default). A separate watchdog restarts a listener only when voice is still explicitly requested. `/voice off`, application shutdown, and privacy mode set a final stop signal, so the watchdog cannot reopen the microphone behind the user's choice. If Windows exposes no recording endpoint, startup fails visibly instead of running a silent retry loop or claiming that listening started.

`/voice status` reports the service start time, uptime, last heartbeat and age, restart/recovery counts, consecutive failures, and the latest error. Voice logs roll over at UTC midnight and log-write failure cannot terminate listening. The device inventory is refreshed periodically to handle USB/Bluetooth changes.

This makes long-running software resilient; it cannot guarantee operation through Windows sleep/reboot, power loss, revoked microphone privacy, a physically disconnected device, or a broken audio driver. Use the optional current-user startup task for reboot recovery and configure Windows power policy separately if true continuous availability is required.

## Architecture

Voice is a local interface around the existing runtime:

```text
microphone -> VAD / push-to-talk -> STT -> shared AssistantService/SessionState
           -> intent router -> existing ManagerAgent -> planner -> permissions -> tools
           -> verifier -> SQLite memory/logs -> short response -> TTS
```

There is no voice-only planner, executor, permission policy, or memory database. Spoken actions have the same project boundary and safety checks as typed goals.

## Setup and startup

```powershell
.\setup.ps1
.\start.ps1                 # desktop GUI with chat, live work state, and voice
.\start.ps1 -Gui            # explicit desktop GUI
.\start.ps1 -Voice          # voice only
.\start.ps1 -Combined       # typed CLI plus background voice
.\start.ps1 -CliOnly        # typed CLI with no microphone listener
.\start.ps1 -Tray           # optional tray plus voice
```

The first transcription downloads Faster Whisper `base.en` locally. Later starts use the ignored cache under `data/models/whisper`.

## Push-to-talk

Push-to-talk is the default and does not continuously record. Hold `Ctrl+Space`, speak, then release. Pressing the hotkey while the assistant speaks stops playback before recording the interruption. Change `voice.push_to_talk_key` in `config/settings.yaml`; supported keys include Ctrl, Shift, Alt, Space, Escape, F8-F12, letters, and digits.

Immediate commands include:

- `stop` or `stop talking`: stop speech only;
- `cancel task` or `never mind`: request cancellation of the current task;
- `mute` / `unmute`;
- `volume up` / `volume down`;
- `show status`;
- `remember that ...`.

Other requests enter the existing manager and agent loop.

“Take a screenshot and tell me what is on my screen” uses the registered `describe_screen` tool, `computer_observe` permission, a fresh screenshot, the configured local Ollama vision route, and explicit interpretation verification. Visual models can still misidentify similar applications, so screen descriptions are observations rather than authority for risky clicks.

Push-to-talk owns the hotkey while it is active (`push_to_talk_exclusive: true`). A `WH_KEYBOARD_LL` hook ([app/voice/interruption.py](../app/voice/interruption.py)) swallows the trigger key while its modifiers are held, so Ctrl+Space no longer opens suggestion popups in the focused editor. The hook keeps its own pressed flag, because Windows stops updating a swallowed key's async state. It ignores software-injected keys, is never installed under pytest, and is removed when voice stops or leaves push-to-talk.

## Hands-free end of speech

`EnergyVoiceActivityDetector` raises its noise floor toward the 10th-percentile level of the last two seconds, even while frames are still classed as speech. A steady fan or TV is learned as the room within seconds. `record_until_silence` also tracks the speaker's own level. After speech starts, a frame below `release_ratio` (0.2) of that level is silence, whatever the absolute threshold says. `max_utterance_seconds` (15) cuts any utterance that never ends. The capture summary records `ended_by` (`silence`, `length_cap`, or `no_speech`) and the noise floor. Hands-free transcripts below `vad.minimum_confidence` (0.3), or matching Whisper's empty-room phrases ("Thank you.", "you"), are logged as `noise_rejected` and dropped. "Yes", "yeah", and "okay" are never dropped, because they answer approvals.

## Mixed languages

`BilingualTTS` wraps the configured voice. `split_scripts` cuts a reply into English and Arabic runs, which play in order through the main voice and the Arabic Piper voice. Without the Arabic voice, Arabic runs are omitted from speech. Recognition switches to the multilingual `small` model in two cases. In Arabic reply mode (`session.reply_language == "ar"`), it detects the language, so English is still heard. During a lesson's speaking exercise, it forces Arabic.

## Other languages

`voice.stt.detect_other_languages` (default `true`) sends every utterance to the multilingual `small` model with language detection whenever it is installed, keeping the English hotwords and prompt. `BilingualTTS` (alias `MultilingualTTS`) looks up a voice per language through `LanguageSkills.speaker`. In a non-English reply language, that voice reads the whole reply. In English replies, runs in other scripts go to their language's voice, falling back to another installed voice for the same script.

## Wake phrase

Wake mode is optional and off by default. Enable it with `/voice mode wake`. The configured phrase is `hey hayat`; narrow aliases compensate for observed STT renderings. This is a phrase gate over short VAD-bounded local transcriptions, not a dedicated neural wake-word engine. Use push-to-talk when continuous microphone activity is not desired.

A dedicated neural wake-word engine is available as an option. With `voice.wake_word.engine: openwakeword`, an openWakeWord model scores 80 ms microphone frames and nothing is recorded or transcribed until it detects the wake word; HAYAT then answers "Yes?" and records one command. There is no pre-trained "hey hayat" model: the free ones are `hey_jarvis`, `alexa`, `hey_mycroft` and `hey_rhasspy` (download one with `python -m scripts.setup_wakeword hey_jarvis`), or `model` can name a custom-trained `.onnx` file. `threshold` (default 0.5) trades misses against false wakes. If the model is missing, voice says why once and keeps using the phrase gate. Verified on synthesised speech: "Hey Jarvis" scored 0.997 and an unrelated sentence 0.000.

## Devices and voices

```text
/voice devices
/voice status
/voice test
```

Select input/output devices in `config/settings.yaml` by numeric ID or a unique name substring. `auto` uses Windows defaults. Select an installed Windows SAPI voice by name; `auto` uses the default voice.

`/voice devices` includes Windows input diagnostics. `/voice probe` performs a short real capture and reports duration, input ID, and peak level. A default input of `-1` is a Windows/driver condition, not a Whisper setting.

Change devices for the current session without editing configuration with `/voice input ID|auto` and `/voice output ID|auto`.

## Speech-to-text

`faster_whisper` is the default. It receives 16 kHz audio, runs `base.en` on CPU with int8 quantization, and warms in a background thread. Continuous capture already uses adaptive local VAD, so Whisper's second VAD filter is disabled by default to avoid dropping short commands. Quiet non-silent audio receives bounded DC removal and level normalization. Clear speech uses one decode for latency; empty or acoustically low-confidence output receives one stronger-beam retry, with a hard maximum of two passes. The selected raw result, alternatives, confidence, and decode count remain structured for the current turn, while durable voice logs retain only length/digest by default. A bounded high-confidence lexicon repairs observed sound-alikes such as `High at` -> `HAYAT`, `power shell` -> `PowerShell`, and `pie torch` -> `PyTorch`; it does not fuzzy-rewrite filenames or approval language. Model, language, device, compute type, cache root, hotwords, and multi-decode thresholds are configurable.

The Arabic course uses a second recognizer, created only when you press **Speak** or type `record` in a lesson: Faster Whisper `small` with `language: ar` (`learning.language_speech_model` selects the size). Arabic speech output uses the open Piper voice `ar_JO-kareem-medium` in its own isolated worker. `python -m scripts.setup_arabic` downloads both.

`windows_speech` is a zero-download local fallback. It recognizes a temporary PCM wave using `System.Speech`; the file is deleted immediately.

## Speech output and interruption

Piper is the shipped local TTS default and uses the installed local voice; Windows SAPI remains a configurable fallback. Playback is asynchronous and stoppable. Temporary audio is deleted. Speech runs on a worker thread, so the agent and push-to-talk listener remain responsive. After playback completes, voice state returns to `READY`. In hands-free mode, recent assistant speech is compared with new transcripts and likely speaker echo is discarded, while explicit stop/cancel commands still interrupt immediately. This reduces self-trigger loops without treating microphone input as trusted authority.

Display responses keep complete task evidence. Spoken responses are short and avoid reading URLs, large paths, stack traces, and code.

## Approvals

The existing permission manager remains authoritative. An `ASK` action pauses and announces the tool. Approval requires `approve`, `confirm`, or `yes continue`; plain `yes` and vague language are rejected. `deny`, `no`, and `cancel` reject the action. `DENY` categories cannot be enabled by voice.

Close-window commands target the session's exact window handle. If unsaved work is detected, the task pauses and asks whether to save, discard, or cancel. The answer resumes that pending action; the assistant never silently selects “Don't save.”

## Privacy and logs

Raw microphone recordings are not stored. Voice JSONL logs contain session ID, state, durations, task linkage, cancellation, recovery, device probes, and errors. By default they store only transcription length and a short digest, not the words. Enable `log_transcriptions` only after accepting that privacy tradeoff.

`/privacy on` stops an active listener, stops speech, releases the camera, and blocks sensor/network tool calls in the authority kernel. If voice was active, `/privacy off` resumes it. Calling `/voice on` while privacy mode is active records the request to resume but does not open the microphone.

External audio can trigger recognition, so microphone text is not stronger authority than typed input. High-risk actions still require explicit approval, while credential, administrator, financial, and protected-system actions remain denied.

## Tests

```powershell
python -m app.voice.test_microphone --probe
python -m app.voice.test_microphone
python -m app.voice.test_speaker
python -m scripts.voice_hardware_loopback_check
python -m scripts.voice_integration_check
python -m scripts.voice_windows_integration_check
```

The interactive microphone test prompts for “Hello AI.” On 2026-08-20 the latest hardware loop spoke through the physical speaker, captured 6.5 seconds through the default Realtek microphone array, transcribed the exact command `Create a text file called voice-test.txt containing hello from my AI`, created/read/verified the disposable file, and spoke completion. The script removed that file after verification. The deterministic filesystem integration check passes transcript -> agent -> filesystem -> verifier -> memory -> response -> TTS. The Windows integration check passes a disposable Notepad window, multi-turn write/add context, explicit unsaved-work choice, exact-window close, and preservation of pre-existing windows.

## Desktop interface

The Tkinter interface uses the same `Runtime` and `AssistantService` as the CLI. It shows conversation, live state with elapsed time, CPU/RAM/GPU health, voice/privacy/child controls, and an observable operational trace from routing, planning, tools, and verification. That trace is deliberately not hidden model chain-of-thought. Permission prompts appear as GUI dialogs; voice cannot overwrite the GUI approval callback after startup.

### Voice Studio

**Studios ▾ → Voice** opens the Voice Studio. Everything on it is read from the running voice system:

- **Microphone input** and **Speaker output** cards show a scrolling waveform and a level meter in dB from the measured audio levels, the listening/speaking state, and a picker for the input or output device the audio system reports. Choosing a device switches it immediately.
- The **pipeline** (Listening → Transcribing → Thinking → Speaking) lights the stage the voice system is actually in; using tools, checking a result and waiting for approval count as Thinking.
- Controls: start or stop voice, push-to-talk, hands-free and wake word modes, test the microphone or speaker, and stop speaking.
- **Conversation** lists what you said and HAYAT's replies in the current voice session.
- The side column (hidden in small windows) shows the voice and speech-to-text engines, the listening mode and push-to-talk key, a **voice speed** slider (0.5×–2×, for this session), **What HAYAT is doing** with each voice step and its time, and sample commands you can click to put in the chat box.

The speed setting now reaches the Piper voice, including the isolated synthesis process: at 1.5×, a test sentence took 1.56 s instead of 1.93 s (pauses do not scale). Earlier, the `speed` value in the voice configuration was read but never applied.

## Optional startup

Startup is never installed automatically. Inspect and explicitly run `.\scripts\install_startup.ps1`. It registers a current-user, limited-privilege logon task for tray mode. Remove it with `scripts\remove_startup.ps1`.

## Troubleshooting

- No input: run `/voice devices`, select an available microphone, and manually check Windows microphone privacy settings.
- Default input `-1` or controller Code 43: repair/restart the exact audio controller from an Administrator terminal or install the matching OEM audio/chipset package, reboot, and rerun `/voice probe`. Do not install a driver for a different laptop model.
- Push-to-talk detected but empty: hold `Ctrl+Space`, begin speaking after it is down, and release after the sentence. HAYAT now retains a 0.4-second minimum capture instead of losing quick releases.
- Quiet speech missed: inspect `/voice probe` peak; continuous mode now keeps 0.5 seconds of pre-roll and uses a 0.0035 adaptive threshold with 1.4 seconds of ending silence.
- Slow first command: allow model download and warmup to finish.
- Wrong device: set the displayed device ID and restart voice mode.
- Hotkey conflict: use another combination such as `ctrl+f8`.
- No sound: run the speaker test and check the chosen Windows output manually.
- Wake misses: use push-to-talk or add only a narrowly observed phrase alias.

## Natural speech and reference voices

Speech uses clean spoken formatting and configurable sentence pauses. Voice Studio
includes an optional local reference-voice selector, requiring a supplied agreed
recording, local XTTS checkpoint and optional engine. See setup, limits and
verification.

## How replies are spoken (2026-10-11)

Replies are rewritten for the ear before they are spoken ([speech_text](../app/speech_text.py)):
- $83,560.00 is said as "83,560 dollars", and +1.12% as "up 1.12 percent".
- "$3 – $5" is said as "3 dollars to 5 dollars".
- sat/vB, EH/s, km and "24 h" are said in full.
- List separators and symbols (· ● ◆ ▲ → ✓) become pauses or words, and each list line is its own sentence.
- Web addresses, wallet addresses and transaction hashes become "a link", "an address" or "a hash".
- A bracketed source domain is left out.
- Speech stops at the end of a sentence (about 300 characters), never mid-word.

A plain sentence is spoken exactly as written.
