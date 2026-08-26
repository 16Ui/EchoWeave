---
status: accepted
---

# Use expiring execution leases with fencing tokens

EchoWeave uses a filesystem-backed expiring Lease with a monotonically increasing fencing token to assign one Execution Owner to a Logical Turn. A process-local keyed Singleton coordinates threads and heartbeats, but it is deliberately not treated as global ownership: plain in-memory locks cannot survive process restart, while a permanent file lock cannot distinguish a crashed owner from a slow one; the expiring lease enables takeover and the fencing token lets cooperative execution boundaries reject stale owners.

## Consequences

Lease mutation must occur under a cross-process file lock, and active executions must verify their fencing token before Provider and Tool boundaries. A process paused longer than the lease TTL can lose ownership; external systems still require idempotency keys or their own fencing support to reject an already-running stale side effect.
