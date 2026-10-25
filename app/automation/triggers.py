from __future__ import annotations

from datetime import datetime
from typing import Any

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger


def build_trigger(schedule_type: str, schedule: dict[str, Any]):
    if schedule_type == "once":
        return DateTrigger(run_date=datetime.fromisoformat(schedule["run_at"]))
    if schedule_type == "interval":
        allowed = {key: value for key, value in schedule.items() if key in {"weeks", "days", "hours", "minutes", "seconds"}}
        return IntervalTrigger(**allowed)
    if schedule_type == "daily":
        return CronTrigger(hour=int(schedule.get("hour", 9)), minute=int(schedule.get("minute", 0)))
    if schedule_type == "weekly":
        return CronTrigger(day_of_week=schedule.get("day_of_week", "mon"), hour=int(schedule.get("hour", 9)), minute=int(schedule.get("minute", 0)))
    if schedule_type == "cron":
        return CronTrigger.from_crontab(schedule["expression"])
    raise ValueError(f"Unsupported schedule type: {schedule_type}")
