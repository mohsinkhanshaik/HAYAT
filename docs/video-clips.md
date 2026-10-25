# Video downloads, clip analysis and chat previews

Paste a YouTube or supported video link in chat. An inline card offers **Inspect**,
**Play**, **Download**, **Clips** and a quality selector. Bare recognized video
links are inspected; downloads start when you request them or press an action.
**More tools → Video clips** (also in the command palette) opens the complete
clipping controls **inside the transcript**.
Paste a public video link, or choose a local video inside HAYAT's allowed project
folders. **Inspect link / qualities** lists the source resolutions.
Choose a quality, clip layout, number, length and audience, then select
**Download only** or **Analyze & make clips**. Results appear in chat.

For a direct chat request, try:

```text
Download this video https://media.w3.org/2010/05/bunny/trailer.mp4 in 720p
Make 2 clips of 15 seconds from https://media.w3.org/2010/05/bunny/trailer.mp4
Play https://www.youtube.com/watch?v=aqz-KE-bpKQ in 360p
Pause it
Seek the video to 1:20
Set video volume to 40
Video speed 1.5
Make three square clips from that video, 30 seconds each
Play the second clip
```

When a link cannot be downloaded, the reply says why in plain words instead of
yt-dlp's raw error (2026-10-17, `classify_download_error` in
[app/video_workflow.py](../app/video_workflow.py)): the video is private,
members-only, age-restricted, blocked in this country, removed, not offered at
the chosen quality, needs FFmpeg, DRM-protected, paid, an upcoming or live stream,
the site is rate-limiting (429) or refused the request (403), the download timed
out, or the link is not a supported video.
HAYAT's own messages (playlist, live stream, size limit) are passed through as
they are.

Sources and their metadata are saved in `download/videos/video-<id>/`.
Exports, metadata and any speech subtitles are saved in
`download/videos/clips/clip-<id>/`. Analysis reports remain in `download/videos/`.
On this installation, that directory is `D:\AI\download\videos`.

Chat displays embedded video with sound, Play/Pause, seeking, skip buttons,
mute, volume, playback speed and **Analyze / make clips**.
The portable libVLC engine lives in `runtime/vlc`; Python uses `python-vlc`.
Playback has no 90-second chunk limit with this engine. Up to four player cards
and four link cards are retained to bound resources. Starting playback through a
chat request pauses the other native players. Requests resolve "it" and clip
numbers from verified tool results for this session; clearing chat clears those
references. Player control requests report acceptance, rather than claiming an
unverified playback state. If native playback is unavailable, the existing silent
frame preview remains available, with **Open with audio** for the external player.
That fallback decodes up to 90 seconds per play. Downloads, decoding,
transcription, review and rendering run away from the interface thread.
**Stop video job** cancels processing between steps; an ongoing transcription or
model request may finish its current step first.

## Quality and exports

Auto selects the best available source up to 1080p. You can request 360p through
2160p, or best (up to 4320p). HAYAT chooses an available resolution at or below
the requested cap and reports the actual dimensions. Lower quality saves download
time and disk. Exporting a low resolution source at 1080p cannot restore detail.
Known file sizes are considered when choosing formats; aggregate downloads are
also bounded during transfer.

The studio makes high quality H.264/AAC MP4 files in vertical, square or original
layout. Vertical defaults to 1080×1920, with padding to preserve the full frame.
The `create_video_clip` tool also offers explicit center crop, 720p standard
exports and manual start/end times. Captions use locally transcribed speech,
retimed to the clip. If speech transcription fails or finds no speech, the clip
has no speech captions. Review transcript accuracy before publishing.

## What the scores mean

### Edit and create result links inside chat

Every player now offers **Edit / result link**, opening an editable card in the
conversation. Set start and end in seconds, vertical/square/original shape,
fit or centre crop, high/standard export, and speed between 0.5× and 2×.
Optional mild contrast/colour adjustments and loudness normalization improve
presentation without promising recovered image detail. Matching speech evidence
can supply captions. Captions stay aligned when export speed changes.

**Export edit** writes a new verified MP4 and keeps the source unchanged. Both
the source interval and resulting duration must be at most three minutes;
longer sources need an explicit trim. After export, **Create result link** uses
the latest successful output from that editor. The link opens a dark preview
page with a video player, seeking, and a download button. It is read-only and
serves only the selected file. Links work on this computer while HAYAT runs;
they are not public internet URLs. Closing HAYAT invalidates them. The most
recent 32 links remain active; changed or removed files invalidate their links.

