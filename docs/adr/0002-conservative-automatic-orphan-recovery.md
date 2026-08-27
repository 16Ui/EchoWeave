---
status: accepted
---

# Automatically recover only lease-proven orphan turns

EchoWeave runs automatic recovery as an opt-in lifecycle component and only considers non-suspended incomplete Turns whose persisted Lease has expired and whose starting checkpoint still exists. Discovery does not reserve a Turn: the worker must acquire the Lease and revalidate the durable state before writing recovery events, while a per-Turn attempt ceiling prevents repeated process crashes from becoming an infinite retry loop.

## Consequences

Terminal failures, missing legacy Leases, suspended side effects and released-but-incomplete records remain operator-visible instead of being guessed safe. Multiple schedulers may scan the same storage, but only the Lease winner can mutate the recovery history.
