---
id: GH-2
merge_checks:
  passed: true
  duration_s: 600.0
  gate: {started_at: '2026-10-20T10:25:00+00:00', wait_s: 0.0, run_s: 600.0, share: 10, priority: land, overdue: false}
full_checks:
  version: 1
  passed: false
  gate: {started_at: '2026-10-20T11:00:00+00:00', wait_s: 0.0, run_s: 300.0, share: 12, priority: integration, overdue: false}
---

## Original

A merge that passed and a full-checks run that did not.
