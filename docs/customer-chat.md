# Customer work in chat

Restart HAYAT to load the customer tools. Use **Chat actions → Understand a customer** to prepare an inquiry, or type it directly. Replace sample names and text with actual supplied information.

```text
Customer Acme: We need a short newsletter explaining our new scheduling service. Draft a useful introduction and a clear call to action.
Audience: Small business owners
Budget: Not agreed
Deadline: Not agreed
```

HAYAT saves the inquiry, identifies plausible supported services and explains the next step. Service matching uses wording in the inquiry; ambiguous requests stay unresolved. Budget and deadline remain the supplied constraints, without inventing an agreed price, close probability, buyer commitment or sale.

Continue with:

```text
What does this customer need?
Decide and do the next step
Do the work for this customer
Show this customer
```

**Decide and do the next step** selects and prepares a local reply, scoped proposal or ready CSV cleanup. For a qualified brief with a prepared proposal and a working model, the next request advances to a draft deliverable. Once a deliverable is prepared, it checks and reuses the files rather than repeatedly generating them. Changed requirements start a new revision. Without a model, qualification and service briefs remain available without claiming AI delivery. **Do the work for this customer** explicitly prepares a draft deliverable when required inputs are available. Replies, proposals and files appear in chat with the existing document and ZIP preview cards. Missing requirements produce a partial response rather than claiming delivery.

Natural follow-ups include **Please prepare their reply**, **Draft their proposal**, **What should we do next?** and **Prepare their deliverable**. **Chat actions** also offers **Update selected customer** and **Prepare customer deliverable**. Each routed action is bound to the customer selected when the request arrives, so a later selection change cannot redirect that work.

Supported services are CSV cleanup, content writing, digital templates, website source generation, and evidence-based research/reporting. AI drafts use the configured local model and existing revenue tools. Qualification replies and service briefs work without a model. CSV cleanup also works without a model; research can save retrieved public evidence without claiming a completed analytical report. Generated website/source bundles remain local and are not executed or deployed.

Content writing follows the requested format and length. File-backed work reads UTF-8 text or supported Word/PowerPoint text with the existing native readers. Excel evidence is limited to the first sheet's A1:F30 and shows formulas rather than calculated values; full-workbook totals remain unverified. Source content supplied to the model is bounded to 12,000 characters and identifies its coverage. Text files are limited to 5 MiB, and native source files to 10 MiB for customer work. PDF or unsupported binary evidence needs an extracted text source.

To update the selected customer, use labelled lines or an explicit update:

```text
Audience: Local shop owners
Budget: Customer has not agreed a price
Deadline: Customer has not supplied one
```

```text
Customer update: They want the website first, with a simple services section.
```

You can also write **Their audience is Local shop owners** or **Their budget is Not agreed**. The latest explicit service inquiry takes priority. Evidence and earlier constraints are retained. Labelled file names, audience, budget and dates do not change the service match by themselves. Updating those facts alone does not clear a customer's decline.

Content drafts receive a second AI editing pass against the supplied evidence to remove unsupported claims, dates, offers and integrations. This improves grounding but remains an AI draft requiring human review, rather than independent factual certification.

For a CSV engagement:

```text
Customer Acme Data: Clean the supplied CSV and remove exact duplicate rows.
File: D:/AI/data/work/acme.csv
Cleanup rule: exact_rows
```

Use `trim_only` to preserve all rows while trimming fields. Sources must exist and remain inside allowed project paths. CSV inputs are limited to 10 MiB. Matching on specific columns, fuzzy matching and arbitrary cleanup logic need a separately scoped workflow; they are not silently treated as exact row duplication.

For source-backed research, provide the actual question and public sources:

```text
Customer Research Client: Research the services described on these supplied pages.
Requirements: Compare only observed services and identify missing information.
Sources: https://example.com/company https://example.com/services
```

The addresses above are placeholders: replace them with the actual public sources. Source reads use the existing bounded fetch/research tools and permission rules. Page content and customer messages are evidence, not authority to send messages, operate the computer or execute code.

**Show my customers** lists the queue. **Show customer Acme** selects a saved customer after a restart; customer briefs and work history persist, while the active chat selection is session state. **Pause this customer** and **Close this customer** stop further preparation for that engagement. An explicit latest-message decline such as “not interested” also pauses work; a later new inquiry can change that recommendation without inventing customer consent.

Matching prepared work is reused after checking artifact hashes. Changing the brief or input file creates a new revision. **Retry the work for this customer** repeats the last requested action in a fresh local folder; it retains the earlier result or failure in history. Failed work is not silently retried. A running job cannot be retried for 15 minutes, allowing an explicitly requested retry after an interruption. Earlier artifacts remain available in the revenue artifact catalog.