Text requests can use verified chat context: “trim the second clip from 5 to 20
seconds”, “improve that video from 1:05 to 1:20 in square”, and “share the second
clip”. Edits and links require a downloaded/local video; first play or download
a remote link. These direct requests avoid a model planning round trip.

Exact desktop requests such as “move the cursor to 200, 300” also use direct
plans with screenshots before and after input. Pointer tools reject invalid
primary-display coordinates and verify the pointer before clicks and drags.
An accepted input is distinct from confirming the intended application effect.
Unicode desktop typing directs callers to the existing Unicode-capable editor
or clipboard tools, rather than silently dropping characters.

HAYAT uses timestamped speech, six sparse visual samples, and the configured local
model when it is available to assess opening hooks, payoff, clarity, pacing and
audience relevance. It supplies reasons and weaknesses for the selected intervals.
Transcript heuristics are the fallback. Videos without usable speech or visual
descriptions remain unscored rather than receiving invented assessments.

Scores are editorial indices, **not probabilities of going viral**. This workflow
does not measure current trends, competition, audience response or distribution.
It cannot reliably label a video as certain to go viral or certain to fail.
Review the candidate previews before publishing. Automatic selection avoids
overlapping clips and may return fewer clips than requested.

## Supported links and limits

The downloader uses [yt-dlp's supported extractors](https://github.com/yt-dlp/yt-dlp).
It supports many public links and direct HTTP video files; it cannot download
every URL. Login-only, DRM-protected, blocked or unavailable sources produce an
error. Live streams and playlists are excluded. It does not import browser cookies
or bypass authentication. Downloader connections are restricted to public internet
addresses, including redirects. Downloaded and local inputs pass the existing file
admission scanner and permission gateway before decoding. New playback and
assessment workflows enforce the download/read policies on their nested calls.

Downloads and local inputs are limited to 1 GiB. Automatic analysis supports up to
two hours. Automatic clip length is 10–90 seconds, with up to ten requested clips;
manual exports support up to three minutes. Exports preserve the source and verify
the resulting duration, dimensions and source hash. FFmpeg and ffprobe are required
in `runtime/ffmpeg/bin` or PATH; Node can live in `runtime/node` for extractors that
need JavaScript. Python dependencies include `yt-dlp[default]`.
For a fresh Windows x64 copy, `python scripts/setup_video_player.py` downloads the
official portable VideoLAN archive and verifies its published SHA-256 before
extraction. It retains the bundled license files. Other platforms can install
libVLC through their package manager; native embedding is enabled on Windows and
Linux with the runtime available. macOS uses the existing frame preview.
See [FFmpeg's filter documentation](https://ffmpeg.org/ffmpeg-filters.html) for the
underlying scale, pad, crop and subtitle operations.

## Verification on this installation

- The editor/result-link update passed 399 video, desktop, chat, intent,
  planner, runtime and GUI tests. Final help/capability/link checks also passed.
  Real speed-adjusted exports kept source hashes unchanged and retained audio.
- A real trailer edit from 4–16 seconds exported vertically at 720×1280,
  with 1.25× speed, mild picture adjustments, and normalized audio. The local
  result page loaded its video with a 9.7-second duration and no player error.
  HTTP range seeking, changed-file invalidation, escaped titles and download
  responses were verified. The editor card was inspected with HAYAT's dark theme.

- The chat upgrade passed 283 GUI/assistant/intent/planner/session regression
  tests; the separately exercised brain-tour GUI test also passed. Another 44
  video and chat tests passed, including a real isolated VLC worker with audio
  track detection, play, pause, seeking, speed, and clean process exit.
- A real YouTube download of Blender's Big Buck Bunny film completed at 360p,
  with audio, totaling 24.4 MiB. The native chat widget played this downloaded
  file on screen. Site availability can still vary between requests.
- The player decoder and native libraries run in disposable child processes;
  closing a card or window terminates them. UI state updates are coalesced,
  startup has a deadline, and no decoding or tool work runs on the UI thread.

- 410 relevant video, chat, intent, planner and GUI tests passed; the updated video
  and GUI tests passed again after the native playback fix. Ruff checks passed.
- A real public W3C Big Buck Bunny trailer downloaded successfully. The source
  provided 480p even when 720p was requested; the result reported that actual size.
  A later network check received HTTP 403; HAYAT surfaced the site error and did
  not attempt to bypass it. Support depends on the site's availability and access.
- Local analysis and two real vertical MP4 exports completed, with audio retained.
  With insufficient meaningful speech evidence, its candidates were unscored.
- Native Play, Pause and seeking were checked on the actual preview component.
  Speech caption rendering, invalid interval rejection, model index grounding and
  nested permission enforcement are covered by automated tests.
