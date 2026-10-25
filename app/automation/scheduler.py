from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler

from app.agent.core import Agent
from app.automation.jobs import JobRepository, ScheduledJob
from app.automation.triggers import build_trigger
from app.execution import execution_scope
from app.safety.authority import Principal


class AgentScheduler:
    """Runs scheduled goals through the same permission-controlled Agent instance."""

    def __init__(self, agent: Agent, repository: JobRepository) -> None:
        self.agent = agent
        self.repository = repository
        self.scheduler = BackgroundScheduler()

    def add(self, job: ScheduledJob) -> None:
        build_trigger(job.schedule_type, job.schedule)  # validate before persistence
        self.repository.save(job)
        if job.enabled:
            self._schedule(job)

    def enable(self, job_id: str) -> None:
        self.repository.set_enabled(job_id, True)
        self._schedule(self.repository.get(job_id))

    def disable(self, job_id: str) -> None:
        self.repository.set_enabled(job_id, False)
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

    def remove(self, job_id: str) -> None:
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
        self.repository.delete(job_id)

    def run_now(self, job_id: str):
        return self._run(self.repository.get(job_id))

    def _schedule(self, job: ScheduledJob) -> None:
        self.scheduler.add_job(self._run, build_trigger(job.schedule_type, job.schedule), id=job.id, args=[job],
                               replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=60)

    def _run(self, job: ScheduledJob) -> None:
        execution_id = self.repository.begin_execution(job.id)
        self.repository.update_execution(execution_id, "RUNNING", checkpoint={"goal": job.goal, "resume_policy": "manual_review_required"})
        try:
            # Unattended schedules never inherit LOCAL_OWNER authority. They
            # share the background learner's fixed research/read allowance;
            # anything outward-facing or mutating remains stopped until the
            # owner runs/approves it interactively.
            with execution_scope(principal=Principal.BACKGROUND_LEARNER.value,
                                 explicitly_requested=False, turn_id=f"schedule:{job.id}"):
                outcome = self.agent.run(job.goal)
        except Exception as exc:
            self.repository.update_execution(execution_id, "FAILED", checkpoint={"error": f"{type(exc).__name__}: {exc}"})
            raise
        final_state = "COMPLETED" if outcome.complete else "CANCELLED" if "cancel" in outcome.summary.lower() else "WAITING" if outcome.needs_user_input else "FAILED"
        self.repository.update_execution(execution_id, final_state, task_id=outcome.task_id, checkpoint={"summary": outcome.summary, "verification": outcome.goal_verification, "resume_policy": "manual_review_required"})
        self.repository.record_run(job.id, outcome.task_id, final_state.lower())
        for event in outcome.history:
            result = event.get("result", {})
            if not result.get("success") and result.get("permission") == "ASK":
                self.repository.request_approval(job.id, event["tool"], event.get("arguments", {}), result.get("error", "Approval required"), outcome.task_id)
        return outcome

    def approve_and_run(self, request_id: int):
        """Grant exactly one bound use of the approved tool call, then rerun the job.

        The grant binds to the tool, the exact normalized arguments, the current
        session, and a single invocation. It is revoked afterwards so an approval
        can never outlive the rerun it was issued for.
        """
        request = self.repository.decide_approval(request_id, "approved")
        registry = self.agent.executor.registry
        broker = registry.approvals
        grant = broker.grant(
            request.tool,
            request.arguments,
            uses=1,
            scope="scheduled_approval",
            reason=f"Approved scheduler request {request.id} for job {request.job_id}.",
        )
        try:
            outcome = self.run_now(request.job_id)
        finally:
            if grant.uses_remaining <= 0:
                self.repository.decide_approval(request.id, "consumed")
            grant.uses_remaining = 0
        return outcome

    def start(self) -> None:
        for job in self.repository.list(enabled_only=True):
            self._schedule(job)
        self.scheduler.start()

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
