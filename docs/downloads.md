# Downloads and dependencies

## Seeing every download (the icon in the header)

The **download icon** at the top right of HAYAT's main window (an arrow into a tray, next to
Help):
- turns gold, with a red badge counting active downloads, while something is downloading;
- shows a hover summary of this session's web requests;
- opens **Downloads & web activity** when clicked. The command palette entry (Ctrl+K,
  "downloads") does the same.

| Tab | What it shows |
|---|---|
| Active | Each download in progress: a file, an AI model (Ollama pull) or a video. Shows what it is, a progress bar with how much of how much, the speed, where it comes from and where it is being saved. |
| History | Every file, model and video downloaded, newest first, kept across restarts: when, what, from which site, saved to which path, size, and the result (done, failed, blocked by the antimalware check, cancelled). **Show in folder** (or double-click) opens Explorer at the file. Also: **Open downloads folder** and **Copy address**. Files downloaded before this log existed come from the download manager's own records. |
| Web activity | Every request HAYAT made this session, newest first: the time, what it was for (Markets, Web3, PublicIntelligence, AgentWeb…), the site, the address, the size and how long it took. Failed requests are red. A filter box narrows the list. A panel sums up each site: requests, data, average time, failures, and what it was used for. |

**What is stored:**
- History is kept in `data\logs	ransfers.jsonl`, on this computer only.
- Web requests are kept for the session only (the last 500), because there are many.
- Addresses are shown without their query string, so keys and tokens never appear in the
  window or the file.


## Governed public downloads

`DownloadManager` accepts absolute HTTPS URLs on an authoritative-host allowlist, rechecks the final redirect host, enforces a per-request byte limit and project quota, computes SHA-256, optionally compares an expected hash, records SQLite audit metadata, and never runs the result. The completed partial file must pass a fail-closed Microsoft Defender custom scan before it is marked complete. A detection or scanner failure is retained as `.blocked` and is never released to a parser or executor.

```text
/downloads status
/downloads recent
/downloads models
/downloads cleanup
```

Files are stored under `data/downloads`; incomplete `.partial` files are removed on ordinary transfer errors. Security-blocked evidence is retained with a `.blocked` suffix for audit and owner-directed cleanup. Trusted source does not mean trusted content: inspect before use.

## Python environment

```text
/dependencies
/dependencies check
/dependencies outdated
```

Dependencies are installed into `D:\AI\.venv` from `requirements.txt`. Status reports installed versions/missing packages; `check` runs `pip check`; `outdated` queries available versions but upgrades nothing. Re-run project setup for normal repairs:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
.\.venv\Scripts\python.exe -m pip check
```

Free project-local dependencies may be installed when justified. Administrator elevation, unknown executables, security changes, and oversized model downloads still require separate review/approval.
