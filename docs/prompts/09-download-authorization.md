# Free dependency and download authorization

**Intent:** Allow normal free project-local dependencies to be obtained without repeated questions while retaining security boundaries.

**Requirements:** Prefer authoritative sources/package managers; verify HTTPS/publisher/version/hash; assess disk/RAM/VRAM/license/overlap for models; project/user scope first; no elevation, purchases, security changes, or unknown executables.

**Introduced:** Governed download registry, authoritative host policy, redirect revalidation, byte/quota limits, SHA-256, partial cleanup, and dependency inventory.

**Current status:** Downloads are never automatically executed. The current 500 GB project budget is a maximum with a 300 GB physical free-space reserve.
