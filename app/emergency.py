"""Owner-controlled, latched execution interlock shared by every tool entry point."""
from __future__ import annotations

import threading


class ExecutionInterlock:
    def __init__(self):
        self._lock = threading.Lock()
        self.halted = False
        self.epoch = 0

    def halt(self):
        with self._lock:
            self.halted = True
            self.epoch += 1

    def resume(self):
        with self._lock:
            self.halted = False

    def ticket(self):
        with self._lock:
            if self.halted:
                raise PermissionError('Emergency stop is active. Resume explicitly in the GUI before running tools.')
            return self.epoch

    def check(self, ticket):
        with self._lock:
            if self.halted or ticket != self.epoch:
                raise PermissionError('Tool cancelled by the emergency stop; submit a new request after resuming.')


def stop_runtime(runtime):
    """Latch synchronously; stop owned activities without waiting on network requests.

    An in-flight native call may finish. Its ticket cannot authorize another call,
    even if the owner resumes while it is unwinding.
    """
    runtime.registry.interlock.halt()
    errors = []
    def attempt(label, operation, *, require_stopped=False):
        try:
            result = operation()
            if require_stopped and result is False:
                errors.append(label + ': still stopping after its deadline')
        except Exception as error:
            errors.append(f'{label}: {type(error).__name__}')
    attempt('agent', runtime.agent.cancel_current)
    attempt('approvals', runtime.registry.approvals.revoke_all)
    session = runtime.session
    for key in ('pending_action', 'pending_offer', 'pending_install', 'pending_learning'):
        setattr(session, key, None)
    lifecycle = getattr(runtime.authority, 'approvals_lifecycle', None)
    if lifecycle and session.active_turn_id:
        attempt('turn approvals', lambda: lifecycle.cancel_for_turn(session.active_turn_id, reason='emergency stop'))
    jobs = getattr(runtime, 'research_jobs', None)
    if jobs:
        for job in jobs.running():
            jobs.cancel(job.job_id)
    learner = getattr(runtime, 'learner', None)
    if learner:
        attempt('learning', lambda: learner.request_stop('owner emergency stop'))
    teams = getattr(runtime, 'teams', None)
    if teams:
        attempt('worker teams', teams.cancel_all)
    invention = getattr(runtime, 'invention', None)
    if invention:
        attempt('invention', invention.close)
    camera = getattr(runtime, 'camera', None)
    if camera:
        attempt('camera', camera.off)
    # These are runtime-owned workers. Never terminate an arbitrary OS process.
    for name in ('colibri_service', 'watch', 'voice', 'screen_reader', 'intelligence', 'scheduler', 'event_triggers', 'job_watcher', 'price_alerts'):
        worker = getattr(runtime, name, None)
        if worker and callable(getattr(worker, 'stop', None)):
            attempt(name, worker.stop, require_stopped=True)
    return {'halted': True, 'errors': errors,
            'detail': 'Tools blocked. Running calls may finish; completed actions are not undone. Resume does not restart background services.'}


def branch_conversation(runtime, message_id, *, edit=False):
    """Copy a validated prefix into a fresh session, preserving the original."""
    turns = getattr(runtime, 'turns', None)
    if runtime.agent.is_running or (turns and turns.current() is not None):
        raise ValueError('Wait for the current task to stop before branching.')
    old = runtime.conversation.session_id
    with runtime.memory.connect() as db:
        selected = db.execute('SELECT role,content FROM conversations WHERE session_id=? AND id=?',
                              (old, message_id)).fetchone()
        if not selected or (edit and selected['role'] != 'user'):
            raise ValueError('Select an existing user message to edit, or a message to continue after.')
        rows = db.execute('SELECT * FROM conversations WHERE session_id=? AND id<=? ORDER BY id',
                          (old, message_id)).fetchall()
    if edit:
        rows = rows[:-1]
    import uuid
    import json
    new = 'session-' + uuid.uuid4().hex[:12]
    # One transaction: a failed copy cannot change the active session.
    from app.memory.database import now_iso
    with runtime.memory.connect() as db:
        for row in rows:
            metadata = {'branched_from_session': old, 'branched_from_message': row['id']}
            db.execute('INSERT INTO conversations(session_id,timestamp,role,content,model,metadata_json) VALUES(?,?,?,?,?,?)',
                       (new, now_iso(), row['role'], row['content'], row['model'], json.dumps(metadata)))
    from app.session import SessionState
    fresh = SessionState(session_id=new)
    # Keep the object shared by all existing runtime components, but discard
    # pending actions, referents and standing instructions from the abandoned tail.
    for key, value in vars(fresh).items():
        if key != '_lock':
            setattr(runtime.session, key, value)
    runtime.conversation.session_id = new
    runtime.conversation._answer_cache.clear()
    return {'session_id': new, 'previous_session': old, 'messages': len(rows),
            'draft': selected['content'] if edit else ''}
