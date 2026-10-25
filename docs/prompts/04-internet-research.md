# Internet research upgrade

**Intent:** Let the agent acquire current knowledge instead of relying only on pretrained knowledge.

**Requirements:** Provider-neutral search, Playwright reuse, structured extraction, bounded crawling/budgets, source quality, freshness, corroboration, citations/provenance, injection isolation, memory integration, offline fallback.

**Introduced:** `app/research`, free provider chain, public-only fetch safety, extraction/crawl budgets, GitHub/paper/document paths, and research specialist behavior.

**Current status:** Working and persisted under the knowledge database; paid search is disabled by default.
