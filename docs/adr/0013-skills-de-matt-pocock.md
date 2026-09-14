---
status: accepted
date: 2026-09-13
---

# Las skills de Matt Pocock entran fijadas como submódulo y son obligatorias por rol

El humano desarrolla con el plugin `mattpocock-skills` y quiere que los roles hagan lo
mismo. El SDK 0.2.152 carga plugins locales (`plugins=[{"type": "local", ...}]`) y filtra
qué skills ve un rol (`skills=[...]`) sin tocar el aislamiento del runner
(`setting_sources=[]`). El plugin es MIT.

**Fijado como submódulo git**, `mattpocock/skills` en la versión 1.2.3 bajo
`agentic/plugins/mattpocock-skills/`. La versión queda en el frontmatter del documento de
tarea junto a los hashes de prompts. Es la misma regla que ADR-0005 aplica a los prompts:
lo que cambia el comportamiento se versiona y se registra por tarea, y §9 exige re-correr
la línea de base idéntica. Actualizar el plugin es un commit visible.

**Cada rol ve solo las suyas.** De las 37 del plugin, la mayoría son interactivas (grill,
teach, wizard) o publican en el tracker (to-tickets, triage), y ADR-0001 reserva el issue
para el humano. Developer: `tdd`, `implement`, y `resolving-merge-conflicts` solo el
integrador. Tech Lead: `codebase-design`, `domain-modeling`, `code-review`. Los
entrevistadores (ADR-0014): `grill-with-docs` y `to-spec`. Cada skill invocada carga texto
al contexto y cuesta plata; una interactiva en un rol sin humano se cuelga o alucina la
respuesta.

**Tres son obligatorias en el prompt.** Developer arranca cada subtarea con `implement` y
escribe código solo dentro del loop de `tdd`; Tech Lead revisa cada subtarea y la
integración con `code-review` desde el commit base de la subtarea. Consecuencia en el
esquema de Tech Lead: cada subtarea declara sus costuras (`seams`), porque `tdd` exige
confirmarlas con "el usuario" antes de escribir un test, y en una corrida sin humano el
usuario es el plan. `code-review` corre dos subagentes en paralelo; encarece la revisión y
lo mide el set de evaluación.

**Lo que las skills publican, va al documento.** `to-spec` publica en el tracker
configurado; acá el prompt lo redirige a la sección del rol en el documento de tarea. El
issue solo recibe el link al PR y las preguntas para el humano (ADR-0011, ADR-0014).

## Opciones descartadas

- **Path a la caché de usuario** (`~/.claude/plugins/cache/...`): no es reproducible en
  otra máquina y el plugin se actualiza solo.
- **`skills="all"` y que cada rol elija**: contexto y costo sin control, y las skills
  interactivas a mano de un rol sin humano.
- **Todas disponibles, ninguna obligatoria**: una skill disponible se usa cuando el
  modelo se acuerda, y lo obligatorio es lo que hace medible ADR-0012.
