
## Prior art, novelty and feasibility (2026-11-09)

**Prior art** (Invention window → **Prior art**, or "has anyone patented …?", tool `invention_prior_art`) checks
the idea in the goal box:

* patents from Google Patents public pages (found through HAYAT's search chain; robots.txt allows /patent/
  pages only) with number, dates, owner and abstract, and papers from OpenAlex with their research field;
* which of the idea's features already appear in that prior art and which look new, the closest items and
  how much of the idea they share (rare words weigh more);
* how other research fields work on the same thing;
* an evidence-based feasibility reading (researched only / patented / crowded, how recent the work is) and
  risks (close patents: check freedom to operate, not legal advice; regulated areas; no evidence it works);
* next steps: the one-sentence claim, prototype the new part first, the number that would prove it.

A diagram shows each feature as known (with how many items contain it) or new, beside the fields working on it.
Live 2026-11-09, "a bicycle that charges a phone from regenerative braking energy": 6 patents, the closest
(KR102001207B1) sharing 87 %, "charges phone" the only part not found. When OpenAlex or the search engines
refuse (OpenAlex's free keyless budget is shared by everyone on the same network address), the report says
what was not searched instead of reporting "none found". Prior-art search is never complete.
