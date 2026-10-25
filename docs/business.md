# Earning money with HAYAT: the business toolkit

HAYAT can run the paperwork of a one-person business. It keeps your clients, tracks your hours, writes quotes and invoices as PDFs, records what you were paid and what you spent, tracks work leads, and tells you each month what you earned and what to do next. It works together with the [digital-product tools](digital-products.md), and product sales appear in the same profit report.

Everything is local and free. Nothing new needs to be installed: the toolkit uses ReportLab for PDFs and openpyxl for the Excel export, and both are already in `requirements.txt`.

**What HAYAT does not do.** It never collects money, never logs into a payment or bank account, and never sends an invoice by itself. Sending an invoice by email goes through the existing email tool and its approval rules. Payments and expenses are what you tell HAYAT. The reports say so, and HAYAT has not verified them. None of this guarantees income: it saves you time and keeps you from forgetting to bill, chase, or follow up.

## Set it up once

1. Tell HAYAT your business details. These are printed on every invoice.
   - "Set my business profile: business name Mohsin Studio, email me@example.com, default currency USD, payment instructions: pay by bank transfer to ..."
   - Put only what a client needs to pay you. Never put passwords or card numbers in the profile.
2. Set a monthly income goal: "Set my income goal to 2000 dollars."
3. To email invoices from HAYAT, open the Business window and press **Email setup**. Give your email address and an app password. For Gmail, make the app password under Google Account, Security, App passwords, which needs 2-step verification. Gmail, Outlook, Yahoo, iCloud and Zoho servers are filled in for you; for another provider, give its mail server. The password is kept in the Windows credential store, never in a file. HAYAT then logs in once to check it works, without sending anything. Advanced setups can still use the `email` section of `config/settings.yaml`, which takes priority.

## Everyday use

