This feature has no approved Spec, so your approval also produces a draft Spec: return its body in `spec_draft`. The system writes the file, adds the front matter and checks the format; you never set a status.

`spec_draft` is the body only, in Markdown: no front matter block (no line `---`), no template braces, no HTML comment, and no line that is only `=== spec data ===`. It is the PRD you just validated, restated as one Spec the owner can approve, with these `##` sections, each once and not empty, and no other `##` heading:

- `## Goal`: what the task delivers and for whom, in a few sentences.
- `## Acceptance criteria`: one bullet (`- text`) per criterion, each checkable on the delivered change alone.
- `## Out of scope`: what this task does not do.
- `## Decisions`: one top-level bullet per decision you took where the request was silent, each with exactly one indented sub-bullet `  - Reason: <why>`.
- `## Risks`: the risks of the task as lines of text, or `none`.
- `## Screens`: when the task touches the UI, say that the screens come from the design stage and write no `###` entry; otherwise say `none`.
- `## Slices`: at least one `### <slug>` (lowercase, digits and hyphens), each with `#### Acceptance criteria` (at least one bullet) and `#### Risks` (slug bullets, or a single `- none`). A task that is one piece of work is one slice.

Leave `spec_draft` out when your verdict is `returned`.
