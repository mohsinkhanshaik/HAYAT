# Finding people to contact

**Private networking → Find people to contact**, or in chat:

```text
Find people to contact: founders of fintech startups in India
Find top machine learning researchers in India
Show me CEOs of banks in Germany
Find python developers in Hyderabad
Find executives at Infosys
Find recruiters at Zoho
```

The contact book and **Find professional contacts** only helped once you already had a name. This finds the
names: real people for a goal (a job, business, investment, research or your network), each with the source that
names them and professional ways to reach them. Code: `app/research/people_finder.py`, window:
`app/gui_people_finder.py`, tool: `people_find`.

## Where the people come from

| Who | Source | What you get |
|---|---|---|
| Chief executives, founders and chairs of organisations in a field and place | **Wikidata** (open, CC0) | role (with "since" year), organisation, its website, their LinkedIn ID when they published it there; ranked by how many Wikipedia editions cover the organisation, so leading organisations come first |
| Researchers on a topic, in a country | **OpenAlex** (open, CC0) | institution in that country, citations, works, ORCID record |
| Developers by place and language or keyword | **GitHub** public API | company, short bio, website, followers |
| CTOs, VPs, heads of…, named organisations | **The organisation's own pages** (robots.txt honoured) | names with their titles from its leadership or team page |

HAYAT picks the sources from the role (researchers → OpenAlex, developers → GitHub, leaders → Wikidata, other
titles → team pages). Untick **Choose sources for me** to pick them yourself.

**Recruiters:** recruiters are rarely listed on company sites. For "recruiters hiring data analysts in Michigan",
the companies come from your saved job matches; for each named organisation you also get its website, careers
page and a LinkedIn search for that role there, which you open yourself.

## How it stays accurate

Live checks on 2026-10-25 shaped these rules:
- **A post someone left is not theirs.** The first run listed Stuart Gulliver (left HSBC in 2018) and Jes Staley
  (left Barclays in 2021) as chief executives: Wikidata keeps past holders with an end date. Only statements with
  no end date, not deprecated, are used.
- **When a successor is recorded without the predecessor's end date** (KfW listed both its chief executives), only
  the holder who started last is kept, with "since 2021" shown. Wikidata still lags at times (it listed HSBC's
  former chief executive with no end date), so each row says to check the organisation's own site.
- **Organisations' own sites.** Named organisations are looked up on Wikidata for their official website, then its
  own "Leadership", "Management profiles" or "Team" links are read. Guessed paths were 404 on stripe.com, and a
  bare "management" had matched "waste management" articles. Button text ("Read Bio") and slogans are not names.
- **Third-party pages** (org-chart sites) are labelled as such, never as the organisation's website.
- **Titles must fit the role:** "head of marketing" needs marketing (or CMO, growth, brand) in the title; any
  "Director" is not enough.
- **Wikimedia's rules:** Wikidata refuses clients that don't identify themselves with a contact address (403 for
  every request, through Tor or not). HAYAT sends `HAYAT/1.0 (<project URL>; local personal assistant)` to
  Wikimedia only.

Live results (through Tor): founders of fintech startups in India 9; CEOs of banks in Germany 10 (Deutsche Bank,
Commerzbank, KfW, DZ Bank…); machine learning researchers in India 20 (IISc, IITs); python developers in
Hyderabad 12; executives at Infosys 3 from its own management page. Stripe and Zoho publish no list HAYAT can read;
their websites, careers pages and LinkedIn searches are given instead.

## Deep: how to reach them (2026-10-26)

Tick **Deep** (or ask "… in depth" / "… with their contact details"). For the top five people HAYAT runs contact
discovery on their organisation (or a developer's own website) and adds what was **published**: a work e-mail on a
page that names them, the organisation's contact page, and team addresses (press, sales, support), each with the
page it came from. Another person's address on the same page is never given to them. Live: for "founders of AI
startups in India" it found Krutrim's support page and sales@/cloudsupport@ addresses, and contact pages for two more.

Many universities and publishers refuse requests from Tor exits (IISc's faculty page did). The result says how many
pages refused; with Tor off ("unmask") more answer, which is your choice. ORCID's public e-mail endpoint is closed to
programs by its robots.txt, so it is not read.

## What you can do with a result

- **Save to contact book**: one or several (Ctrl+click). Saved encrypted on this computer, with the source and
  the professional routes, then **Outreach draft** prepares an introduction for you to review and send.
- **Find their contact routes**: opens **Find professional contacts** with the name, organisation and website
  filled in, for published work channels and team routes.
- **Open source**, the links under **Ways in**, and **Export CSV**.
- **Earlier searches**: every search is kept in `data/networking/people-finds/` and can be reopened.

## Boundaries

Public professional information only: no guessed e-mail addresses (GitHub e-mails are never read), no data
brokers, no personal social pages, no private details, and nobody is contacted. A name and role are what the source
says, not verified identity; the source is always linked.
