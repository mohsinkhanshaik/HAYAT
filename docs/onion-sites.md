# Onion sites: where to start, what each one is, and checking a link

**Searches → Onion pages → Find onion sites**, or in chat: "show me onion sites for news", "onion sites about
privacy", "is this onion link real? http://….onion". Code: `app/research/onion_directory.py`, tool `onion_sites`.

## The verified directory

HAYAT reads *Real-World Onion Sites* (Alec Muffett, CC BY-SA 4.0) through Tor and keeps a copy for a week. It lists
the onion services of mainstream organisations, about 171 in 14 categories:

| Category | Examples |
|---|---|
| News, BBC World Service, Deutsche Welle, RFE/RL & VOA | BBC, The New York Times, The Guardian, ProPublica, Deutsche Welle |
| Search engines | DuckDuckGo, Brave Search |
| Social networks | Facebook |
| Tech and software | the Tor Project, Proton, Debian |
| Civil society | Privacy International, Riseup |
| Government | the US Central Intelligence Agency |
| SecureDrop | newsrooms' inboxes for sending documents to journalists anonymously |

Each row shows what the category covers, the **proof page** (the organisation's own announcement of the address)
and how many of the last 14 daily checks reached it. The list excludes onion-only sites and drugs, exploitation and
"sketchy-content" sites. **Read it here** opens a site in the navigator (Tor only, no scripts, forms or sign-in).

## Searching beyond the directory

**Search the onion index too** looks for a topic. Ahmia's robots.txt now asks programs not to use its search, so
HAYAT does not, and says so with a link to search it yourself in Tor Browser. Instead HAYAT finds onion addresses
that organisations publish on ordinary web pages (through Tor; DuckDuckGo sometimes blocks Tor, and then it says so).
Each result is labelled by what it appears to be (news, whistleblowing, search, library, privacy, mail, forum,
crypto, hosting, blog) and marked **unverified** unless the directory lists it. Results offering drugs, weapons,
stolen data, fraud, hacking for hire or abuse are withheld: counted, never linked.

## Checking a link

Paste any onion address in the navigator and the line under it says:
- **✓ Listed**: which organisation, its proof page and reachability;
- **⚠ Lookalike**: it starts or ends like a listed site's address but is different, the usual phishing copy, with
  the real address;
- **Not in the verified directory**: anyone can run an onion site; be careful with logins and payments.

Live (2026-10-26, through Tor): the directory loaded in 23 s (171 sites); "privacy email" found Proton Mail, Riseup,
Privacy International and Systemli, each matched to the directory, plus one unverified mail service.
