# agentic-system

Sistema agéntico de desarrollo: 17 roles sobre LangGraph + Claude Agent SDK.
`README.md` es el estado consolidado del diseño; leerlo antes de proponer
arquitectura o roles. Su sección 13 lista decisiones ya reemplazadas: no
reintroducirlas.

Este repo es el **sistema**. El producto sobre el que trabaja vive en otro repo
y el sistema nunca escribe acá cuando corre (README.md, sección 8).

## Agent skills

### Issue tracker

Issues de este repo en GitHub Issues de `GonzaloZevallos/agentic-system`, vía `gh`.
See `docs/agents/issue-tracker.md`.

### Triage labels

Las cinco etiquetas canónicas sin renombrar: `needs-triage`, `needs-info`,
`ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Multi-context: `CONTEXT-MAP.md` en la raíz apunta a un `CONTEXT.md` por módulo en
`agentic/core/<módulo>/`; los ADRs del sistema en `docs/adr/`, los de un módulo en
`agentic/core/<módulo>/docs/adr/`, creados de forma lazy. See `docs/agents/domain.md`.
