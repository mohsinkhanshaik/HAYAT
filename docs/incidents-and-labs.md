# Incident diagnosis and safe labs

## Incident mode

Incident mode gathers evidence before suggesting a change:

```text
/mode incident
/incident ollama
My local model server is down. Diagnose it without changing anything.
```

Scopes include system, AI runtime, GPU, Ollama, Docker, Kubernetes, and network. Snapshots inspect components, resources, processes/ports, and available command output read-only. A future fix still uses normal planning, permissions, and verification.

## Labs

```text
/labs
/lab create dns_failure
/lab restore LAB_ID
```

Available exercises simulate DNS, container networking, Kubernetes GPU scheduling, disk pressure, service crash, and GPU OOM reasoning. They create project-local files under `data/work/labs`; they do not damage real services. Scoring records completion, hints/errors, recovery, verification, and learner mastery. Restore returns the simulated configuration to its known-good state.