| You want to | Say something like |
| --- | --- |
| Add a client | "Add client Acme Ltd, email ap@acme.com, hourly rate 40" |
| Track time | "Log 2.5 hours for Acme on the landing page" |
| Time as you work | "Start a work timer for Acme: homepage design", then "Stop the work timer" |
| Quote a job | "Create a quote for Acme: logo design 150, two revisions 40 each, 5% tax" |
| Turn the quote into an invoice | "Create an invoice for Acme from quote Q-2026-0001" |
| Bill your hours | "Create an invoice for Acme with my unbilled time, due in 14 days" |
| Send it | "Email invoice INV-2026-0001 to Acme", or select it and press **Email invoice** in the Business window (the email tool's approval rules apply) |
| Record payment | "Mark invoice INV-2026-0001 as paid, reference bank-1234" |
| See who owes you | "Show my unpaid invoices" or "Show my overdue invoices" |
| Record a cost | "Record an expense of 12.50 for software: domain renewal" |
| See the month | "Show my business report" or "How much did I earn last month?" |
| Prepare taxes | "Export my business records for taxes for 2026" |

Invoices are numbered INV-YYYY-NNNN and quotes Q-YYYY-NNNN, with no gaps. A mistaken invoice is voided, never deleted, and any hours on it become unbilled again. A payment can be partial, and a repeated payment reference is not counted twice.

## Compare a paid pilot with the plan

Open **Business model → Check paid pilot**. Enter totals for one cohort: qualified
leads, paying customers, received revenue after refunds, direct costs, acquisition
spend and delivery hours. The result compares observed conversion and contribution
with the current plan, including the planned value of owner time. Saving retains
the exact plan snapshot and timestamp; it does not add income or expenses to the
accounting books. No paying customers remains a lack of paid evidence, and a small
cohort does not establish future conversion or market size. The assistant can use
the same `business_pilot` and `business_pilot_save` tools. Verification and limits.

## Start a business or service

Not sure what to sell yet? HAYAT can take you from an idea to your first invoice. Open the Business window's **Launch** tab, or just ask.

1. **Find an idea that fits.** "Give me business ideas for someone who knows Arabic and design." HAYAT matches 16 low-cost businesses one person can start (writing, design, social media, websites, tutoring, translation, virtual assistance, video editing, bookkeeping, IT help, CVs, data work, digital products, photography, handmade goods, coaching) to your skills, hours and budget. Each comes with what you need to start, where first clients are, a first offer, and how to check demand. It promises no income and recites no prices: check real rates with research.
2. **Start it.** "Start a business called Mohsin Designs offering logos and brand kits for small businesses." You get a 12-step launch plan. HAYAT ticks steps off by itself when your books show them done: a service priced, your business details filled in, ten leads found, three contacted, the first invoice sent, withdrawals set up. You tick the rest, such as checking demand and local rules.
3. **Price it.** "Calculate my hourly rate for 2000 dollars a month at 20 hours a week." The price calculator works back from what you want to keep, after tax, costs and four weeks off a year. Then add your services with their prices and what is included.
4. **Show it.** "Make a website for my business" writes a one-page website with your services, prices and contact email. "Make my marketing kit" writes your pitch, a profile or gig description, an outreach email, a follow-up, social posts and a price list, ready to edit.
5. **Sell it.** Track leads, review the outreach, then "quote from service" makes a quote PDF that becomes an invoice when they say yes. Payments happen through a separately configured provider; the wallet records actual receipts and withdrawals.

HAYAT never registers, publishes or sends anything by itself. To put your website online, connect a free Netlify account once (Business, Launch, **Publishing setup**, with a personal access token kept in the Windows credential store). Then say "Publish my business website" or press **Publish website**. You see the files and the address, confirm, and HAYAT publishes and fetches the live page to check it matches. Registering on a website or a government portal works the same way through the form tools: HAYAT fills the form, shows you every value and a screenshot, you type any password and confirm, and then it submits and shows you the result. See [Owner-confirmed outward actions](security.md#owner-confirmed-outward-actions). Whether you must register a business or pay tax depends on where you live: HAYAT can research it, and the decision is yours.

| You want to | Say something like |
| --- | --- |
| Get ideas | "Help me make money", "What type of business can you make?", "What can I sell?" or "Ways to make money with Excel" |
| Start a business | "Start a business called Lina Translations offering Arabic translation for families abroad" |
| See the plan | "Show my launch plan" |
| Tick a step | "Mark the research step done" |
| Add a service | "Add a service called Logo design for 150 with 3 concepts and 2 revisions, delivered in 5 days" |
| Quote it | "Quote my logo design service for Acme" |

## Several income streams, from real records

Open the Business window's **Income streams** tab, or ask "Show my income streams" or "Where does my money come from?". Every source you earned from is listed: each client, each digital product, and each other source such as a Fiverr or Upwork payout. Each shows its amount, its share, and its change since last month. If most of your income comes from one source, HAYAT says so. Everything comes from what you recorded; nothing is estimated or predicted.

## Today's brief

Once a day, when something needs your attention, HAYAT posts a short brief in the chat. It covers income this month and by stream, progress toward your goal, overdue invoices, unbilled hours, lead follow-ups, the next launch step, and actions waiting for your confirmation. It only reads your books; it never sends, publishes or changes anything. Ask "Show my business brief" or press **Today's brief** any time. Turn the automatic brief off with `business.daily_brief: false` in `config/settings.yaml`.

## Deep review of your books

"Do a deep review of my business", "when will I get paid", "who pays late", "what is my real hourly rate", "am I on
track for my goal", "how much is my pipeline worth", "show earning opportunities", or **Business → Deep review**
(two charts, then the review). It reads only your own records, plus pay in the job postings HAYAT collected:

- **Money expected, by when.** Unpaid invoices fall into the next 30, 31–60 or 61–90 days, "overdue, still
  expected", or "at risk" (over 60 days overdue, not counted on). Each client's own record moves the date: a
  client who paid their last invoices 12 days late is expected 12 days after the due date. This is set against
  your usual monthly spending (the average of the three months before this one).
- **Paying habits:** per client, the days from issue to payment and past the due date ("pays on time", "usually
  pays about 12 days late"), and what is overdue now.
- **Your real hourly rate:** what was actually paid for invoices built from logged time, divided by those hours,
  per client and overall, next to the agreed rate. A client paying an effective $30 an hour against an agreed
  $40 has unpaid or discounted time. Unbilled hours are listed with the age of the oldest.
- **Dependence:** each client's share of the last twelve months' income. Over half from one client is a risk,
  and the review says so.
- **Pipeline:** open leads valued at your own win rate, once at least five leads have closed. When one source
  (Upwork, referrals) has five closed leads, it uses that source's rate. With fewer, leads are not valued, and
  the review asks you to mark them won or lost as they close. Leads untouched for two weeks are listed.
- **Goal pacing:**
  - what is still needed this month, and per remaining day;
  - how much of it is already expected in by month end;
  - the rest in hours at your real rate, or in invoices of your usual size.
- **Spending:** categories that jumped this month against the three months before, and six months of money in
  and out.
- **Pricing against the market:** the hourly pay in matching job postings (yearly pay ÷ 2,080 hours), next to
  your real rate. Employers pay employees; freelancers usually charge more to cover gaps, tools and tax, so
  treat it as a floor, not a price.
- **Earning opportunities:**
  - contract, freelance and part-time postings that fit your CV (from the Jobs window);
  - the skills employers ask for that you have, to sell as a service.
- **Do first, by the money at stake:**
  - chase overdue invoices, with the client's habit;
  - invoice old unbilled hours;
  - follow up stale leads;
  - check clients paying under the agreed rate;
  - reduce dependence on one client;
  - look at spending that jumped.

**Today's brief** now starts with the most money at stake. Payments are as you recorded them, not verified, and
nothing here is a promise of income. The review sends and changes nothing.

## Facts, not guesses

Questions about money in the wider world change by place and year: "How much can a translator earn?", "What do logo designers charge?", "Is there demand for dog walking here?". HAYAT sends these to its research tools, which answer with sources and say how far to trust them, instead of answering from memory. It never promises income. The ideas and the price calculator contain no market figures: the calculator works only from your own numbers.

## Finding paid work

HAYAT can research where work like yours is offered and keep the opportunities in one list.

1. "Research freelance gigs for logo design that pay at least 100 dollars."
2. Read the sources it cites and decide which are real.
3. "Add a lead: Shopify theme fix on Upwork, link https://..., worth 500, follow up Friday."
4. "Show follow-ups due" each morning, then "Update lead 3 to proposal" or "won" or "lost".

Applying, messaging clients, or posting on a marketplace happens in your own accounts. HAYAT can open the page in its browser, but logging in and submitting stay with you.

## Finding clients: real prospects, never invented ones

Say what kind of business you want as clients and where:

- "Find solar installers in Austin, Texas and make a lead list"
- "Find 20 dentists in Detroit"
- "Make a lead list of roofing contractors in Dallas"

`find_prospects` searches the public web, skips directories, review sites, "top 10" articles and quote brokers, and keeps one result per company website. It then opens each company's own homepage (honouring robots.txt) and records only the email address and phone number that business prints there. The list is saved as a CSV in `data/artifacts/business/leads/` and each new company is added to your leads, marked "not contacted". Running it again does not add the same company twice. A search for a whole state mostly returns directories, so a city gives better results.

What it never does: invent a company, a person's name or an email address, or call anything "verified". A published address is labelled published, because nobody has checked that it is read. It contacts no one. A first email is a separate step that you read and confirm, and only once email is set up in Business, Email setup.

"Create lead and make a list" on its own names no target, so HAYAT asks which businesses and where instead of guessing.

Cold email has rules. In the US (CAN-SPAM), a commercial email needs an honest subject line, your name and a postal address, and a working way to opt out. Never claim things you can't back up, such as homeowners "with budget confirmed".

## Your earnings wallet

**Accounts, cards and money you add (2026-10-13).** In **Business → Wallet**:
- **Money methods:** your bank accounts, cards and payment handles.
  - Each is kept by a nickname, the bank or brand, and at most the last 4 digits.
  - Before anything is saved, these are refused: a full card number (13–19 digits that pass the card
    checksum), any run of 6 or more digits, an expiry date like 12/27, and the words CVV, PIN, password and OTP.
  - In chat: "add my HDFC savings account ending 1234", "add my visa card ending 4242", "show my money methods",
    "remove payment method 2".
- **Add money:** "add 5000 to my wallet from HDFC savings", or the **Add money** button. This records money you
  moved into the business yourself, in your bank or payment app.
  - The wallet shows it as *money you added*, never as income, so profit, the monthly report and the deep
    review stay true.
  - A bank reference stops it being recorded twice.
- **Getting paid, for real:** "my UPI ID is name@bank", "my PayPal.me is yourname", "my Stripe payment link is
  https://…". After that, every invoice PDF has a **How to pay** block:
  - a UPI QR code with the amount and invoice number already filled in (for rupee invoices);
  - a PayPal.me link with the amount;
  - your payment link.

  Whoever pays scans or clicks, and their own app sends the money straight to your account. **Pay-now QR** on an
  invoice, or "create a payment QR for INV-2026-0003", shows the same options with Copy and Open buttons, and
  saves the QR as a picture to send. "Generate a UPI QR for 1500 rupees" does it for any amount. When the money
  arrives, record the payment.

**Decisions, pots, bills and rules (2026-10-13).** HAYAT proposes what to do with the wallet's money and you approve each decision; see [the HAYAT Wallet](hayat-wallet.md).

**Why HAYAT doesn't load money from a card itself:**
- Charging cards needs a licensed payment processor and card-data compliance (PCI DSS).
- Holding money for others needs a money-transmitter licence.
- Card numbers kept on this computer would be one injected web page away from misuse.
- HAYAT's own rule, since 2026-10-03, is that it never moves money.

So money moves only in your bank or payment app, and HAYAT keeps the records.

**Rupees and more.** The books, products and customer quotes now take INR, AED, SAR, PKR, BDT, SGD, MYR, CHF, ZAR,
HKD, SEK, NOK, DKK, PLN, BRL, MXN and QAR, alongside USD, EUR, GBP, CAD, AUD and NZD. Before, rupee invoices
were refused.

Everything you earn is kept in HAYAT's wallet: invoice payments, digital-product sales after fees and refunds, and any other income you record, such as a marketplace payout or a tip. Expenses come off it. What remains is **available to withdraw**, shown per currency.

HAYAT keeps the record, not the money. The money itself sits in the bank, PayPal or store account your clients paid into. The wallet tells you how much of it is yours to take out, and keeps a history of every withdrawal.

You can set up withdrawals whenever you like. Until then your earnings keep adding up, and the business report reminds you that a method is not set yet.

| You want to | Say something like |
| --- | --- |
| See the wallet | "Show my wallet" or "How much can I withdraw?" |
| Add income from elsewhere | "Record income of 80 from Fiverr for a banner gig" |
| Set up withdrawals later | "Set up withdrawal to PayPal", or use **Set up withdrawal** in the Wallet tab |
| Take money out | "Withdraw 100", or "Withdraw everything" |
| Confirm the transfer | "Withdrawal 1 is done" |
| Change your mind | "Cancel withdrawal 1" |

**Setting up a withdrawal method.** Choose bank, PayPal, Stripe, Wise, Payoneer, UPI or other. Give the account a name you recognise, such as "My savings" or your PayPal email. You can also add the last 4 digits of the account. HAYAT refuses anything that looks like a full account or card number, and any password, PIN or security code. Those belong in your bank or PayPal, never in HAYAT.

**Withdrawing.** A withdrawal can't be more than what is available. It is recorded as pending, and the amount stops counting as available straight away. Then make the transfer yourself in that account and tell HAYAT it is done. A pending withdrawal can be cancelled, which makes the amount available again. HAYAT never moves money.

## The Business window

Open **Studios ▾ → Business**, the **Business** quick action, or search "business" in the command palette. The window is a dashboard over your own books:

- **Five cards**: total revenue, profit before tax, owed to you, wallet balance and the monthly target with a progress bar. Revenue and profit show the change against the previous period (for example "↑ 24% vs last month"); with nothing earlier to compare, the card says so instead of inventing a figure.
- **Invoices**: counts for the period (invoices, paid, pending, overdue, total invoiced), a search box and a status filter, and a list where overdue invoices are red, pending ones gold and paid ones dimmed. Double-click opens the PDF; right-click offers Open PDF, Email invoice, Mark paid and a payment-reminder draft.
- **Tabs** for clients, leads, expenses, the **Wallet** (every amount in and out, withdrawal method and withdrawals), income streams, Launch and Products.
- **Quick actions**: create an invoice (the request is put in the chat box for you to finish), mark as paid, draft a reminder, add a client, export the year for taxes, open the documents folder, add an expense and today's brief.
- **Sidebar** (hidden in narrow windows; it scrolls): revenue, profit margin, expenses and client count; a six-month revenue-versus-expenses chart in your main currency; upcoming and overdue invoices and lead follow-ups; recent wallet transactions; and suggestions from your books.

Accounting figures reflect what you recorded. Other currencies are never added together, and HAYAT does not verify payments with your bank.

The **Business model** tab separately evaluates your planning assumptions. It
shows downside/base/upside scenarios, 11 one-factor stress tests, and the price,
acquisition-cost allowance and conversion needed for your profit target. Delivery
capacity and the value of your time are included. These are scenario calculations,
not demand predictions or recorded revenue; changing price does not simulate
customer reactions. The form stacks in a narrow pane, tables scroll horizontally,
and the comparison, form and stress tests share one vertical scroll area.

Deep acquisition research now combines configured search indexes and merges
tracking-link duplicates before fetching listings. Seller claims still require
verification; finding a listing does not establish availability or fair value.

## The monthly report

The report counts income when it is received: client payments, digital-product sales net of fees and refunds, and other income you recorded. It subtracts recorded expenses to give profit before tax. Each currency is reported separately and never converted. It also lists overdue invoices, unbilled hours, lead follow-ups due, and whether you are on pace for your goal.

## Where things are kept

| What | Where |
| --- | --- |
| The books | `business.db` beside the memory database in `data/memory/` |
| Invoice PDFs | `data/artifacts/business/invoices/` |
| Quote PDFs | `data/artifacts/business/quotes/` |
| Tax exports | `data/artifacts/business/exports/` |
| Websites | `data/artifacts/business/site/<business>/index.html` |
| Marketing kits | `data/artifacts/business/marketing/` |
| Email account (address and server, no password) | `data/memory/email-account.json` |

Supported currencies: USD, INR, EUR, GBP, AED, SAR, PKR, BDT, SGD, MYR, CHF, ZAR, HKD, SEK, NOK, DKK, PLN, BRL, MXN, QAR, CAD, AUD and NZD, all with two decimals. The tax export is a starting point for you or your accountant. Check every figure against your bank and store statements before filing.

## Tools

`business_profile_set`, `business_profile`, `client_add`, `client_list`, `time_log`, `time_timer_start`, `time_timer_stop`, `time_unbilled`, `quote_create`, `invoice_create`, `invoice_get`, `invoice_list`, `invoice_record_payment`, `invoice_void`, `expense_record`, `expense_list`, `lead_add`, `lead_update`, `lead_list`, `income_goal_set`, `business_report`, `business_export`, `wallet_balance`, `wallet_record_income`, `wallet_payout_setup`, `wallet_payout_clear`, `wallet_withdraw`, `wallet_withdrawal_done`, `wallet_withdrawal_cancel`, `business_ideas`, `venture_start`, `venture_status`, `venture_step_done`, `service_add`, `service_list`, `quote_from_service`, `rate_calculator`, `landing_page_create`, `marketing_kit_create`, `publish_website_prepare`, `form_fill_prepare`, `pending_actions`, `income_streams`, `business_brief`, `find_prospects`.

`find_prospects` uses the `web_search` permission (already allowed; privacy mode switches it off) and writes only to the books and `data/artifacts/business/leads/`.

All are low risk. Tools that write use the `filesystem_write_project` permission, and tools that only read use `filesystem_read`. Both are already allowed in `config/permissions.yaml`, so no permission was changed. Files can only be written inside HAYAT's allowed folders.
