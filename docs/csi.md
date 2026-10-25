# Wi-Fi sensing (channel state information)

Channel state information (CSI) is what a Wi-Fi receiver measures about the channel for every subcarrier of a packet: an amplitude and a phase. When people move through a room, they block and bend the radio paths, so those amplitudes start to wobble. Wi-Fi sensing reads that wobble. HAYAT can connect a CSI source, check that it is live, record it for later and say whether something is moving. The code is in [app/csi.py](../app/csi.py).

## What hardware gives CSI

| Source | CSI? | How HAYAT reads it |
| --- | --- | --- |
| An ESP32 running [ESP32-CSI-Tool](https://github.com/StevenMHernandez/ESP32-CSI-Tool) or Espressif [esp-csi](https://github.com/espressif/esp-csi), plugged in by USB | Yes: 64 to 192 subcarriers per packet | `serial`: reads its `CSI_DATA,...` lines from the COM port (921600 baud by default) |
| A device on your own network that sends CSI (an ESP32 over Wi-Fi, or a Linux machine with Nexmon, Intel 5300 or Atheros CSI tools and a small forwarder) | Yes | `udp`: listens on a port (5500 by default) for `CSI_DATA` lines or JSON lines |
| A recording HAYAT saved, or a log captured elsewhere | Yes, replayed | `file` |
| This computer's own Wi-Fi card (MediaTek MT7922 on Windows) | **No** | `wifi_link`: signal strength (RSSI) about once a second, always labelled "not CSI" |

Windows Wi-Fi drivers do not give programs per-subcarrier CSI, so HAYAT never calls anything from the built-in card CSI. Signal strength can still show someone walking between this computer and the router. It can't show breathing or small gestures.

The JSON-lines format accepts `amplitude` (with an optional `phase`), `real` and `imag` lists, or `csi` as `[[real, imag], ...]` pairs or a flat list in ESP32 order (imaginary part first). `rssi`, `mac`, `channel` and `t` (Unix seconds) are optional.

## Say it in chat

| You say | Tool | What happens |
| --- | --- | --- |
| "Scan for CSI devices" | `csi_scan` | Lists USB serial ports, marks the ones that look like an ESP32 bridge (CP210x, CH340, FTDI, ESP32-S3/C3 USB), and lists the Wi-Fi adapter and any open connections |
| "Connect CSI", "connect CSI on COM5", "connect CSI on UDP port 5500 and record", "connect CSI from file office-20261014.jsonl" | `csi_connect` | Opens the source, waits up to 3 s for frames and reports what arrived. With no device it uses `auto`: an ESP32-like port if one is plugged in, otherwise `wifi_link`, and it says which |
| "Is CSI connected?", "check CSI", "CSI status" | `csi_status` | Says, for each connection, whether it is open and receiving: frames per second, subcarriers, RSSI, transmitters seen, how old the last frame is, and any error |
| "Calibrate CSI" | `csi_calibrate` | Records 10 s of a still, empty room as the baseline |
| "Is anyone moving? Check the Wi-Fi sensing" | `csi_motion` | Compares the last 10 s with the baseline: still, some movement or strong movement |
| "Show CSI recordings" | `csi_recordings` | Lists saved recordings with frame counts and sizes |
| "Disconnect CSI" | `csi_disconnect` | Closes the connection and its recording |

"What is CSI?" is still answered in the conversation. Only requests about the hardware run the tools.

## How "connected" is checked

A connection has a state: `open`, `reconnecting` (the port went away, so it retries every 2 s), `finished` (a replayed file) or `closed`. **Connected** means the source is open. **Receiving** means a frame arrived within the last 3 s (5 s for `wifi_link`). An ESP32 that is plugged in but not printing CSI is reported as "open but no frames have arrived", not as working. Unplugging the board shows "not connected … retrying", and plugging it back in resumes the same connection. Lines that are not CSI, such as the ESP32's boot log, are counted as `unparsed_lines`.

## How movement is judged

For CSI, HAYAT prepares the frames in three steps:

1. Each frame's amplitudes are divided by that frame's mean. The radio's automatic gain moves every subcarrier together, but people do not.
2. Lone spikes are replaced by the subcarrier's median. These are the occasional corrupted packets an ESP32 prints, more than 3 scaled median deviations out (a Hampel filter). A run of outliers is the room changing, so runs are kept.
3. Only frames from the most frequent transmitter and of the most common width are used.

Two numbers come out of each window:

- **Variation** (`score`): the median, over the subcarriers that carry signal, of how much each one varies in time. It is compared with the still-room baseline.
- **Smoothness** (`autocorrelation`): the median lag-1 autocorrelation of each subcarrier. A still room leaves white noise, close to 0. A body moving through the radio paths changes the amplitudes smoothly from one packet to the next, close to 1. This needs no calibration.

With a baseline from "calibrate CSI", the answer is the variation's ratio to the still room: under 1.6× is still, under 3× is some movement, and above that is strong movement. When packets arrive at 8 per second or more and the smoothness stays under 0.2, the extra variation is reported as noise (interference or a busy channel) and the room stays "still". Without a baseline, CSI is judged on smoothness alone: under 0.35 is still, under 0.65 is some movement, and above that is strong movement. Variation below the radio's own floor always counts as still. For `wifi_link`, the score is the standard deviation of RSSI in dB, judged on rough fixed thresholds that the answer calls uncalibrated.

Movement means the radio paths changed: a person, a pet, a fan or a door all count. HAYAT does not claim that a person is present.

## Live monitor and events

While a source is receiving, [app/sensing.py](../app/sensing.py) scores the newest 4 seconds once a second and keeps the last 10 minutes as a timeline. A new level must hold for two readings in a row before it counts, so one noisy window does not flip it. Each change publishes a `SENSING_MOTION` event (`from`, `to`, `connection`, `score`, `ratio_to_still`), so proactive rules can react to movement. "What does the Wi-Fi sensing see?" (`sensing_status`) answers from this timeline: the level now and how many of the last 60 seconds had movement. The sensory-state report (`sensory_fusion_status`) includes Wi-Fi sensing beside the camera, microphone and screen.

## Cross-checking with the camera

"Is anyone moving? Check Wi-Fi sensing with the camera too" (`sensing_check` with `with_camera`) watches Wi-Fi and the camera's pixel change over the same few seconds, then says which sensor saw what:

| Wi-Fi | Camera | Verdict |
| --- | --- | --- |
| movement | change | Movement confirmed: both saw it |
| movement | no change | Wi-Fi only: most likely outside the camera's view (another part of the room, behind a wall or door), or something like a fan |
| no movement | change | Camera only: often light (a lamp, a screen, the sun), or movement too small or too far from the radio path |
| no movement | no change | Still |

The camera never opens silently. It joins a check only when you ask for one, or when you tick "Confirm Wi-Fi movement with a 3-second camera check" in the window. That tick runs one camera check each time Wi-Fi movement starts. Each camera check goes through the `camera_watch_change` tool, so `camera_use` authority and privacy mode decide as they do for any camera request. No frames are kept.

## The Wi-Fi sensing window

The window has two tabs. **Sensing** is described below. **Router & network** shows the router this computer is connected to, its 5G signal, nearby Wi-Fi and the devices on the network ([Router and home network](network.md)).


Open it from **Studios → Wi-Fi sensing (CSI, movement, camera check)**, the command palette ("Wi-Fi sensing"), or the **Open window** button on the main window's Wi-Fi sensing panel. It has four panels:

- **Status lights**: connected, receiving, real CSI (with the subcarrier count) or signal strength only, the movement level, and privacy.
- **Source**: choose auto, an ESP32 on a COM port, UDP, a recording, or the built-in adapter. Then Scan, Connect, Disconnect or Calibrate. "Record every frame for later" saves the stream. Each button runs the same tool as chat.
- **Live signal**: a waterfall of every subcarrier's amplitude over the last 240 packets, with time running left to right and bright meaning strong. Ripples moving through it are things moving through the radio paths. For the built-in adapter it draws signal strength instead, labelled "not CSI".
- **Movement**: the level now and one bar per second for the last 2 minutes, coloured olive for still, gold for some movement and red for strong movement.
- **Camera cross-check**: "Check now: Wi-Fi + camera", the confirm-with-camera tick, and the last verdict.

The main window shows the same picture in brief. Its right column has a **Wi-Fi sensing** panel with the connection, the level and the last minute as bars. The Sensors grid has a **Wi-Fi** tile, and the top status line reads "Wi-Fi: CSI 98/s", "Wi-Fi: signal" or "Wi-Fi: off".

Privacy mode closes the Wi-Fi connections and pauses the monitor, which publishes `SENSING_MOTION` with level `paused`. A privacy switch that left a through-wall motion sensor running would not be one.

## Data kept for later use

- Recordings: `data/sensing/csi/recordings/<name>-<date>-<time>.jsonl`. The first line is a header and each following line is one frame (`t`, `kind`, `rssi`, `mac`, `channel`, `amplitude`, `phase`). Each recording stops at 512 MB. To replay one, say "connect CSI from file <name>".
- Baselines: `data/sensing/csi/baselines.json`, one per connection name.
- For new features: `registry.csi` is the `CsiHub`. `hub.subscribe(callback)` calls `callback(name, frame)` for every frame, and `hub.frames(name, seconds)` returns what was buffered (about the last 30 s at 100 frames a second). `movement_score(frames)` and `parse_line(text)` work on their own.

## Limits and permissions

- Up to 4 connections at once.
- UDP listens on every interface so that a device on your network can reach it. Packets from outside private, loopback and link-local addresses are dropped, and `allow_from` can limit packets to one sender. Windows Firewall may ask once whether Python may receive on that port.
- `csi_connect`, `csi_disconnect`, `csi_motion` and `csi_calibrate` use `computer_observe`, a sensor category, so privacy mode switches Wi-Fi sensing off along with the camera and screen. `csi_scan`, `csi_status` and `csi_recordings` only read state (`system_read`).
- Serial access needs the `pyserial` package from requirements.txt.
- Sense only the spaces you are responsible for. CSI shows movement through walls.
