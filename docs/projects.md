# Projects and conversation sessions

The project registry gives names to approved roots and records one active project. The shared session remembers verified references such as the active application, window, browser page, file, task, and project so follow-ups like “run its tests” can resolve safely.

```text
/project list
/project status
/project use NAME
/project clear
```

Pronoun resolution is context assistance, not permission. A resolved path still passes sandbox and tool policy. If a target is ambiguous, the agent should ask instead of guessing or clicking random coordinates.

Project memory is durable; transient session references are not treated as eternal facts. Use `/remember TEXT` for an explicit semantic fact and `/recall QUERY` to inspect retrieval.
