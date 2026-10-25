# Authoritative operational state

`AgentStateService` answers operational questions from live runtime components. It reports the active provider/model, execution modes, activity, active task and plan step, active project, current app/window, selected tools, memory/database status, public-internet reachability, research, voice, vision, storage, and the last observed action/result.

Self-state questions are classified as `SYSTEM_STATE` and bypass the conversational model. This prevents plausible but false answers such as claiming to use ChatGPT, claiming an invented background project, or claiming an action occurred without evidence. The same data is available through the read-only, permission-checked `get_agent_state` tool and the `/brain` command.

Activity is explicit: idle, conversation, planning, researching, coding, computer control, executing, verifying, waiting for approval, speaking, completed, or failed. When no independent task is active, the assistant says so.

Run the regression benchmark:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_self_state.py
```

The committed before artifact records the reproduced false behavior. The after artifact must pass all state questions and hallucination traps.
