# Jobs and applications

HAYAT finds jobs on public job boards, reads each posting and your CV, scores how well each one fits
(with every part of the score shown), prepares applications on its own, and tracks them. **Sending
an application always waits for your confirmation**, because it acts as you toward an employer.

Open it from **Studios → Jobs**, or ask in chat.

## Worldwide, deeper searches and scrolling

In **Matches**, leave Location blank or choose **Worldwide** to view all advertised locations. The editable field accepts any city/country name, including countries outside its suggestions. Separate alternative locations with `;`, for example `Tokyo; Singapore`. Worldwide removes the search-location restriction; it does not mean every remote role accepts applicants from every country. Your own preferences and posting restrictions still affect eligibility.

Choose **normal**, **deep** (the GUI default), or **maximum**, then **Search now**. Deep reads up to 5 pages per supported query; maximum reads up to 20. Shared limits are respectively 100 requests/180 seconds and 240 requests/300 seconds, with a fair request allowance for each configured source. Up to 6 or 12 alternative role terms are searched. Page counts, publisher errors and coverage limits remain visible. An in-flight request may finish after the time limit or Stop click; no later request starts. **Stop search** retains retrieved pages. Maximum is bounded, not an exhaustive search of the internet.

Himalayas uses documented search pages or opaque browse cursors; Jobicy uses its current free `/api/jobs` endpoint without a commercial key. Arbeitnow and The Muse use numbered pages. Other feeds and followed employer boards expose snapshots; deeper mode cannot invent extra pages. Repeated pages/cursors stop pagination, and later-page failures retain earlier results. The existing fetcher respects publisher rate limits and robots restrictions.

Chat no longer cuts every report to 10 jobs. `job_search` returns up to 100 by default (configurable to 500), and `job_matches` accepts `offset` and `limit` to retrieve every saved match. The GUI shows 200 per page with **Previous / Next 200**. Changing a filter resets to the first page. Examples: “deep search for remote python jobs worldwide” and “maximum search for analyst jobs in Japan”.

All six Jobs tabs have page-level vertical and horizontal scrolling. Tables, job reports and the profile form retain independent scrolling. Drag the outer scrollbar to reach controls at small sizes; use Shift+wheel horizontally over the page canvas, or focus it for Page Up/Down and Home/End.

For sources without a configured free API, choose **Employer boards**, **LinkedIn**, **Indeed** or **Web**, then **Browse platform**. This opens a real role/location query. Employer boards searches public Greenhouse, Lever, Ashby, Workday and SmartRecruiters results. Optionally select **Read page and scroll** to use the visible HAYAT browser, read its DOM and scroll up to 8 times. Page text is an unverified lead, not an imported/scored posting. Use **Sources → Discover** and follow a supported employer board to include its roles in later searches. Login, CAPTCHA and country selection remain manual; this control does not apply or submit. No paid service is required.

**Human verification:** choose Google, Bing or DuckDuckGo using **Web engine** (the GUI starts with DuckDuckGo). If the chosen site requests verification, HAYAT pauses and keeps the visible tab/session open. Complete the check there, then click **Continue after verification** in Matches. Continuation reads the same tab without reloading the search, retaining its cookies. It refuses a closed/replaced session, changed privacy route, different query or unrelated page. If the check remains, it stays paused. No paid solver, identity disguise, proxy rotation or automated challenge solving is configured. See verified public routes and continuation checks.

**Automatic fallback:** select **Auto** in Web engine for Web or Employer boards, then Browse platform. This enables visible browser reading and tries DuckDuckGo, Bing and Google, stopping at the first accessible page. Each provider is attempted at most once per run; blocked/unavailable engines receive a session-local five-minute cooldown. Each later provider opens in another tab, preserving blocked tabs. If every route is blocked, the last usable challenge tab is restored for manual completion. The GUI/chat show the attempt trace; errors and cooldown times remain visible. **Stop search** prevents another provider starting after the current operation finishes. Auto has a 180-second between-operation budget (an in-flight operation may finish later). It cannot silently use a direct browser while a privacy proxy is active; use route-aware APIs in that mode.

Auto switches to other public providers; it does not solve CAPTCHA, override website permissions, import jobs or submit applications. An accessible page is still unverified search evidence, not proof of a matching open job. Tools can request `job_browser_search(engine="Auto", scan=true, query="analyst", location="Japan")`.

