"""Named revenue workflows, with explicit local, research and draft semantics."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class RevenueTool:
    name: str
    group: str
    mode: str
    task: str


# These are operations, not promises of income or installed third-party accounts.
_ROWS = '''
income_execution_plan|Income|plan|Save a practical earning plan, delivery checklist, budget assumptions and tracked next actions from your skills, buyer and offer. No model needed.
open_source_integration|Development|build|Inspect an actual public repository and build a local integration prototype with pinned version, license notes, setup, tests and rollback instructions. Do not execute downloaded code.
find_freelance_jobs|Freelance|search|Find public freelance opportunities for the supplied skills and preferences.
score_job_opportunity|Freelance|score|Rank supplied opportunities by pay, effort, competition and evidenced skill fit; close probability needs outcome data.
generate_proposal|Freelance|draft|Write a tailored freelance proposal from the actual job description and supplied profile; do not invent experience.
proposal_ab_test|Freelance|experiment|Record proposal variants and aggregate actual sent, response and win counts.
follow_up_lead|Sales|draft|Prepare an unanswered prospect follow-up using the previous message and tracked lead; do not send.
lead_finder|Sales|search|Find public business candidates for the supplied service and location.
lead_enrichment|Sales|audit|Research a supplied company website; distinguish sourced company facts from unknown size, contacts and stack.
lead_score|Sales|score|Score supplied prospects using evidenced need, fit, budget and engagement signals.
personalized_outreach|Sales|draft|Draft a prospect-specific message using sourced observations and the actual offer; do not invent familiarity.
outreach_campaign|Sales|campaign|Manage a persistent email sequence, review exact messages, approve once and send one due message explicitly.
reply_classifier|Sales|classify|Classify a supplied reply as interested, not interested, later, referral, spam or uncertain.
sales_pipeline|Sales|pipeline|Read or update existing lead stages and follow-up dates in the business ledger.
deal_probability|Sales|probability|Estimate an empirical close rate from supplied observed outcomes, with uncertainty; no invented per-deal percentage.
next_sales_action|Sales|next|Recommend the next action from the actual lead stage and follow-up date.
service_generator|Services|draft|Create concrete service packages from supplied skills with deliverables, scope, exclusions and validation steps.
pricing_engine|Finance|calculate|Calculate an explicit cost and margin based price from hours, rate, costs and fee assumptions.
quote_builder|Finance|quote|Create a numbered quote PDF using the existing client ledger and supplied line items.
proposal_builder|Sales|draft|Create a client proposal with objectives, sourced scope, milestones, dependencies, acceptance criteria and supplied price.
contract_builder|Finance|contract|Fill a user-supplied approved contract template; missing fields remain unresolved and no contract is executed.
invoice_automation|Finance|invoice|Create, list or inspect numbered invoices using the existing ledger; no money is collected.
payment_reminder|Finance|draft|Prepare a reminder from an actual unpaid invoice balance and due date; do not send.
profit_tracker|Finance|dashboard|Read actual recorded income, expenses, client hours and profit by currency.
client_profitability|Finance|profitability|Calculate received revenue and effective hourly receipts per client, with explicitly allocated costs.
upsell_detector|Sales|draft|Identify evidence-based expansion opportunities from supplied completed work and client needs.
cross_sell_engine|Sales|draft|Suggest complementary services from the existing engagement and documented client needs.
retainer_builder|Sales|draft|Draft a recurring service offer with workload limits, service levels, exclusions and supplied monthly pricing.
churn_risk|Sales|score|Score observed disengagement and service issues; an ordinal risk signal is not a calibrated churn prediction.
client_report_generator|Services|draft|Create a client value report from supplied work, measurements and unresolved issues; invent no results.
testimonial_request|Sales|draft|Prepare a testimonial request after supplied successful delivery; do not send or invent a testimonial.
referral_request|Sales|draft|Prepare a referral request after supplied successful delivery; do not send.
portfolio_builder|Content|draft|Turn supplied finished project evidence into a portfolio case study with attributable results.
case_study_generator|Content|draft|Create a before/after case study; quantify only supplied verified measurements and mark missing baselines.
personal_brand_engine|Content|draft|Turn supplied work evidence into LinkedIn or X post drafts without publishing.
content_idea_miner|Content|search|Find public topics and audience questions in a supplied niche with source links.
viral_topic_score|Content|score|Score observed hook, relevance and originality signals; this is a heuristic, not a virality probability.
script_generator|Content|draft|Create a short or long video script with hook, beats, factual claims to verify and call to action.
clip_generator|Content|clip|Create actual local video clips using HAYAT's existing clip engine.
thumbnail_idea_generator|Content|draft|Create title and thumbnail concepts matched to the supplied video without misleading factual claims.
content_repurposer|Content|draft|Repurpose supplied transcript into shorts scripts, posts, a newsletter and carousel copy.
posting_scheduler|Content|calendar|Maintain a local posting calendar with review state and externally reported receipts; no automatic social publishing.
content_performance_tracker|Content|metrics|Record or report measured views, impressions, clicks, watch time and attributable revenue.
content_roi|Content|calculate|Calculate attributable content return from actual receipts and production/distribution costs.
affiliate_opportunity_finder|Content|search|Find public affiliate program candidates in a supplied niche; terms require verification.
affiliate_content_builder|Content|draft|Draft source-based comparison content with affiliate disclosure and explicit unknowns; do not invent product use.
affiliate_conversion_tracker|Content|metrics|Track actual affiliate clicks, conversions, commissions and refunds by campaign.
digital_product_idea|Products|draft|Develop downloadable product ideas for a supplied audience and identify evidence needed to validate demand.
digital_product_builder|Products|build|Build actual local text/code/template files and a downloadable ZIP, with a manifest and hashes.
landing_page_generator|Products|build|Build a local HTML landing page from the actual offer; never fabricate testimonials or operational checkout.
checkout_funnel_builder|Products|build|Build local funnel pages using a supplied real checkout URL or a visibly unconfigured checkout placeholder.
conversion_optimizer|Products|experiment|Compare observed headline, pricing or CTA variants using actual exposures and conversions.
micro_saas_idea_finder|SaaS|search|Find public evidence of repetitive business problems and buyer pain for a supplied audience.
micro_saas_validator|SaaS|audit|Research supplied product/competitor URLs for buyer pain, observed pricing and demand evidence; unknown demand stays unknown.
mvp_generator|SaaS|build|Create a local prototype source bundle with setup instructions and explicit unconfigured integrations; do not deploy.
customer_feedback_analyzer|SaaS|draft|Analyze supplied feedback into attributable themes, severity, requested changes and counterexamples.
feature_priority|SaaS|score|Rank supplied features by observed reach, value, confidence and effort; assumptions remain visible.
subscription_metrics|SaaS|calculate|Calculate MRR, ARR, churn, LTV and CAC from explicit subscription and acquisition inputs with stated assumptions.
saas_pricing_optimizer|SaaS|calculate|Compare supplied tiers using observed customers, receipts and costs; recommend testing rather than inventing demand.
website_audit_seller|Audits|audit|Inspect a supplied public website and prepare a source-backed opportunity report; findings are not proof of willingness to buy.
seo_audit|Audits|audit|Check observed page title, description, headings, links and content from a public fetch; disclose crawl limits.
performance_audit|Audits|audit|Report public fetch timing and transfer size; do not label these Core Web Vitals or a full browser benchmark.
security_audit_basic|Audits|audit|Perform public-page configuration observations only; no exploitation, port scans or unsupported vulnerability claims.
ai_automation_audit|Audits|draft|Map supplied business processes to feasible automations, evidence needs, human review points and operating costs.
automation_roi_calculator|Audits|calculate|Calculate time and cost savings, payback and ROI from explicitly supplied process and implementation costs.
automation_proposal|Audits|draft|Turn supplied audit evidence and calculated savings into a scoped automation proposal without invented ROI.
website_builder_agent|Services|build|Build a local website source bundle from supplied business facts, offer and contact details; do not publish.
chatbot_builder|Services|build|Build a local support/lead bot prototype grounded in supplied company knowledge, with unconfigured credentials left explicit.
workflow_builder|Services|build|Build a local automation script/configuration from supplied API details; credentials remain environment placeholders.
data_cleanup_service|Services|cleanup|Clean a selected CSV by trimming fields and optionally dropping exact duplicates; preserve the original and return a change report.
reporting_service|Services|draft|Create a business report from supplied datasets and measurements, including methodology, gaps and actionable findings.
dashboard_builder|Services|build|Build a local interactive HTML dashboard from supplied data; do not invent metrics.
research_service|Services|audit|Research supplied public sources and create a cited competitor or market research report.
competitor_monitor|Monitoring|monitor|Capture and compare explicit public competitor URLs on request; persist snapshots without an unattended schedule.
pricing_monitor|Monitoring|monitor|Capture and compare public pricing page snapshots; only report observed changes.
review_monitor|Monitoring|monitor|Capture and compare supplied public review pages; coverage depends on accessible content.
reputation_report|Monitoring|draft|Summarize supplied review evidence by theme, date and unresolved complaints; do not invent reviews.
local_business_finder|Local business|search|Find public local business candidates for a supplied niche and location.
google_profile_audit|Local business|draft|Audit supplied Google Business Profile observations for completeness and consistency; no private-account access.
local_seo_opportunity|Local business|audit|Compare supplied local business pages using observed on-page/location signals; ranking positions require actual rank data.
appointment_lead_finder|Local business|search|Find public business prospects for a supplied appointment-based service; do not access private customer lists.
lead_list_builder|Local business|search|Build a source-linked list of public business candidates in a supplied niche.
data_enrichment_service|Services|audit|Enrich supplied public company URLs with sourced observations; contacts and firmographics stay unknown unless evidenced.
resume_service|Career|draft|Tailor a resume to a supplied role using actual experience only; mark missing qualifications.
linkedin_optimization_service|Career|draft|Draft profile improvements using actual experience and target role; do not update the account.
job_application_assistant|Career|pipeline|Track application opportunities and statuses in the local lead ledger; do not submit applications.
interview_prep_product|Career|draft|Create a tailored interview pack from supplied job requirements and actual experience, with practice questions and answer outlines.
template_marketplace_builder|Products|build|Build actual template files, a listing draft and a ZIP bundle; do not publish marketplace listings.
prompt_product_builder|Products|build|Build an industry-specific prompt pack with usage instructions, input fields, examples and evaluation checks.
code_template_builder|Products|build|Build actual source templates and setup instructions; syntax checks do not establish production correctness.
api_wrapper_builder|Products|build|Build a local API wrapper from supplied endpoint documentation with credentials as placeholders.
data_product_builder|Products|build|Package supplied public/licensed data with provenance and license notes; do not invent resale rights.
newsletter_builder|Content|draft|Create a niche newsletter issue from supplied sources with citations, subscription copy and sponsorship placeholders.
sponsorship_finder|Content|search|Find public sponsor candidates relevant to the supplied audience and reach.
sponsorship_outreach|Content|draft|Prepare a sponsor pitch from actual audience metrics and offer; do not send.
marketplace_listing_optimizer|Marketplace|draft|Improve a supplied marketplace listing with accurate title, description and attributes; do not publish or invent condition.
resale_opportunity_finder|Marketplace|search|Find public sourcing and resale listing candidates; observed listing prices are not completed sale prices.
price_comparison_engine|Marketplace|calculate|Calculate sourcing-to-resale contribution after explicit fees, shipping, returns and acquisition costs.
inventory_profitability|Marketplace|calculate|Calculate inventory contribution and capital exposure using explicit quantities and costs.
service_marketplace_manager|Marketplace|calendar|Store service listing drafts and review/publication receipts locally; do not change third-party accounts.
revenue_experiment_manager|Revenue|experiment|Persist small business experiments and compare recorded contribution and time by currency.
revenue_dashboard|Revenue|dashboard|Show recorded income sources, expenses, client time, experiments and next actions; no invented revenue.
income_idea_engine|Income planning|income|Rank 18 practical offer patterns by supplied skills, with buyer, deliverable, validation and executable Hayat tools.
income_portfolio_plan|Income planning|income|Plan a staged service, retainer and reusable product path from actual skills; validate each step before expanding.
offer_unit_economics|Income planning|income|Calculate contribution, margin and break-even sales from explicit price, fees, costs and valued delivery time.
revenue_goal_planner|Income planning|income|Calculate sales and delivery capacity needed for a supplied monthly contribution goal; targets are not earned income.
recurring_revenue_planner|Income planning|income|Calculate required active customers, churn replacement allowance and delivery capacity from supplied assumptions.
sales_funnel_diagnostics|Income planning|income|Find the weakest observed transition in one supplied sales cohort without inventing a cause or forecast.
demand_validation_plan|Income planning|income|Create a bounded buyer validation test with explicit go/stop criteria and evidence to collect.
income_action_queue|Income planning|income|Persist and prioritize local income actions; completing an action requires owner-reported outcome evidence.
osiris_status|Osiris|income|Read the independent Hayat global intelligence dashboard; isolated income workspaces retain their legacy operations summary.
osiris_playbook|Osiris|income|Read a sourced global intelligence brief; isolated income workspaces retain their legacy next-income-action plan.
productized_service_builder|Services|draft|Draft a repeatable fixed-scope service with buyer, deliverables, acceptance checks, supplied price and capacity limits.
licensing_offer_builder|Products|draft|Draft a reusable asset licensing offer from actual ownership and permission evidence; rights and commercial terms need review.
workshop_offer_builder|Services|draft|Draft a practical paid workshop offer with supplied expertise, buyer problem, exercises, scope and explicit price inputs.
maintenance_plan_builder|Services|draft|Draft an ongoing maintenance service with supported systems, access needs, response expectations and supplied pricing.
b2b_partnership_proposal|Sales|draft|Draft a complementary business partnership with supplied referral terms, attribution rules and responsibilities; do not send.
'''
CATALOG = tuple(RevenueTool(*line.split('|', 3)) for line in _ROWS.strip().splitlines())
BY_NAME = {tool.name: tool for tool in CATALOG}


def relevant_tools(text: str, limit: int = 14) -> list[str]:
    """Keep a large catalog from bloating every model tool-selection turn."""
    words = set(re.findall(r'[a-z0-9]+', text.lower()))
    scored = []
    for index, tool in enumerate(CATALOG):
        keys = set(re.findall(r'[a-z0-9]+', tool.name.replace('_', ' ') + ' ' + tool.group.lower()))
        score = len(words & keys)
        if tool.name in text.lower():
            score += 20
        if score:
            scored.append((score, -index, tool.name))
    return [name for _, _, name in sorted(scored, reverse=True)[:limit]]


def revenue_request(text: str) -> bool:
    value = text.strip().lower()
    if any(re.match(rf'^(?:use\s+|run\s+)?{re.escape(name)}\b', value) for name in [*BY_NAME, 'revenue_tool_catalog']):
        return True
    return bool(re.match(r'^(?:please\s+)?(?:find|score|rank|generate|draft|prepare|build|create|compare|'
                         r'track|record|show|calculate|analy[sz]e|audit|manage|optimize)\b', value)
                and re.search(r'\b(?:freelance jobs?|proposals?|outreach|sales pipeline|revenue dashboard|'
                              r'content roi|affiliate|subscription metrics|client profitability|leads?|'
                              r'follow.?ups?|retainer|sponsorship|case study|digital product|micro.?saas|'
                              r'pricing engine|churn|a/b test|revenue experiment|revenue tools|growth tools|'
                              r'income ideas?|income plan|income actions?|income queue|osiris|unit economics|'
                              r'revenue goal|recurring revenue|sales funnel|demand validation)\b', value))
