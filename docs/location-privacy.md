# Location privacy

Added 17 October 2026. Code: [app/location_privacy.py](../app/location_privacy.py), window
[app/gui_location_privacy.py](../app/gui_location_privacy.py). Studios → **Location privacy**, or ask
"who can see my location?".

The owner asked that HAYAT may look up where he is, but that nobody else can detect it. HAYAT cannot
hide a computer from the whole internet: any website sees your internet address, and Windows apps with
location permission can ask Windows directly. What HAYAT does is make sure nothing **it** does gives your
location away, and show you every other way it gets out, with what to do about each.

## What HAYAT does

| Protection | How |
|---|---|
| Place names on this computer | A Windows location fix used to be sent to BigDataCloud to name the town, so an outside service learned your exact coordinates each time HAYAT looked. Now the name comes from GeoNames' list of about 144,000 places (population over 1,000, CC BY 4.0), installed once into `data/places/`. It comes from the `reverse_geocoder` 1.5.1 file on PyPI, pinned by SHA-256 (GeoNames' own download server asks robots not to fetch). Say "set up private place names", or press the button in the window. Until then, the town is not named at all rather than asked online. |
| Guard on what leaves | Before any tool that sends a message, posts, writes to a connector, searches the web or reads a public page runs, the tool registry screens its arguments. Text holding your precise coordinates (within 5 km of the latest fix, written as numbers or in a map link) is held, with how to share it on purpose. |
| Photos | A photo attached to a message has its GPS tags removed: a stripped copy in `data/work/no-gps/` is what gets sent, the original is unchanged, and the result says so. If the tags cannot be removed, the photo is not sent. "Remove the location from holiday.jpg" makes a stripped copy on request. |
| Public lookups | A position sent to a public service (weather, a deep look) that is within 5 km of you is rounded to one decimal, about 11 km. That is enough for the weather and too coarse to find a house. The fusion "around a place" view rounds the same way when it has to ask online for a place name. |
| Nothing kept on disk | The latest fix the guard compares against is held in memory only. It is gone when HAYAT closes, and the guard starts again at the next location reading. |
| Sharing on purpose | "Share my location once" lets the next message include it. It needs your approval and lasts one send or five minutes, whichever comes first. |

## What the check shows

`location_privacy_check` reads, without changing anything:

- **Windows apps**: which apps may use the location service and when each last did, from Windows' own record (`CapabilityAccessManager\ConsentStore\location`). HAYAT's own reads show up as `powershell.exe` (the location) and `netsh.exe` (Wi-Fi scans), and are named as HAYAT, not flagged.
- **Websites**: which sites Edge, Chrome, Brave, Opera and Firefox allow to see your location.
- **Your internet address**: what one public service (ipwho.is) says about it, which is what every website already sees. With a recent fix, it also shows how far from you that places you. Privacy mode skips this.
- **Photos**: JPEG, TIFF and WebP files in Pictures, Desktop and Downloads (up to 600) that carry GPS tags.

## Fixing what it finds

A finding that has a settings page gets a button under it in the window, and you can also ask in chat:
- **Open Windows location settings** ("open my location settings") opens `ms-settings:privacy-location`, where each app's switch is.
- **Open Edge / Chrome / Brave / Opera location permissions** ("open edge location settings") opens that browser's site location page (`edge://settings/content/location` and the like). The browser is found from its Windows App Paths registration.
- Firefox opens its privacy settings.

HAYAT only opens the page (`location_open_setting`, permission `application_launch`); you flip the switch.
It doesn't change Windows' privacy settings or browser profiles itself, so every change is yours and yours
to undo. For your internet address there are the [Masks](#masks-tor-a-masked-browser-and-the-guard) below,
and for photos, "remove the location from <photo>".

## On this computer, 17 October 2026

The first live check found:
- **Location service:** on, with 32 apps allowed.
- **Apps that read your location in the last 30 days:** Edge, Microsoft Teams, Adobe Acrobat and the Windows COM host, plus HAYAT itself.
- **Websites allowed your location:** loopnet.com in Edge and chatgpt.com in Chrome.
- **Your internet address:** places you near Detroit, through T-Mobile.
- **Photos:** none of 230 carry GPS tags.

Place names were installed (144,563 places); Southfield, Michigan is now named without anything being sent.

Tests: [tests/test_location_privacy.py](../tests/test_location_privacy.py) (stand-ins for Windows, the
browsers and the IP service; real photos and place files made in a temporary folder) and the window test
in [tests/test_gui.py](../tests/test_gui.py).

## Masks: Tor, a masked browser and the guard

Code: [app/privacy_masks.py](../app/privacy_masks.py). Window: the **Masks** cards in Studios → Location
privacy. Chat: "how untrackable am I?", "set up tor", "mask HAYAT", "open masked browser", "unmask HAYAT".

The owner asked for several masks to be untrackable, with no Wi-Fi network renamed and no third-party VPN
app. So the Wi-Fi name is not read or mentioned, no VPN is recommended or installed, and every mask runs
from HAYAT:

| Mask | Covers | How |
|---|---|---|
| Tor | HAYAT's own web reading: research, world feeds, market data, pages | Tor's Expert Bundle 15.0.25 (21 MB) comes from dist.torproject.org. It is kept only if its SHA-256 matches the published one (the same on the archive mirror) and Windows Defender passes it, and only `tor.exe` and its two country tables go into `runtime	or`. It runs as a local SOCKS proxy on 127.0.0.1:9052. HAYAT's public fetcher connects through it once Tor has finished connecting; local traffic (the brain, the router page) never does. The choice is remembered (`data/privacy-masks.json`), Tor restarts with HAYAT and stops when HAYAT closes. |
| Masked browser | Your own browsing | The Edge or Chrome already on the computer, started by HAYAT with a separate profile (`data/tor/browsers/`) and its proxy set to HAYAT's Tor. Names are resolved only through Tor (`--host-resolver-rules`), so there is no DNS leak, and WebRTC may not go around the proxy. It shares no cookies or history with your normal profile. Nothing is installed and no Windows setting changes. It needs Tor on first. It hides your address; it is not Tor Browser, so protection against fingerprinting is the browser's own. |
| Location guard | What HAYAT sends | See above: coordinates held, photos stripped, positions rounded. |

If a VPN is ever connected, HAYAT still detects it by its adapter and tests it for DNS and IPv6 leaks.
It never suggests one.

**What the check shows:**
- each mask on or off;
- what websites see of your computer (one direct request to ipwho.is) and, with Tor on, of HAYAT (one request through Tor);
- any VPN leak.

Privacy mode asks no website at all.

**What no mask does:** accounts you sign into, a phone's GPS, browser fingerprinting and what you type
yourself still identify you. Masks hide where a connection comes from, not who you are. Many sites slow
down or block Tor. HAYAT keeps its robots.txt and rate rules with or without it.

**Live checks, 17 October 2026:**
- Tor set up (22.5 MB, hash matched, Defender clean) and connected in 12.9 s. check.torproject.org answered `IsTor: true`.
- Websites placed HAYAT in Stockholm, Sweden, while the computer itself was seen near Detroit through T-Mobile.
- Edge with the masked-browser settings (run headless, so no window opened) reached check.torproject.org through Tor (`IsTor: true`); plain Edge reached it directly (`IsTor: false`).
- Tor was stopped afterwards, and the owner's on/off setting was not changed by the tests.

Tests: [tests/test_privacy_masks.py](../tests/test_privacy_masks.py).
