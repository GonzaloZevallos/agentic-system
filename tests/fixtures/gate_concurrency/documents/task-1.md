---
id: GH-1
runs:
- role: Developer
  gates:
  - {started_at: '2026-10-20T10:00:00+00:00', wait_s: 0.0, run_s: 600.0, share: 6, peers: 0, priority: subtask, overdue: false}
  - {started_at: '2026-10-20T10:20:00+00:00', wait_s: 120.0, run_s: 480.0, share: 4, peers: 1, priority: subtask, overdue: false}
- role: Developer
  gates:
  - {started_at: oops, wait_s: 1.0, run_s: 1.0}
---

## Original

A task with two gates on one run: the first was refused, the second delivered.
