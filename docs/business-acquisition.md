# Business acquisition research

HAYAT distinguishes buying an existing business from starting a new one. A
request mentioning a business for sale, distress, failure, undervaluation or
acquisition reaches `business_acquisition_research`, rather than startup ideas.
It also recognizes the reported spelling “i dint ask u that.” Correction-only
turns and acknowledgements preserve the substantive topic; an explicit
replacement changes it. “What did look at?” reports recorded source checks.

Open **Business → Businesses for sale → Research an acquisition** to prepare a
search with a region, industry, optional budget and currency, then send it in
chat. For example: “Research distressed laundromat businesses for sale in Ohio.
Look deep into valuation, seller mistakes and turnaround risks.” No default
location or purchase budget is inferred from the computer's location.

The tool searches and fetches public pages through the existing public-only
fetcher. Normal mode plans three searches; deep mode adds completed-sales and
failure/turnaround evidence queries, and chat increases the page limit from
six to eight. Time budgets are soft checkpoints around requests; individual
requests retain the fetcher's timeout. Search/fetch failures and skipped
coverage remain visible. Source checks retain URLs, retrieval dates and content
hashes. Search snippets alone never become listing evidence.

Directories remain discovery pages. Their linked listing pages may be fetched
within the same page budget, but figures from several businesses cannot become
one company's financials. Listing availability is unconfirmed until separately
verified; sold/pending indicators are flagged. Explicit asking-price, annual
SDE, annual EBITDA and annual-revenue labels are extracted as **unverified
publisher reports** with evidence spans and currency labels. Projections are
flagged. Monthly income, ambiguous scaled/ranged figures and conflicting
figures do not silently become normalized annual earnings.

Fair value, undervaluation and failure causes remain unknown without supporting
records. The workflow asks for normalized earnings, financial reconciliation,
comparable completed transactions, liabilities, lease transfer, capex and
working capital. It does not infer that retiring owners failed or that digital
changes will produce guaranteed revenue. Public availability and publisher
claims are not substitutes for tax returns or bank records.

`business_valuation_scenario` calculates a range only from explicit supplied
annual normalized SDE/EBITDA, an assumed multiple range and adjustments. Its
formula is earnings × multiple − debt + cash − capex − working capital. This
is an assumption scenario, not a certified appraisal. It rejects non-finite
and negative inputs and never mixes revenue, monthly MRR, SDE and EBITDA
multiples. The owner must confirm whether the asking price is an equity price,
the transaction structure, excluded assets and which adjustments are warranted.

Privacy/local-only mode prevents public listing research. Research and
valuation do not contact sellers, make offers, purchase businesses, or train
model weights. They are exposed through the normal tool permissions and audit
path. A bounded public search cannot cover every business or prove a bargain.

The live October 8 probe successfully fetched public Ohio discovery pages,
and recorded one source/provider failure. This demonstrates network plumbing,
not verified investment opportunities. The source separation also follows the
SBA's instruction to investigate existing businesses and review contracts,
leases, cash flow, inventory and valuation before buying:
[SBA business acquisition guidance](https://www.sba.gov/counseling/plan-your-business/).
