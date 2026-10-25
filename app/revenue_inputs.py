"""Shared input guidance for model schemas and the revenue workspace."""
from app.revenue_catalog import BY_NAME


GUIDES = {
    'income_execution_plan': 'data.skills, data.buyer, data.offer and data.deliverable are required. Optional weekly_hours (default 5), price, direct_costs, delivery_hours, hourly_cost, currency, context and attachments. Saves a plan ZIP and persistent actions; estimates never become income.',
    'open_source_integration': 'goal describes the actual integration. data.repository is OWNER/REPO from open_source_search; supply target_stack, acceptance_tests and constraints. Inspects the repository then creates a reviewable local prototype bundle.',
    'income_idea_engine': 'data: skills (text or list of text), limit (1–18, default 10). Without skills returns general offer patterns; no market claims.',
    'income_portfolio_plan': 'data: actual skills (text or list). Returns three candidate offers and a staged validation/delivery/retainer/product plan.',
    'offer_unit_economics': 'data: price, direct_costs, delivery_hours, hourly_cost; optional fee_percent (0–100), acquisition_cost, fixed_costs, currency. Contribution includes valued delivery time, excludes tax.',
    'revenue_goal_planner': 'data: monthly_target (contribution after variable costs), contribution_per_sale (>0), hours_per_sale, monthly_available_hours; optional monthly_fixed_costs, currency. Target math only.',
    'recurring_revenue_planner': 'data: monthly_target, contribution_per_customer (>0), hours_per_customer, monthly_available_hours; optional active_customers, monthly_churn_percent (0–100), monthly_fixed_costs, currency.',
    'sales_funnel_diagnostics': 'data: leads, replies, qualified, proposals, wins (whole counts from one cohort, monotonically decreasing); optional cohort label.',
    'demand_validation_plan': 'data: offer, buyer; optional interviews (1–1000, default 5), minimum_commitments (default 1). goal can supply offer. Criteria are assumptions, not evidence.',
    'income_action_queue': 'action create/record/update/list/report; key: stable action ID. data: title, tool (registered Hayat tool), priority (1–5, 1 most urgent), status (planned/in_progress/blocked/done/cancelled); evidence required for done.',
    'osiris_status': 'Alias for the independent global intelligence dashboard in a full Hayat runtime. No Osiris service is required. Standalone income workspaces show their local business operations.',
    'osiris_playbook': 'Alias for a source-linked global intelligence brief in a full runtime. Standalone income workspaces show next income actions. No forecast probabilities are invented.',
    'score_job_opportunity': 'data: pay, difficulty, competition, fit (observed 0–100 signals); optional wins and closed historical counts. Or opportunities: [{title, ...signals}].',
    'lead_score': 'data: need, fit, budget, engagement (observed 0–100); optional prospects: [{name, ...signals}].',
    'churn_risk': 'data: disengagement, complaints, payment_delay (observed 0–100).',
    'viral_topic_score': 'data: hook, audience_relevance, originality, timeliness (observed 0–100). No viral probability.',
    'feature_priority': 'data: reach, impact, confidence (0–100), effort (>0); optional features: [{name, ...signals}].',
    'deal_probability': 'data: wins and closed (actual whole historical outcome counts).',
    'pricing_engine': 'data: hours, hourly_cost, direct_costs (optional 0), margin_percent, fee_percent (optional 0), currency; optional competitor_prices from observed comparable offers.',
    'content_roi': 'data: attributable_revenue, production_cost, distribution_cost (optional 0), currency.',
    'automation_roi_calculator': 'data: hours_before, hours_after, runs_per_month, hourly_cost, implementation_cost, monthly_running_cost, currency.',
    'price_comparison_engine': 'data: sale_price, acquisition_cost, fee_percent, return_percent, shipping_cost; optional other_costs, return_cost, quantity, currency.',
    'inventory_profitability': 'data: sale_price, acquisition_cost, fee_percent, return_percent, shipping_cost, quantity; optional other_costs, return_cost, currency.',
    'subscription_metrics': 'data: subscriptions: [{monthly_price, customers}], starting_customers, lost_customers, acquisition_spend, new_customers, gross_margin_percent, currency.',
    'saas_pricing_optimizer': 'data: tiers: [{monthly_price, customers}], starting_customers, lost_customers, acquisition_spend, new_customers, gross_margin_percent, currency.',
    'contract_builder': 'data: approved_template (text with {{field}} placeholders), fields: {field: value}. No invented legal clauses.',
    'quote_builder': 'data: client (existing client), items: [{description, quantity, unit_price}]; optional valid_days, tax_percent, currency, notes.',
    'invoice_automation': 'action create/list/report; data: client, items: [{description, quantity, unit_price}], due_days, tax_percent, currency, notes, include_unbilled_time, from_quote; or number/status for inspection.',
    'payment_reminder': 'data: number (actual unpaid invoice number). goal describes the desired reminder tone.',
    'follow_up_lead': 'goal and context: actual prior conversation and offer; optional data.lead_id links an open tracked lead.',
    'client_profitability': 'data: allocated_costs: {client_name: actual_cost_amount}. Unknown allocations remain unknown; cash receipts and hours come from the ledger.',
    'profit_tracker': 'data: period (this month, last month, YYYY-MM, YYYY, all); optional allocated_costs by client.',
    'revenue_dashboard': 'data: period (this month, last month, YYYY-MM, YYYY, all); optional allocated_costs by client.',
    'next_sales_action': 'data: lead_id (existing tracked lead).',
    'find_freelance_jobs': 'goal: skills/niche/location. data: platforms (upwork, contra, linkedin, fiverr, job_boards), limit (1–50). Public candidates require job-page verification.',
    'outreach_campaign': 'action create/list/approve/pause/cancel/send_next. data: messages: [{to, subject, body, due_at: timezone-aware ISO timestamp}] on create; campaign_id otherwise. Review exact content before sending.',
    'posting_scheduler': 'action create/list/due/record_published. key: stable post ID. data: content, platform, due_at (timezone-aware ISO); receipt_url when publication is reported. Local calendar only.',
    'service_marketplace_manager': 'action create/list/record_published. key: stable listing ID. data: content, platform, due_at; receipt_url for owner-reported publication.',
    'proposal_ab_test': 'action record/report. key: variant/cohort ID. data: variant, sent, responses, wins. Store cumulative snapshot counts; repeated keys replace snapshots.',
    'conversion_optimizer': 'action record/report. key: variant/cohort ID. data: variant, exposures, conversions. Different audiences or small samples do not establish a causal winner.',
    'revenue_experiment_manager': 'action create/record/update/report. key: experiment ID. data: hypothesis, revenue, costs, hours, currency, outcome evidence.',
    'content_performance_tracker': 'action record/report. key: content ID. data: views, impressions, clicks, watch_seconds (average per view), duration_seconds, attributable_revenue, source_url; unknown metrics stay unknown.',
    'affiliate_conversion_tracker': 'action record/report. key: campaign ID. data: clicks, conversions, commissions, refunds, source_url; unknowns stay unknown.',
    'data_cleanup_service': 'data: path (CSV ≤10 MB), drop_duplicates (optional boolean). Trims fields, preserves the original and writes a report.',
    'clip_generator': 'data: path or url; optional quality, audience, max_clips, clip_seconds, aspect, captions. Delegates to the existing make_video_clips engine.',
    'data_product_builder': 'data: license, source_urls or source_path, and actual records/content or path to read; goal describes the product. No resale rights are invented.',
}


def input_guide(name):
    if name in GUIDES:
        return GUIDES[name]
    mode = BY_NAME[name].mode
    return {
        'draft': 'goal: actual brief/job description. context: actual profile, previous work/messages. data: documented facts, measurements and source URLs. Returns a saved local Markdown draft and ZIP.',
        'build': 'goal: concrete build brief. context/data: actual requirements, facts, records and API documentation; optional data.path to read a source file. Returns actual local files and a ZIP; no execution/deployment.',
        'search': 'goal: actual niche, query or location. data.limit: optional result limit. Returns public search candidates with sources and gaps.',
        'audit': 'data: url or urls (1–5 explicit public URLs). goal: scope of the audit/research. Returns observed evidence and, with a model, a saved cited report.',
        'monitor': 'action snapshot/report; key: stable monitor ID; data: url or urls (1–5 public URLs). Captures on request; no background schedule installed.',
        'pipeline': 'action create/update/list/report; data: title, source, url, value, currency, follow_up, notes for create; lead_id, status (new/contacted/proposal/won/lost) for update.',
        'classify': 'goal: the actual reply text. Ambiguous replies remain uncertain; no message is sent.',
    }.get(mode, 'Supply actual observations in data; read the tool description before running.')
