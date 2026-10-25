# Income planning and Osiris

The Revenue workspace has **115 workflows**, including ten deterministic income
and operations tools and five additional offer-writing tools. Ideas, calculations,
the action queue and Osiris work without a model. Drafts use the configured model.

Open **Chat actions → Revenue workspace**, filter by **Income planning** or
**Osiris**, and inspect the input guide. **Income actions** shows priorities,
status and suggested tools. **Osiris playbook** prepares a request in chat.

## Ideas into offers

```text
Find income ideas for Python and spreadsheets
Show my income actions
Show Osiris status
Show Osiris playbook
```

`income_idea_engine` ranks 18 reusable patterns: data cleanup, reporting retainers,
automation pilots, website refreshes, maintenance, video editing, newsletters,
career packs, templates, code licenses, workshops, research, support knowledge,
accessibility review, dashboards, localization, referral partnerships and licensed
data. Each includes a buyer, deliverable, validation step and registered tools.
Skill keyword matching does not establish competence, demand or market prices.

`income_portfolio_plan` stages a service, recurring support and reusable products.
`demand_validation_plan` sets a bounded interview/sample test with go/stop criteria
and evidence to collect. Commitments remain separate from cash.

New draft tools prepare productized services, licensing offers, workshops,
maintenance plans and business partnerships. They use supplied facts and leave
missing prices, rights and terms unresolved. Drafts are saved and bundled locally.

## Contribution and capacity

```text
offer_unit_economics {"data":{"price":200,"direct_costs":20,"delivery_hours":2,"hourly_cost":30,"fee_percent":10,"acquisition_cost":10,"fixed_costs":250,"currency":"USD"}}

revenue_goal_planner {"data":{"monthly_target":1000,"monthly_fixed_costs":80,"contribution_per_sale":90,"hours_per_sale":2,"monthly_available_hours":20,"currency":"USD"}}

recurring_revenue_planner {"data":{"monthly_target":1000,"contribution_per_customer":100,"hours_per_customer":2,"monthly_available_hours":30,"active_customers":8,"monthly_churn_percent":10,"currency":"USD"}}

sales_funnel_diagnostics {"data":{"cohort":"pilot","leads":100,"replies":20,"qualified":10,"proposals":8,"wins":2}}

demand_validation_plan {"data":{"offer":"CSV cleanup pilot","buyer":"Teams with messy exports","interviews":5,"minimum_commitments":1}}
```

These are examples. The first gives $90 contribution per sale after fees, costs,
valued time and acquisition costs; three sales cover fixed costs. The second
requires 12 sales and 24 hours, exceeding the supplied 20-hour capacity. Monthly
targets represent contribution after variable costs. Taxes, acquisition time and
administrative time require separate allowances. Recurring plans use supplied
churn assumptions. Funnel counts must describe one cohort; zero denominators
stay unknown, and low rates do not prove their cause.

Calculations do not create payments, combine currencies or update cash books.
Record real receipts with existing invoice/payment, product-sales or income tools.

## Persistent actions

```text
income_action_queue {"action":"create","key":"csv-pilot","data":{"title":"Prepare the buyer-approved sample","tool":"data_cleanup_service","priority":1}}

income_action_queue {"action":"update","key":"csv-pilot","data":{"status":"done","evidence":"Buyer reviewed the sample and change report."}}
```

Priorities run from 1 (most urgent) to 5. States are `planned`, `in_progress`,
`blocked`, `done` and `cancelled`. Completion requires owner-reported evidence.
Reusing a key updates the action. Suggestions do not execute linked tools.

## Osiris

The owner's supplied link identifies the [Osiris global intelligence platform](https://www.osirisai.live/).
Hayat now implements an independent [global intelligence workspace](global-intelligence.md)
with public feeds, a native map, automatic local alerts and sourced briefs.
In a full runtime, `osiris_status` and `osiris_playbook` delegate to that workspace.
Isolated income workspaces retain the earlier local operations helper.

## Desktop control

Exact requests include `Scroll down 5 clicks`, `Press tab`, `Press ctrl+shift+s`
and `Observe my desktop`. Input plans observe before and after action. Both exact
and model-created desktop input steps have zero automatic retries. Keys and
scroll amounts are validated. Default screenshots have distinct filenames.

`computer_observe` returns a screenshot, foreground metadata, geometry, pixel hash
and a single-use token expiring after 30 seconds. Inspect the screenshot, then
call `computer_click_observed` through the planner/registry with the actual
`observation_id`, `x`, `y` and optional `button`/`clicks`. Changed pixels, geometry,
foreground windows, expired tokens and reused tokens reject the guarded click.
Animated desktops can trigger this conservative guard. Inspection and input
cannot be made an atomic OS action. Existing permission policies still apply.

Planned guarded clicks can use `{{desktop_observation_id}}` as their token. The
executor resolves it only from the latest successful, verified `computer_observe`
result; missing or failed observations cannot supply a token.

Guarded input uses primary-display coordinates and rejects screenshot/input
geometry mismatches. Use existing display tools for other monitors. Readiness
does not prove application control; inspect results or use named application
operations with readback. Unicode uses existing editor/clipboard tools.

Implementation: [income operations](../app/income.py),
[catalog](../app/revenue_catalog.py), [desktop tools](../app/tools/computer.py).
Tests use isolated books and mocked desktop input; they do not establish demand
or claim a live desktop application was controlled.
