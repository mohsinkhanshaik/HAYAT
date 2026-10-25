# Finding new public sources while idle

HAYAT looks for new public camera and data sources by itself, in small cycles,
while the machine is free. It does not wait for each source to be handed over.
It uses only what publishers make public, and it never works around a
restriction. Code: [app/intelligence/discovery.py](../app/intelligence/discovery.py).

## What one cycle does

Each cycle does one small job, about every 10 minutes:

- **Probe** the next waiting candidate. The fetch is polite: robots.txt is
  honoured, only public addresses are used, size and time are capped, and
  nothing logs in.
  - A JSON document is recognised as a **camera catalog** by its shape: at
    least 10 records, each with a location and an `https://` image or `.m3u8`
    stream.
  - An HTML page is read for links to data endpoints (`.json`, `.geojson`,
    `/api/`, `FeatureServer`, DATEX, WFS), and those are queued as new
    candidates.
- **Discover:** search the web for open camera, webcam and weather catalogs,
  starting with the countries HAYAT has no cameras for yet. Every other search
  is limited to that country's government domain (for example `gov.au`,
  `gouv.fr`, `go.jp`), where open-data catalogs live.
- **Health:** sample a few cameras from the network checked longest ago and
  record whether frames still come back.
- **MCP:** look up servers for a topic (cameras, traffic, weather, maps,
  satellite, news and others) in the public MCP registry, and note any
  credentials each needs. **Nothing is installed.** Connecting one is your
  decision.

## When a found catalog goes live

| Found | What happens |
|---|---|
| Official catalog (government or university domain) whose media is on its own domain | Goes live as a camera network by itself and is indexed at once |
| Any other catalog | Waits as *proposed*. Say **approve source N** to turn it on |
| HTTP 401/407, or a page that describes keys or registration | Recorded as *needs access*, with what is required |
| HTTP 403 | Recorded as *refused*: the publisher blocks automated access, and HAYAT does not try to get around it |
| robots.txt disallows it | Rejected |
| A key inside someone else's URL | Never used; the publisher's own registration is needed |

A live discovered network fetches media only from the hosts its own catalog
used. Activated networks come back after a restart.

## What it will not do, whatever the purpose

- It never uses directories of unsecured private cameras or device search
  engines (Insecam, Shodan, Censys and similar). Watching a stranger's
  unprotected camera is a privacy violation.
- It skips aggregators that re-host other publishers' feeds and goes to the
  original publisher instead.
- It never logs in, guesses passwords, uses leaked keys or scans networks. The
  fetcher refuses private and local addresses outright.
- It never runs while you are working, while privacy mode is on, after the
  learner's STOP file is written, or once its own budget (30 requests an hour)
  is spent. It uses the background learner's checks: paused during your turns,
  and CPU and RAM headroom required.

## Ask it

- "What new sources did you find?" shows the discovery status: what is live,
  what is waiting, what needs access, health problems, MCP suggestions and the
  countries with no cameras yet.
- "Discover new camera sources" runs three cycles now.
- "Approve source 7" turns a proposed catalog on.

Settings: `intelligence.discovery` in `config/settings.yaml` (`enabled`,
`requests_per_hour`, `interval_seconds`). The records are kept in
`data/memory/discovery.db`.

## A first live run (2026-10-06)

Searches limited to `gov.au` led to the Queensland, Victoria, Tasmania and
South Australia traffic-camera sites. Their pages refuse automated access
(HTTP 403), so they were recorded as refused, with that reason, rather than
fetched another way. A map-servers lookup found 8 MCP servers and noted which
need a token. Unrecognised pages were read only for data links.
