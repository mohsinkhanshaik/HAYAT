from __future__ import annotations

import json
import math
import statistics
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.memory.database import MemoryDatabase
from app.memory.embeddings import EmbeddingRegistry
from app.models.router import ModelRouter


DOCUMENTS = [
    ("NCCL", "NCCL provides optimized GPU collective communication such as all-reduce across accelerators."),
    ("CUDA streams", "CUDA streams order asynchronous GPU operations and allow overlap when dependencies permit."),
    ("Docker", "Docker packages applications into isolated containers using namespaces and cgroups."),
    ("Kubernetes GPU", "A Kubernetes device plugin advertises extended GPU resources to the kubelet scheduler."),
    ("FSDP", "Fully Sharded Data Parallel shards parameters gradients and optimizer state across workers."),
    ("DDP", "Distributed Data Parallel replicates model parameters and synchronizes gradients with all-reduce."),
    ("RDMA", "Remote Direct Memory Access moves data between hosts with minimal CPU involvement."),
    ("DNS", "Domain Name System resolves human-readable host names into network addresses."),
    ("KV cache", "An inference KV cache stores attention keys and values to avoid recomputing prior tokens."),
    ("Tensor parallelism", "Tensor parallel inference splits individual tensor operations across multiple accelerators."),
    ("Pipeline parallelism", "Pipeline parallelism assigns sequential model stages to different devices."),
    ("VRAM", "Video memory stores model weights activations and cache data used by the GPU."),
    ("PCIe", "PCI Express connects accelerators with the host and can bottleneck inter-device transfers."),
    ("NVLink", "NVLink provides higher-bandwidth direct GPU interconnects than typical PCI Express paths."),
    ("Prometheus", "Prometheus scrapes time-series metrics and evaluates alerting rules."),
    ("OpenTelemetry", "OpenTelemetry standardizes collection of traces metrics and logs."),
    ("Terraform", "Terraform applies declarative infrastructure state through provider APIs."),
    ("vLLM", "vLLM serves language models efficiently using paged attention and continuous batching."),
    ("Triton", "NVIDIA Triton Inference Server hosts models behind standardized inference endpoints."),
    ("Checkpointing", "Training checkpoints persist model optimizer and progress state for recovery."),
    ("Load balancing", "A load balancer distributes requests across healthy service instances."),
    ("Linux cgroups", "Linux control groups account for and limit process resource consumption."),
    ("Kubernetes scheduling", "The Kubernetes scheduler binds pending pods to suitable nodes using constraints and scores."),
    ("GPU Operator", "The NVIDIA GPU Operator manages drivers device plugins and GPU software components in Kubernetes."),
    ("InfiniBand", "InfiniBand is a low-latency high-throughput fabric commonly used with RDMA in clusters."),
]

PARAPHRASES = {
    "NCCL": "Which component handles communication collectives between graphics processors?",
    "CUDA streams": "How can kernel work and memory copies overlap asynchronously on an accelerator?",
    "Docker": "What bundles a program with dependencies in namespace-isolated runtime units?",
    "Kubernetes GPU": "How does a cluster expose accelerator resources so pods may request them?",
    "FSDP": "Which training method partitions weights gradients and optimizer state among ranks?",
    "DDP": "Which replicated training method combines gradients after backward passes?",
    "RDMA": "What network technique transfers host memory without heavy processor involvement?",
    "DNS": "What service turns a hostname into an IP address?",
    "KV cache": "What avoids recomputing attention state for earlier generated tokens?",
    "Tensor parallelism": "How can one matrix operation be divided over several GPUs?",
    "Pipeline parallelism": "What assigns consecutive neural network stages to separate devices?",
    "VRAM": "Where are accelerator weights activations and inference cache kept?",
    "PCIe": "Which host bus may limit accelerator transfer bandwidth?",
    "NVLink": "What direct interconnect offers GPUs more bandwidth than a common host bus?",
    "Prometheus": "Which system scrapes metrics and runs alert rules?",
    "OpenTelemetry": "What vendor-neutral standard collects telemetry signals?",
    "Terraform": "What tool reconciles declarative infrastructure via providers?",
    "vLLM": "Which serving engine uses paged attention and continuous request batching?",
    "Triton": "Which NVIDIA server exposes standardized endpoints for hosted models?",
    "Checkpointing": "What lets interrupted training resume with optimizer progress?",
    "Load balancing": "What distributes traffic among healthy replicas?",
    "Linux cgroups": "What Linux mechanism limits CPU and memory for process groups?",
    "Kubernetes scheduling": "What component chooses a suitable node for a pending pod?",
    "GPU Operator": "What manages accelerator drivers and device plugins across Kubernetes?",
    "InfiniBand": "Which cluster fabric is associated with low latency and remote memory access?",
}


