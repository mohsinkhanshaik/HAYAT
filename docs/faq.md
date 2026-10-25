# Frequently asked questions

**Is this ChatGPT?** No. It is a local Windows agent architecture. Optional provider adapters can call compatible services, but default runtime uses local models.

**Does it use OpenAI or cost money?** Default `ZERO-COST` policy blocks paid/cloud providers. `/cost` verifies this. Electricity, hardware, internet, and disk still have real-world costs.

**Does it work offline?** Local conversation, tools, memory, and cached knowledge can work offline. Current facts/web pages cannot be refreshed and must be marked potentially stale.

**Can it access my camera?** Yes, only explicitly. It starts OFF, Trusted Local Owner mode avoids a redundant prompt for the owner's direct request, privacy mode overrides access, no background recorder exists, and temporary observation frames are deleted.

**Can it control Windows?** Yes, through permissioned application/UI/mouse/keyboard tools with observation and verification. Ambiguous targets should trigger clarification.

**Can it browse the internet?** Yes, using isolated public research browsing and free providers. It does not bypass authentication, paywalls, CAPTCHAs, or access controls.

**Can it remember me or learn?** It can store approved facts, verified task experience, research knowledge, procedures, and mastery. This is not automatic model-weight training.

**Can it use private training data?** Yes, for explicitly owner-consented and verified local examples. They are credential-redacted, separated from the standard corpus, and blocked from cloud export. Private documents learned for recall are not collected automatically.

**Does it train itself?** It can prepare verified standard and private-local datasets. Weight training and promotion do not start automatically and require baseline/evaluation, explicit execution authorization, promotion, and rollback.

**Can it delete files?** Recoverable local-owner deletion is pre-authorized, while protected/system paths and driver/system-tool removal are denied. Keep backups.

**Where is data stored?** Under `D:\AI\data`; see [Data locations](data-locations.md).

**Can I disable voice/camera?** Voice is disabled in default config and supports `/voice off`. Camera starts OFF and supports `/camera off`; either feature may also be disabled in settings.

**What if Ollama stops?** Deterministic tools may still work, but model conversation/reasoning becomes limited. No paid fallback occurs. Restart Ollama and run `/doctor`.

**How do I add another model?** Pull only after checking size/license/resource fit, then benchmark and register capabilities. See [Models](models.md).

**Why is an answer slow?** A model may be cold, too large, using RAM spill, researching pages, or running stronger verification. Check `/performance` and `/system`; FAST mode trades depth for latency.

**What is local/private/deep mode?** LOCAL restricts providers to local endpoints. PRIVATE adds stricter data behavior. DEEP prioritizes stronger reasoning/context/verification. Permissions always remain authoritative.
