# Storage manager

The storage manager is confined to `D:\AI`. Its maximum budget is 3,000,000,000,000 bytes and it preserves at least 300,000,000,000 bytes of physical disk free space. Within that ceiling, durable learning has its own 1,000,000,000,000-byte allocation. These are limits, not preallocated files. Effective remaining capacity is the smallest applicable category/project quota and free disk after the reserve.

Governed downloads under `data/downloads` count against this same policy. Embeddings, datasets, cache, tools, backups, and camera temporaries remain below the project root; temporary camera observations are purged after use.

Registered filesystem writes, appends, and copies reserve-check their additional bytes before mutation. A write is rejected if it would exceed either the project quota or the physical free-space reserve.

`/storage` shows usage by category, including learning allocation and use. Natural or typed controls include `storage status`, `storage refresh`, `storage largest`, and `storage clean`. Cleanup only considers disposable work/cache files, duplicate quarantined downloads, expired research cache rows, and old disposable screenshots. It does not remove durable knowledge, learning history, models, projects, datasets, backups, generated images, or user artifacts.

`storage status` is served from a cache (default up to 60 seconds old, always reported via `last_scanned_at`/`cache_age_seconds`) rather than re-walking the entire project tree on every call -- a full scan crosses `.venv` alone at 10,000+ files, and code that only needed a single static number (like the project budget) used to trigger one anyway. `storage refresh` forces an immediate rescan when you need a guaranteed-current number.

The local-model inventory queries Ollama without downloading anything and records size, quantization, context length, advertised capabilities, vision support, modification time, and any saved benchmark result. Model downloads are never automatic.

The implementation is accounting and policy, not extra disk space: the physical volume still controls what can actually be stored.