**Access recovery:** HAYAT distinguishes human verification, sign-in, rate limits and denied access, using visible challenge widgets, known checkpoint paths and page-level notices. A long job description mentioning verification no longer pauses the search by itself. **Search free APIs** runs the current role/location/depth search while preserving the pending browser session. Provider cooldowns are stored locally and survive restart: HTTP 429 defaults to 60 seconds, HTTP 503 to 30 seconds, denied API access to 15 minutes and robots-disallowed retrieval to an hour. A valid publisher `Retry-After` value controls 429/503 retry time. **Force/maximum searches respect active cooldowns**; other providers and saved jobs stay available. ATS boards sharing a provider also share its cooldown. Retry times are shown in UTC with their offset.

In **Sources**, select a feed or followed employer board to see its access details in a separate scrollable panel: enabled/paused preference, last observed access state, last error, retry time and official website. **Open official site** opens that known provider/board in your regular browser for manual reading or verification. It does not clear restrictions, copy browser cookies into the API client or import postings. The source controls wrap at compact sizes; employer-discovery results stay in their own panel. In tools, `job_sources(source="jobicy")` inspects access and `job_source_open(source="jobicy")` opens the manual route. Unknown URLs and malformed board names are rejected.

Checked against [Himalayas' official reference](https://himalayas.app/docs/remote-jobs-api), [Jobicy's public API guide](https://jobicy.com/jobs-rss-feed) and [Arbeitnow's documentation](https://www.arbeitnow.com/blog/job-board-api). See implementation and verification.

## Start

1. **Read your CV:** "read my CV from C:\Users\me\cv.pdf" (PDF, Word, text or Markdown), or
   **Profile → Read my CV…**. HAYAT reads:
   - your contact lines and links;
   - your skills, with whether they sit in a Skills section;
   - your spoken languages;
   - your years of experience: date ranges merged, so overlapping jobs count once, and degrees left
     out;
   - the roles you have held, your education, and a level (junior under 2 years, mid 2–5,
     senior 5–9, lead 9+).

   A role you have held implies its core skill: "Data Analyst" gives "Data analysis".
2. **Say what you want:**
   - "I want remote data analyst jobs in Hyderabad"
   - "my minimum salary is 80k usd"
   - "only remote jobs"
   - "add docker to my skills"
   - "remove sales from my skills"

   The Profile tab has every preference:
   - your places and the countries you may work in;
   - level;
   - excluded words and companies;
   - notice period, work authorisation and salary expectation, used in the answers sheet.
3. **Search:** "find data analyst jobs", "find me remote python jobs in India", or **Search now**.

## Where the jobs come from

Each source is a keyless public API whose robots.txt allows reading. Each is credited on every job,
and you always apply through the posting's own page or address, as each source asks.

| Source | Covers | How HAYAT asks |
|---|---|---|
| Remote OK | remote jobs worldwide, tech-heavy | by tag, per search term |
| Jobicy | remote jobs | by tag, per search term |
| Himalayas | remote jobs with location limits | its search endpoint, per term |
| Working Nomads | remote jobs worldwide | the latest list |
| The Muse | US-led, many fields | bounded numbered pages |
| Arbeitnow | Europe, mostly Germany, many in German | numbered pages in deep/maximum mode |
| Greenhouse, Lever, Ashby | one company's own careers board | when you follow it: "follow stripe on greenhouse" |

**Not used:**
- **Remotive:** its robots.txt disallows `/api/`.
- **Adzuna and other keyed APIs:** they put the key in the web address, where HAYAT's web log
  would record it.

Background rounds reuse each source for an hour; explicit queries, GUI searches and deeper searches refresh it. Cross-board duplicates require strong shared evidence; distinct requisitions remain separate.

## What HAYAT reads in a posting

- **Skills, by section.** Under "Requirements / must have / you have" they are required; under
  "Nice to have / preferred / bonus" they are a plus; anywhere else they are mentioned.
  - The same skill vocabulary reads postings and CVs, so a match is the same name on both sides.
  - Words that are also ordinary English count only in the spelling that means the skill. "You
    will excel at" is not Excel, "react quickly" is not React, and "Spring 2024" is not Spring Boot.
- **Years asked** ("3+ years of experience") and **level** from the title.
- **Work mode:** remote, hybrid or on-site.
- **Where you must be:** "US only", "must be based in the EU", time zones, work authorisation, and
  whether visa sponsorship is offered.
- **Languages:** the languages it requires, and the language it is written in. A posting written
  in German needs German.
- **Pay:** from the board's fields or the text, for example "$70,000 - $90,000 per year".
- **Application email,** when the posting gives one.
- **Warning signs,** counted only when aimed at you. Examples: "selected candidates must first pay
  a registration fee", "send your bank details", "contact us on Telegram", commission-only work.
  "We will not pay fees to agencies" and a gift-card company describing its product are not
  warnings. A warning means "check carefully", not proof of a scam.

## The match score

**Hard checks come first.** Any of these makes a job **not eligible**, whatever its score (it is
capped at 25):
- it is limited to places that are not yours;
- it is on-site or hybrid and you want remote only;
- it needs a language you do not have;
- it contains a company or word you excluded.

Then the parts:

| Part | Weight | Reads |
|---|---|---|
| Skills | 35 % | required skills count double, nice-to-have and mentioned once; soft skills ("communication") half |
| Duties | 15 % | each of its responsibilities against your CV's own lines (see the deep look) |
| Title | 17 % | words shared with your target titles; roles you held count for less |
| Experience | 13 % | your years against the years asked |
| Level | 10 % | your level against its level |
| Pay | 5 % | its pay against your minimum, in one currency |
| Freshness | 5 % | how recently it was posted |

**Your own choices teach it a little.** After at least three choices (jobs you prepared, opened or applied to,
and jobs you hid), title words from what you pursued lift similar jobs and words from what you hid lower them,
by at most 6 points either way. The reason is shown: "like jobs you hid (marketing)".

- **Thin evidence is pulled toward neutral:** a posting that names two skills, both yours, is not
  "100 %".
- **Missing required skills are listed.** Soft skills are never among them, because every posting
  names them and no CV proves them.
- **Verdicts:** 75+ is strong, 60+ good, 45+ a stretch, below that weak.
- The score is HAYAT's reading of two texts, not the employer's decision.

**Measured on 2026-10-13** with a sample data-analyst CV and live boards: 139 relevant jobs from six
boards in about 15 seconds. The real "Data Analyst" posting that named nearly all the CV's skills
came first. 86 were not eligible: limited to the US, UK, Germany or the Philippines; on-site; or
written in German.

## The deep look at one job

"Tell me everything about job 12", "is job 12 good for my career", "how do I prepare for job 12", "does job 12
fit my CV", or the **Deep look** tab beside a job. It reads the posting, your CV, the jobs HAYAT has collected
and your own record:

- **The role:** its responsibilities, from the lines under "What you'll do" (sub-headings such as "Data Quality
  & Validation" are left out), how success is measured, and who you would work with or report to.
- **How it matches your CV:** every duty against your CV's own lines, marked ✓ shown, ~ partly or ✗ not yet,
  with the line that shows it. Words that mean the same are matched: build and develop, reporting and reports,
  monitor and track, metrics and KPIs, present and communicate. The result is an alignment percentage, which
  is also the score's "duties" part.
- **What it could do for you:**
  - skills you would build (what it asks that you lack);
  - the level step;
  - its pay against your minimum and against similar postings;
  - common next roles;
  - whether it fits your preferences.
- **Around it, from HAYAT's own collected postings:**
  - how many similar jobs it has seen in the last 30 days against the 30 before;
  - their pay (25th, 50th and 75th percentile, once three or more show pay in one currency);
  - the skills they ask most;
  - how many are remote.

  This is a sample of what HAYAT searched, not the whole market. Career paths are common patterns, not
  predictions.
- **Your history:** what happened with similar roles you applied to or hid. Nobody publishes who was hired for a
  role, so HAYAT does not guess it.
- **Instructions applicants miss:** what the posting asks of the application: attach a portfolio, a subject
  line, a deadline, a question to answer, or a word to mention. Some postings hide a word like this to see who
  read them. On 2026-10-13 a live Remote OK posting asked applicants to "mention the word BRIGHTEST", and
  HAYAT flagged it.
- **Why applications to it fail:**
  - the instructions, and the exact names of required skills that screening systems look for;
  - place and language limits;
  - years asked;
  - an old posting (apply soon or not at all) or a new one (apply early);
  - generic letters.
- **The first 90 days,** from its own duties: learn the tools and people (and close gaps), take on the core
  duties, then deliver against how success is measured.
- **Preparation:**
  - one interview question per skill family it asks for, and a "tell me about a time you…" question per duty,
    pointing at the CV line to use or saying you have no clear example;
  - standard questions, with the pay range of similar postings for "salary expectations";
  - a study plan for missing skills: why each matters (required here; asked by N similar postings), the
    official documentation to start with, and a small practice project;
  - CV tailoring for this job: what to put first, which lines to move up, which duties your CV does not show
    (add only if true), and a headline;
  - questions to ask them, such as the pay range if it is not stated.
- **Ask HAYAT's view** adds a frank summary by HAYAT's local model, from these facts only, never inventing facts
  about the company, past hires or the future.

## CV review

"Review my CV", "what should I change in my CV", or the **CV review** tab. It checks:
- your contact lines;
- a Skills section, and skills named in your text but missing from it;
- experience lines that show results with numbers (at least 3 in 10);
- lines that start weakly, with a stronger opening ("Responsible for…" becomes "Led / Owned");
- length and dates.

It also lists the skills your best matches ask for most that your CV does not name (add only the true ones),
and what to put first for your best fits. It never rewrites facts.

## Applying

- **"Prepare an application for job 12"** writes a packet in `data/jobs/applications/<number>-<company>-<title>/`:
  - `cover_letter.md` and `cover_letter.docx`. These use only facts from your profile and the
    posting. A skill the posting asks for that your profile lacks is never claimed.
  - If HAYAT's local model is available, it smooths the wording. Its version is kept only if it
    adds no skill you do not have and still names the company; otherwise the template stands.
  - `answers.md` holds answers for the application form, including a list of what the posting asks
    for that your profile does not show, to answer honestly.
  - `posting.md` is the posting with its source and link.
  - Your CV is attached **unchanged**.
- **Postings with an application email:** "send my application for job 12" puts the email (to, subject,
  letter, attachments) in the **confirmation window**. Nothing is sent until you confirm it there, or
  say "confirm action N" at this computer. Then it is emailed from your own account (set up in
  Settings) and marked applied.
- **Postings with a web form:**
  - "Open job 12" opens the employer's page.
  - "Fill the application form for job 12", or **Fill form**, opens it in HAYAT's visible browser and fills
    what HAYAT knows: your name, email, phone, links, location, years, the cover letter, and the notice period
    and salary expectation you set.
  - It then stops. It attaches no file, because some application systems upload a file the moment it is chosen.
  - It answers no question about the law or you: work authorisation, sponsorship, gender, ethnicity,
    disability, veteran status, age.
  - It ticks no box and presses nothing.
  - You check every field, attach the CV, answer the rest and submit, then say "mark job 12 applied".

## Tracking and follow-ups

- **Statuses:** saved, prepared, opened, applied, interview, offer, rejected, withdrawn.
  - "Mark job 12 as interview", "I got an offer from job 12" or "job 12 rejected me" change it;
    every change is kept with its time.
  - "My applications" or the Applications tab lists them.
- **Follow-ups:** a week after you apply, if nothing has changed, HAYAT drafts a polite follow-up
  (`follow_up.md` in the packet) and tells you. It never sends it.

## The automatic round

Every six hours while HAYAT runs (the first round a few minutes after start), HAYAT:
- searches with your terms;
- scores new jobs;
- tells you about new matches at or above your "tell me from score" (default 70), each only once;
- drafts due follow-ups;
- prepares packets for up to three new strong matches, **only if you turned that on**: "prepare
  applications automatically", or the Profile tab. Turn it off with "stop preparing applications
  automatically".

It never sends anything. Turn the round off with `jobs: {background: false}` in
config/settings.yaml. Run it now from **Sources → Run the automatic round now**.

## Ask in chat

| You say | You get |
|---|---|
| "Read my CV from C:\path\cv.pdf", "my job profile" | What HAYAT understood from your CV, and your preferences |
| "Find python jobs", "find me remote data analyst jobs in India", "any new jobs for me" | A search and the best matches |
| "Show my job matches", "what jobs match me" | The ranked list |
| "Show job 12", "why is job 12 a good match" | What it asks for and every part of its score |
| "Prepare an application for job 12" | The packet |
| "Send my application for job 12" | The email in the confirmation window |
| "Open job 12", "mark job 12 applied", "my applications" | The page, the tracker |
| "Follow stripe on greenhouse", "which job sources do you use" | Company boards, sources |
| "I want remote data analyst jobs in Hyderabad" | Your profile with the new wish, and the saved jobs that fit it now (any part of a preferred place counts, and so does a remote role with no country restriction) |

When a job matches a location filter through the employer's own words rather
than its listed location, the line says so: "United States (listed); the
posting's text names India". A board listed one AssetIntel role for the US
while its description said "work remotely - anywhere from India" (2026-10-24).
Without a CV the profile says so instead of "read from your CV".

Code: [jobs package](../app/jobs/__init__.py), [Jobs window](../app/gui_jobs.py), tests in
[test_jobs](../tests/test_jobs.py).

## Sponsorship, STEM OPT and H-1B research

Set **Profile → US work status** yourself, or say **"I'm on STEM OPT"** or
**"my work status is H-1B"**. This is a self-reported search preference, not a
finding about your legal status. HAYAT distinguishes authorization now from
future sponsorship needs. A posting explicitly excluding sponsorship now or in
the future is flagged for an OPT/STEM OPT profile. Clearance mentions do not
silently become citizenship requirements, and citizen-or-permanent-resident
wording is not reduced to citizen-only.

Select a job and open **Sponsorship & travel**, or say **"check sponsorship for
job 12"**. The report separates:

- statements from the posting, including conflicting statements and programs
  merely mentioned;
- locally imported historical employer decisions, with fiscal years and source
  identity;
- questions about the actual hiring entity, location, remote restrictions,
  future sponsorship, pay, travel and relocation that still need an answer.

For STEM OPT, the checklist links to [USCIS requirements](https://www.uscis.gov/working-in-the-united-states/students-and-exchange-visitors/optional-practical-training-extension-for-stem-students-stem-opt),
[ICE practical training guidance](https://www.ice.gov/sevis/practical-training)
and [Form I-983 instructions](https://www.ice.gov/doclib/sevis/pdf/i983Instructions.pdf).
It calls out E-Verify, the signed training plan, hours, compensation and
supervision. Check the current rules and your individual arrangement with your
DSO or immigration counsel. A posting or an E-Verify search result alone does
not establish eligibility.

**Sources → Import sponsor CSV** accepts a locally downloaded USCIS-style
employer-history extract. Required columns are `Employer`, `Fiscal Year`,
`Initial Approval`, `Initial Denial`, `Continuing Approval`, and
`Continuing Denial` (plural count headings are also accepted). Get official
history from the [USCIS Employer Data Hub](https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub).
An import preserves the filename, SHA-256 and import date. HAYAT labels the
file as locally supplied; it does not authenticate a user-selected CSV's
publisher. Malformed counts or years reject the whole import and leave the
previous history intact. Matching normalizes case and punctuation and otherwise
requires the legal employer name; a subsidiary is not silently matched to its
parent. Importing a file replaces the active extract; include all desired
fiscal years in that file.

**"Find H-1B sponsors"** or **Sources → Sponsor research** lists research leads
from positive initial approvals in the newest fiscal year of that extract.
**"Research sponsorship at Acme Data"** checks a specific legal name. History
requires an imported extract or the explicit setup below. An absent record is unknown, and an old approval does not prove a
current opening or sponsorship offer. [DOL LCA/PERM disclosures](https://www.dol.gov/agencies/eta/foreign-labor/performance)
are linked separately: those filings are not USCIS petition decisions. HAYAT
does not turn either dataset into visa, lottery or hiring probabilities.

## Sorting and the updated window

The Matches tab filters saved results by remote mode and the posting's stated
sponsorship, and sorts by score, newest or pay. **"Show my job matches by
salary"** also works in chat. Sorting considers up to the highest-scoring 500
stored matches before applying the display limit. Pay uses the annualized
midpoint in your profile currency; unknown pay and other currencies follow,
with no invented exchange rates. Hourly annualization assumes 2,080 hours;
daily 260 days, weekly 52 weeks and monthly 12 months. These are comparison
assumptions, not guaranteed earnings.

Text panels and tables have scrollbars. The match/detail divider is draggable,
and the Profile preferences scroll at compact sizes. Superseded read requests
are discarded, selected-job details cannot be replaced by a late result for
another job, and repeated clicks do not queue duplicate active application
actions. Profile reads and save responses preserve edits made while work is
running; a successful save normalizes only fields that still match the submitted
values. Profile file reads and atomic replacements share a lock so Windows
refreshes cannot block a save with a sharing violation. Re-fetching an existing
posting now refreshes its reading and score.

**Sources → Discover** checks an employer's public Greenhouse, Lever or Ashby
board. Turn on **Follow if found** to add it to future searches. These are
public employer-board opportunities, not access to private or unadvertised
positions. Confirm the board's identity before applying.

Browser assistance now preserves existing answers and leaves disabled or
read-only fields for review. It still leaves legal declarations, consents,
uploads and submission to you. Run a repeatable real-browser check with a
generated applicant and an isolated store:

```powershell
.venv\Scripts\python.exe scripts/verify_job_hunting.py --live
```

This fills and reads back a generated form without submitting it, prepares the
application packet, checks live public feeds and employer-board discovery,
then exercises matching, pay sorting, sponsorship research, CV review and the
learning plan. Results go to `.test-runs/job-hunting-live/verification.json`.
The owner's CV, profile, accounts and applications are not used.

Verification on October 18, 2026: the full suite passed **5,863 tests** with
**33 opt-in skips** (screenshots, external live checks and local-model checks),
with no failures. A final **155-check regression** passed against the last
parser and layout changes, including the Jobs captures, real tool-registry
calls, browser support, tool contracts and documentation. The runs overlap;
these are not additive counts of unique tests. Ruff and whitespace checks
passed. Reports are `.test-runs/job-hunting-full-suite.xml` and
`.test-runs/job-hunting-final.xml`.

The separate real workflow verification passed: all six public feeds responded,
the isolated store held **116 distinct relevant jobs**, and the public Stripe
Greenhouse board returned **731 roles** at the time checked. Chromium filled
and read back a generated applicant's form, preserved existing/noneditable
answers, prepared a Word/Markdown packet, and left visa declarations, uploads,
consents and submission untouched. No application was sent. Compact Sources
and scrolled Profile captures were reviewed with their controls visible.

The official USCIS employer-data page and download returned HTTP 403 to this
machine. After the request to find the data independently, a pinned
[public FY2024 mirror](https://github.com/karthiks233/H1B/tree/ec36bd986f6684eae6325a73a3097940aad8ae49)
was installed on October 18, 2026: **46,956 named rows, 40,665 normalized employer
names**, with one unnamed petitioner row explicitly omitted. This is historical
mirror data, **not independently verified against USCIS**, and does not establish
current sponsorship, openings, employer identity or an applicant's eligibility.
Rows sharing an employer name aggregate counts without disambiguating tax IDs
or worksites. HAYAT displays the source, fiscal year, import date, hashes and
this qualification with its research results.

The reproducible, opt-in installer is:

```powershell
.venv\Scripts\python.exe scripts/setup_sponsor_history.py
```

It fetches only the pinned data file, verifies SHA-256
`17bbdbf3abaa98defc8817626ba8821a69cb5d4942fe1d2229a24deb3db7c17e`,
normalizes its UTF-16 tab-separated records, validates counts, archives the raw
extract under `data/jobs/sources/`, and atomically installs
`data/jobs/sponsor-history.json`. Existing history is preserved unless
`--replace` is explicit; replacement creates a backup. No code from the mirror
is executed. Changing fiscal years or refreshing the mirror requires reviewing
and pinning a new source; the installer never calls FY2024 data current.

The installed hashes, normalization and actual JobCenter lookup were checked
separately; the report is
`.test-runs/pending-checks/sponsor-history-verification.json`.

Follow-up opt-in verification and repairs are tracked in
Completing the opt-in checks.
