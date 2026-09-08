---
type: llm
weight: 1
---
The correct diagnosis is that a child database is carved out of its PARENT's unallocated PERM
space, so system-wide free space is irrelevant: the parent (`staging`) does not have 2 GB of
unallocated space left.

PASS requires ALL of:
- The answer states that the space comes from the parent database, not from the system, and says
  that a system-wide or whole-system free-space figure will mislead here.
- It gives a check scoped to the PARENT. Any of these count: `DBC.DatabasesV.PermSpace` for
  `staging`, or `DBC.DiskSpaceV` filtered to `DatabaseName = 'staging'` — what matters is that
  the check is filtered to the parent rather than aggregated over the whole system.
- It gives a concrete fix: raise the parent first, for example
  `MODIFY DATABASE staging AS PERM = ...`, then retry the CREATE.
- Any statement that writes is presented as something the user runs and approves, not silently
  executed.

FAIL if the answer blames syntax, suggests only lowering the requested PERM without mentioning
the parent, or points solely at system-level free space.