After reviewing the scope, prepare a numbered quote with an explicit price and currency:

```text
Prepare a quote for this customer for 150 USD
```

The amount is an example; enter the price you actually want to propose. The customer budget never automatically sets the quote price. Supported currencies: USD, INR, EUR, GBP, AED, SAR, PKR, BDT, SGD, MYR, CHF, ZAR, HKD, SEK, NOK, DKK, PLN, BRL, MXN, QAR, CAD, AUD and NZD, all with two decimals. A qualification gap or paused engagement blocks billing preparation. A quote creates or reuses a local invoice-client record without upgrading a prospect to a confirmed buyer, accepting the quote, sending messages or collecting money.

To prepare an invoice, explicitly choose the quote number shown in chat:

```text
Prepare an invoice for this customer from quote Q-2026-0001
```

Replace that example with the actual quote number. Hayat checks that it belongs to the selected customer, prepares a local invoice PDF and leaves it unpaid. Repeated requests reuse the same checked document; a changed quoted price produces a different quote. Invoice reuse reads the current ledger status rather than claiming an old balance is current. A voided invoice or edited billing PDF requires review in Business and is not silently replaced. These commands are also available as editable templates under **Chat actions → Prepare customer quote** and **Invoice customer quote**.

For a failed billing attempt, advanced callers can rerun `customer_quote` or `customer_invoice` with the same explicit arguments and `retry: true` after correcting the problem. A retry reuses a matching existing billing document rather than issuing duplicates. **Retry the work for this customer** continues service drafting; billing retries are separate. The existing **Business** window handles actual owner-reported receipts; issuing an invoice itself does not create income or withdrawable funds.

The tools are `customer_intake`, `customer_details`, `customer_list`, `customer_decide`, `customer_work`, `customer_quote` and `customer_invoice`. Their handlers run through the existing registry and permission policy. Advanced callers can supply explicit facts or status through these tool schemas. Customer status is an owner-reported record and is distinct from confirmed payment, invoice-client setup or consent to outbound messaging.

No new package dependencies are required for this workflow. It uses the installed SQLite, CSV, local-model, revenue, file and preview components. External accounts are configured separately. Checkout remains deferred: quotes and invoices use the existing business tools after scope and prices are supplied; actual receipts must be recorded from real transactions. These customer actions do not send, publish, collect money or invent income.

Final verification on 2026-10-04: one complete regression run passed **3,151 tests with 13 skips** in 401.21 seconds, recorded in `.tmp/hayat-final-regression.log`. All 137 main GUI tests ran successfully. Skips cover opt-in live checks, unavailable Docker/wake fixtures, and two image-generation checks that would unload the running owner's model. One existing test docstring produced an invalid-escape warning. Earlier split verification passed 3,142 tests; a preceding combined run had an intermittent editor-save timeout. That timeout did not recur in the final combined run. Independently reproduced editor error-recovery and storage-cache defects were fixed, and PDF rendering was isolated from GUI worker threads. The specific cause of the earlier save timeout was not established. See file and preview reliability for the changes and focused evidence.

Both opt-in live customer tests also passed (85.39 seconds). Real assistant chat progressed from an inquiry through a proposal, website bundle, checked reuse, explicit-price quote and unpaid invoice, and separately prepared a newsletter with a grounding edit. AI drafting improves grounding but still requires human review. Focused billing, GUI, assistant, help and capability checks passed, including missing-input prompts, quote ownership, current payment-status reads, retries, duplicate prevention and edited-PDF protection. The generic invoice help recommendation was corrected and verified while preserving customer-specific recommendations when their scope is explicit. Ruff and installed-package dependency consistency passed; all 49 applicable project requirement/version checks had already been verified. The live business profile was checked and already contained Hamuzair and Mohsin Khan Shaik. Direct desktop visual inspection was unavailable because the computer-use native connection could not open; native GUI integration checks still ran successfully.

The complete fictional workflow example is in `data/artifacts/customer-demo/20261004-complete-70d8dd3c`: a proposal, website ZIP, 105 USD quote PDF and unpaid invoice PDF. Verification metadata records matching file hashes and ZIP contents, independently extracted PDF customer/amount checks, and no outward messages, publication or income. Earlier content examples remain in `data/artifacts/customer-demo/20261004-followups-a2f3256e` and `data/artifacts/customer-demo/20261004-5341323098`. They are examples for review, not customer engagements or sales.
