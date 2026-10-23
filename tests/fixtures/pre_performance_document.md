---
id: GH-102
title: 'El Developer agota los 80 turnos con el código commiteado y sin entregar:
  diez veces en un día, una escalación'
type: platform_enhancement
status: ready_for_review
current_role: PjM
flags: {}
returns:
  Tech Lead→Developer:turn-notice-cadence: 1
escalated: null
branch: agentic/GH-102
pull_request: https://github.com/GonzaloZevallos/agentic-system/pull/108
cost_usd: 28.3904
prompts:
  pjm: 99b1729f
  tech_lead_ephemeral_platform: 1daa1a52
  tech_lead_interviewee_platform: e16c8a2f
  tech_lead_platform: 728f2ecb
  developer: d53e1b6b
  tech_lead_review: 1efdec03
plugins: {}
system_commit: 73aac90
system_version: 73aac90
delivery_outcome: merged
merge_commit: 7f9d25bb589b802d26aaab3a18af5b950f84d1a1
subtasks:
  turn-notice-cadence:
    id: turn-notice-cadence
    title: One re-firing close-out notice on a counter that cannot lag the cap
    brief: 'Revise the live turn-budget notice (shipped in c840a41, ADR-0004 seventh
      addendum) in one vertical slice. Evidence already read from .agentic/tasks/
      and quoted in the plan: every `slug: developer` fai'
    seams:
    - tests/core/process/domain/test_turn_notice.py
    - tests/core/process/adapters/claude_sdk/test_hooks.py
    - tests/core/process/adapters/claude_sdk/test_runner.py
    depends_on: []
    status: approved
    branch: agentic/GH-102
    base: 73aac90
    reviewed_head: a1f8a66
    reason: The declared-check enforcement artefact at the domain seam is never run
      on a rejecting input — the companion test re-implements the comparison as a
      tautology (`assert not (15 < 15)`) and would pass with the check deleted.
    return_category: spec-missing-test
runs:
- role: PjM
  slug: pjm
  section: PjM
  version: 1
  session_id: d023c053-1191-415e-8c52-53228f85d43c
  model: claude-sonnet-5
  cost_usd: 0.0212
  cost_by_model:
    claude-haiku-4-5-20251001: 0.0017
    claude-sonnet-5: 0.0195
  turns: 2
  max_turns: 2
  started_at: '2026-09-23T01:26:45.782+00:00'
  ended_at: '2026-09-23T01:26:49.973+00:00'
  queue_wait_s: 6.5019994508475065e-06
  tokens:
    input: 2
    output: 327
    cache_read: 0
    cache_write: 4052
  calls: 1
  tool_calls:
    StructuredOutput: 1
  subagent_cost_usd: 0.0017
  resumed_from: ''
  cold_fallback: false
  failed_cost_usd: 0
  denials: 0
  turn_notice: false
  deliveries: 1
  presubmit_denials: 0
- role: Tech Lead efímero
  slug: tech_lead_ephemeral_platform
  section: 'Interview: Tech Lead efímero→Tech Lead'
  version: 1
  session_id: 32891798-3da9-41d1-87a2-1de7d9609ce3
  model: claude-opus-5
  cost_usd: 3.0585
  cost_by_model:
    claude-haiku-4-5-20251001: 0.1219
    claude-opus-5: 2.9365
  turns: 17
  max_turns: 12
  started_at: '2026-09-23T01:26:50.570+00:00'
  ended_at: '2026-09-23T01:29:53.800+00:00'
  queue_wait_s: 8.616014383733273e-06
  tokens:
    input: 18
    output: 13683
    cache_read: 1406715
    cache_write: 189102
  calls: 24
  tool_calls:
    Bash: 14
    StructuredOutput: 1
  subagent_cost_usd: 0.1219
  resumed_from: ''
  cold_fallback: false
  failed_cost_usd: 0
  denials: 3
  turn_notice: false
  deliveries: 1
  presubmit_denials: 0
- role: Tech Lead
  slug: tech_lead_interviewee_platform
  section: 'Interview: Tech Lead efímero→Tech Lead'
  version: 2
  session_id: 29fbef11-c4ba-472f-97ea-37793bafd3eb
  model: claude-opus-5
  cost_usd: 3.313
  cost_by_model:
    claude-haiku-4-5-20251001: 0.1245
    claude-opus-5: 3.1884
  turns: 19
  max_turns: 12
  started_at: '2026-09-23T01:29:53.805+00:00'
  ended_at: '2026-09-23T01:32:15.669+00:00'
  queue_wait_s: 7.08398874849081e-06
  tokens:
    input: 24
    output: 10727
    cache_read: 1973355
    cache_write: 193347
  calls: 31
  tool_calls:
    Skill: 1
    Bash: 10
    Glob: 1
    Grep: 5
    StructuredOutput: 1
  subagent_cost_usd: 0.1245
  resumed_from: ''
  cold_fallback: false
  failed_cost_usd: 0
  denials: 4
  turn_notice: false
  deliveries: 1
  presubmit_denials: 0
---

# El Developer agota los 80 turnos con el código commiteado y sin entregar: diez veces en un día, una escalación

## Original request

Copied from GH-102 (a document written before QA Performance): trimmed to the first runs and one subtask.
