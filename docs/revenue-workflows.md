# Freelance, sales and revenue workflows

For persistent customer briefs, service recommendations and local action execution directly in chat, see [Customer work in chat](customer-chat.md).

For income ideas, contribution calculations, recurring revenue, persistent actions
and Osiris, see [Income planning and Osiris](income-and-osiris.md).

Open **Chat actions → Revenue workspace**, or **More tools → Revenue**. The
workspace lists 115 named workflows, with search,
area filters, operation type, required inputs and a button to prepare an editable
chat request. It also shows the existing lead pipeline, revenue experiments,
campaign review/delivery states and saved artifacts. Generated drafts and
bundles can be shown in chat.

## What runs now

| Operation | Working behavior |
|---|---|
| Discovery | Public search on requested Upwork, Contra, LinkedIn, Fiverr and job-board domains; source links, deduplication, per-provider failures and check dates. Results are candidates to verify, not authenticated live job data. Fiverr results are treated as service listings/market research. |
| Scoring | Explicit heuristic factors with missing signals disclosed. No invented close, churn or viral probability. Historical outcome rates include sample size and a Wilson uncertainty interval. |
| Drafts | A configured local model creates tailored Markdown artifacts, saved and read back with a verified ZIP. Actual profile, job description, prior messages, source facts and measurements are supplied inputs. Nothing is sent or published. |
| Builders | A configured model creates actual local source/data/template files, a manifest with hashes and a verified ZIP. Python and JSON syntax is checked; dependencies, runtime behavior and deployment still need validation. Products are local drafts/prototypes, not live paid services. |
| Contracts | Fill an owner-supplied approved template. Missing placeholders are reported. No legal clauses, signatures or approval are invented. |
| Books | Quotes and invoices use the existing client ledger and PDF renderer. Profit, income streams, client cash receipts and recorded hours use the existing books; no new or duplicated payment source. |
| Experiments | Persistent variant/cohort snapshots, response/conversion rates and measured contribution per hour. Reusing a key replaces a snapshot, so cumulative counters are not counted twice. Different audiences and small samples do not prove a causal winner. |
| Content and affiliate metrics | Store owner-reported performance measurements by stable content/campaign key. Missing denominators remain unknown. No automatic platform analytics connection is claimed. |
| Campaigns | Save exact email recipients, bodies and timezone-aware schedules. Review approval is bound to content. Each explicit single-message send requires exact-message approval and uses the configured email tool. Ambiguous delivery is held for review and never blindly retried. |
| Calendars | Local social posting and marketplace listing review calendars. Publication receipts are owner-reported. These tools do not automatically publish to social platforms. |
| Audits | Bounded public-page evidence: title, description, headings, links, fetch timing/size and selected configuration headers. No exploitation, complete tech-stack inference, full-site ranking or Core Web Vitals claim. |
| Monitoring | Capture and compare explicit public URLs on request. Snapshots persist; a recurring service has to be configured separately. |
| CSV cleanup | Trim fields and optionally remove exact duplicate rows. Validate row widths, preserve the source and save cleaned data plus a change report. |
| Clips | Delegate to the existing video clipping engine and return actual local clip paths for chat previews. |

The model is given only a relevant subset of the new tools when planning, rather
than all 115 schemas on every turn. Tools still execute through the normal
registry, permissions, execution identity, budgets and audit trail. Temporary
teams may use the approved discovery/scoring tools; they cannot send campaigns
or write business artifacts.

## Chat examples

Natural requests include:

- Find freelance jobs for Python automation.
- Show my revenue dashboard.
- Show my sales pipeline.
- Prepare a proposal from this job description and my actual experience.

Named tools also accept explicit JSON arguments. The workspace and
`revenue_tool_catalog` explain each workflow. `goal` is the brief/query/reply,
`context` contains supplied profile or reference material, `data` contains
actual facts or metrics, and tracking operations use `action` and a stable `key`.

```text
pricing_engine {"data":{"hours":10,"hourly_cost":20,"margin_percent":30,"fee_percent":10,"currency":"USD"}}

proposal_ab_test {"action":"record","key":"concise-cohort-1","data":{"variant":"concise","sent":20,"responses":4,"wins":1}}

content_roi {"data":{"attributable_revenue":200,"production_cost":100,"distribution_cost":20,"currency":"USD"}}

outreach_campaign {"action":"list"}

revenue_dashboard {"data":{"period":"this month"}}
```

The numbers above are examples, not the owner's results. Financial calculations
use explicit assumptions and finite, nonnegative input validation; no currencies
are combined. Client profit remains unknown without allocated costs. Experiment
contributions are kept separate from cash books to avoid duplicated revenue.

## Setup and limits

Public discovery needs the existing search providers and internet access. Login
walls, unavailable pages and provider errors become gaps. External accounts,
private customer data and analytics are not connected by merely registering
these tools. Email requires the existing SMTP/contact setup. Software bundles
need their README setup and runtime tests before use. Data products require
actual supplied data, provenance and explicit license/permission. Nothing here
collects money, establishes guaranteed demand or predicts virality reliably.

The full registered tool list is in the
[generated runtime reference](generated/runtime-reference.md). Implementation:
[revenue engine](../app/revenue.py), [catalog](../app/revenue_catalog.py),
[calculations](../app/revenue_metrics.py), [input guide](../app/revenue_inputs.py)
and [workspace](../app/gui_revenue.py).

Validation covers persistent records, source/domain filtering, unknown scores,
ROI/margin math, artifact integrity, traversal and truncated-generation rejection,
CSV preservation, shared bookkeeping, campaign approval, cancellation and
duplicate/ambiguous send handling. Tests do not send real messages or create
fictional transactions in the owner's books.
