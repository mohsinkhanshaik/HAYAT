# Automation

Job definitions persist in the SQLite `scheduled_jobs` table. Supported schedules are:

- one-time ISO date/time;
- daily hour/minute;
- weekly weekday/hour/minute;
- fixed weeks/days/hours/minutes/seconds interval;
- five-field cron expression.

APScheduler supplies triggers while `JobRepository` retains definitions and last outcomes. Every scheduled goal runs through the same agent, project boundary, permissions, verification, memory, and logs as an interactive goal. Unattended execution is bound to the restricted `BACKGROUND_LEARNER` principal rather than inheriting owner authority. Actions outside its public-read/validated-knowledge allowance fail closed and can be reviewed interactively.

Every execution records `QUEUED`, `RUNNING`, `PAUSED`, `WAITING`, `COMPLETED`, `FAILED`, or `CANCELLED` plus a sanitized checkpoint. `/job executions` exposes the evidence. A restart never implies automatic approval or unsafe task resumption.

Scheduling is enabled inside a running HAYAT runtime. `/job add`, `run`, `enable`, `disable`, and `remove` manage persisted definitions. `main.py --scheduler-daemon` runs enabled jobs continuously. The opt-in installer registers a limited, current-user Windows logon task with confirmation; it never requests Administrator privileges.

Event-driven rules complement time schedules. A rule listens to one supported sanitized event, applies exact payload filters, and produces either a visible suggestion or a restricted background goal. Cooldowns and hourly limits prevent event storms; rules created through tools start disabled and creating or enabling one requires owner approval. See [Integrations](integrations.md).

Knowledge refresh can be run explicitly with `/knowledge-refresh [LIMIT]` or placed in a user-created scheduled goal. No recurring research, dependency monitoring, or autonomous web crawl is installed or enabled by default. Scheduled research retains the same budgets and network permissions.

## Missed and overlapping schedules

Schedules coalesce missed invocations, permit one simultaneous invocation per job,
and expire runs over 60 seconds late. Checkpoints and background permissions remain
active. See the quality upgrade.