def cases() -> list[dict]:
    result = []
    for index, (topic, content) in enumerate(DOCUMENTS):
        result.extend([
            {"query": topic, "expected": topic, "category": "exact_keyword"},
            {"query": PARAPHRASES[topic], "expected": topic, "category": "semantic_paraphrase"},
            {"query": f"Retrieve the verified {topic} procedure or learning note, not a merely similar concept.", "expected": topic, "category": ["procedure", "learning_state", "research_claim", "project_specific"][index % 4]},
            {"query": f"Current correct fact about {topic}; ignore older contradictory or similar-but-wrong memory.", "expected": topic, "category": ["old_vs_current", "contradictory", "similar_but_wrong", "reference"][index % 4]},
        ])
    return result


def metrics(rows: list[dict]) -> dict:
    ranks = [row["rank"] for row in rows]
    return {
        "cases": len(rows),
        "precision_at_1": sum(rank == 1 for rank in ranks) / len(rows),
        "precision_at_3": sum((1 / 3) if 1 <= rank <= 3 else 0 for rank in ranks) / len(rows),
        "recall_at_5": sum(1 <= rank <= 5 for rank in ranks) / len(rows),
        "mrr": sum((1 / rank) if rank else 0 for rank in ranks) / len(rows),
        "ndcg_at_5": sum((1 / math.log2(rank + 1)) if 1 <= rank <= 5 else 0 for rank in ranks) / len(rows),
        "false_memory_retrieval_rate": sum(rank != 1 for rank in ranks) / len(rows),
        "mean_latency_ms": round(statistics.mean(row["latency_ms"] for row in rows), 3),
        "p95_latency_ms": round(sorted(row["latency_ms"] for row in rows)[94], 3),
    }


def main() -> int:
    router = ModelRouter()
    registry = EmbeddingRegistry.from_config(router.config["embedding_model"], router.config["providers"])
    if not registry.active:
        print(json.dumps({"passed": False, "reason": "No active embedding model."}, indent=2)); return 2
    with tempfile.TemporaryDirectory(prefix="ai-retrieval-") as temporary:
        database = MemoryDatabase(Path(temporary) / "retrieval.db")
        database.initialize()
        expected_ids = {}
        for index, (topic, content) in enumerate(DOCUMENTS):
            expected_ids[topic] = database.store_memory(["semantic", "episodic", "procedural", "learning"][index % 4], content, .75, "retrieval_benchmark", metadata={"project": "AI" if index % 4 == 3 else None, "confidence": .85, "successful_uses": index % 3})
        database.embedding_provider = registry.active
        reindex = database.reindex_embeddings(batch_size=32)
        strategies = {
            "lexical_only": ("lexical", None),
            "semantic_only": ("semantic", None),
            "hybrid_default": ("hybrid", None),
            "hybrid_semantic_heavy": ("hybrid", {"neural": .48, "hashed_semantic": .06, "lexical": .20, "entity": .05, "project": .07, "recency": .03, "confidence": .04, "importance": .04, "successful_use": .03}),
        }
        reports = {}
        case_rows = cases()
        for name, (strategy, weights) in strategies.items():
            rows = []
            for case in case_rows:
                started = time.perf_counter()
                results = database.retrieve(case["query"], limit=5, strategy=strategy, project="AI" if case["category"] == "project_specific" else None, weights=weights)
                elapsed = (time.perf_counter() - started) * 1000
                expected = expected_ids[case["expected"]]
                rank = next((position for position, item in enumerate(results, 1) if item["id"] == expected), 0)
                rows.append(case | {"rank": rank, "top_id": results[0]["id"] if results else None, "latency_ms": round(elapsed, 3)})
            reports[name] = metrics(rows) | {"rows": rows}
        winner = max(reports, key=lambda name: (reports[name]["mrr"], reports[name]["recall_at_5"], -reports[name]["mean_latency_ms"]))
        report = {"embedding": registry.active.status(), "reindex": reindex, "strategies": reports, "selected_default": winner, "reranker": {"enabled": False, "reason": "No separately validated reranker quality/latency improvement yet."}}
        destination = PROJECT_ROOT / "data/benchmarks/retrieval/after.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({"embedding": report["embedding"], "reindex": reindex, "strategies": {name: {key: value for key, value in result.items() if key != "rows"} for name, result in reports.items()}, "selected_default": winner, "destination": str(destination)}, indent=2))
        return 0 if reports[winner]["recall_at_5"] >= .95 else 1


if __name__ == "__main__":
    raise SystemExit(main())
