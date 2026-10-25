# HAYAT digital products

HAYAT can research an audience, write content, generate images, create PDF/Word documents, and now prepare a local digital-product listing and verified delivery ZIP. The new tools use the existing Python environment and standard library: no paid API, extra service, or package installation is needed.

The October 4 GUI and file upgrade adds in-chat editing, responsive previews, native spreadsheet/slide generation, and a generated starter product bundle.

The [Product workspace](product-workspace.md) now brings draft editing, three native starter presets, persistent deliverable selections, native file editors, and delivery verification into **Business → Products**.

Restart HAYAT to load the tools into an already-running desktop session. Product sales also appear in the monthly profit report of the [business toolkit](business.md), next to client income and expenses.

## A complete workflow

1. Ask HAYAT to research a specific audience and problem. Check the cited evidence before treating it as demand.
2. Ask it to create the deliverables using its existing writing, research, image, PDF, Word, or Python tools. Verify facts, instructions, and usability.
3. Ask it to create a digital product draft with title, audience, description, price, currency, and your chosen license terms. `product_create` returns the product ID. Prices are your inputs, not predictions of demand.
4. Ask it to package that product ID with an explicit list of deliverable paths. `product_package` makes a ZIP containing `deliverables/`, `LICENSE.txt`, `LISTING.md`, and `manifest.json`. It validates file paths, checks archive integrity, and returns file hashes. Inspect each file before uploading: archive integrity does not establish factual accuracy, commercial rights, or product quality.
5. Connect your chosen storefront/account using the existing browser or connector tools. Publication, customer delivery, and payment collection require that account and its setup; a local bundle is not a live store. This upgrade does not publish anything or connect a payment account.
6. After an actual sale, supply its unique store transaction reference, product ID, gross amount, fees, and refunds to `product_record_sale`. Repeating the same reference does not count it twice. Different amounts on the same reference are rejected. The current ledger is append-only and stores refunds known when recording the transaction; later refund adjustments and corrections require your external accounting records.
7. Ask for a product sales report. `product_sales_report` groups gross, fees, refunds, and net receipts by currency. Net receipts exclude production costs and taxes. These are user-reported records, not independently verified payments.

## Prompts to try

- “Create a digital product draft for a weekly planning template for freelancers. Use a draft price of 9.99 USD and these license terms: [your terms].”
- “Show my product catalog.”
- “Package my digital product [ID] using these files: [paths].”
- “Record a sale for my digital product [ID], reference [store order ID], gross 9.99, fees 1.25, refunds 0.”
- “Report my digital product sales revenue.”

The catalog and sales ledger live beside the configured memory database in `products.db`. Delivery ZIPs go to `data/artifacts/products/<product-id>/`. Existing permissions and authority checks apply. Currency support is the same two-decimal list as the business books (USD, INR, EUR, GBP, AED, SAR and more; see the business guide). Files have unique filenames inside a bundle; up to 100 files and 250 MiB are allowed. Secret and executable file extensions are excluded, and configured protected paths remain blocked.

## Checks performed on October 3, 2026

All applicable requirements were installed within the configured version ranges, `pip check` found no broken requirements, and the installed Playwright Chromium launched and read a local test page. HAYAT's project doctor reported ready. The focused tests cover packaging/readback, persistence, duplicate transactions, refunds, currency separation, permissions, and request/planner routing.

These checks confirm the local workflow. They do not establish market demand, sales, or working storefront/payment credentials.
