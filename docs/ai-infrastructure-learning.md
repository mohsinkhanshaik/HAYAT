# AI infrastructure learning path

The prerequisite graph progresses from foundations to operations:

```text
Linux -> networking -> containers -> Kubernetes
                      |              |
                      +-> GPU systems -> CUDA -> NCCL
                                         |
                       distributed training and inference
                                         |
                                  observability and SRE
```

Subtopics include processes, filesystems, permissions, TCP/IP, DNS, routing, namespaces, cgroups, scheduling, storage, GPU architecture/memory/interconnects, DDP/FSDP/DeepSpeed, batching/KV cache/tensor parallelism/vLLM/TensorRT-LLM/Triton, checkpointing, and incident response.

```text
/mode teach
Teach me NCCL using questions first.
Test me on Kubernetes GPU scheduling with no hints.
/learn weaknesses
/mode teach+lab
Give me a GPU OOM diagnosis lab. Do not reveal the solution unless I ask.
```

Lessons inspect prerequisite mastery. Active recall and lab evidence update estimates; scheduled review uses a transparent time/mistake/importance heuristic, not a claim of perfect cognitive modeling.
