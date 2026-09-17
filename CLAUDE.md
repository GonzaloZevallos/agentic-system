# agentic-system

Sistema agéntico de desarrollo: 17 roles sobre LangGraph + Claude Agent SDK.
`README.md` es el estado consolidado del diseño; leerlo antes de proponer
arquitectura o roles. Su sección 13 lista decisiones ya reemplazadas: no
reintroducirlas.

Este repo es el **sistema**, y desde ADR-0017 es también su propio segundo producto:
el sistema se desarrolla a sí mismo. La regla no es que no escriba acá, es que
**nunca escribe en el checkout desde el que corre**, y eso se verifica por repositorio y no
por ruta (ADR-0002, segunda adenda): una corrida la ejecuta `stage0`, un **clon** aparte
fijado a un sha, y los artefactos (`.agentic/`, la rama `agentic/<tarea>`, el pull request)
caen en este repo. `scripts/stage0.sh <ref> [dir]` lo crea o lo actualiza. El otro producto, el SaaS de
gimnasios, sigue en su repo y sigue siendo la base del set de evaluación
(README.md, secciones 8 y 9).

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
