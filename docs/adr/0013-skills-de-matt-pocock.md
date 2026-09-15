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

**Solo entran las skills que un agente puede invocar.** Veintidós de las 37 llevan
`disable-model-invocation: true`: son para un humano en un prompt (`/nombre`) y la
herramienta Skill las rechaza si las pide el modelo. Entre ellas están cuatro que el
diseño quería para los roles: `grill-with-docs`, `to-spec`, `implement` y `to-tickets`.
Las cuatro entran igual, por su contenido y no por invocación: `grill-with-docs` es solo
"cargá `grilling` y `domain-modeling`", que sí son invocables, y eso piden los
entrevistadores; `implement` son cuatro líneas (tdd, typecheck, code-review, commit) que
el prompt del Developer ya dice; `to-spec` aporta "confirmá las costuras", que entra por
el plan del Tech Lead, y una plantilla de spec que no reemplaza al formato de la
definición (competidores, comportamiento caso por caso, métricas, flags), porque ese
formato es contrato del sistema; `to-tickets` aporta el criterio de corte de subtareas
(rebanadas verticales demostrables, aristas de bloqueo, expand–contract para refactors
anchos), escrito en el prompt del Tech Lead.

**Cada rol ve solo las suyas.** Las demás son interactivas (teach, wizard) o publican en
el tracker (triage), y ADR-0001 reserva el issue para el humano. Entrevistadores
(ADR-0014): `grilling` y `domain-modeling`. Tech Lead: `codebase-design` y
`domain-modeling` al ser entrevistado y al planear, `code-review` al revisar. Developer: `tdd`, y
`resolving-merge-conflicts` solo el integrador. Un test comprueba que ningún rol pida una
skill no invocable. Cada skill invocada carga texto al contexto y cuesta plata.

**Dos son obligatorias en el prompt.** Developer escribe código solo dentro del loop de
`tdd`; Tech Lead revisa cada subtarea y la integración con `code-review` desde el commit
base de la subtarea. Consecuencia en el esquema de Tech Lead: cada subtarea declara sus
costuras (`seams`), porque `tdd` exige confirmarlas con "el usuario" antes de escribir un
test, y en una corrida sin humano el usuario es el plan. `code-review` corre dos
subagentes en paralelo; encarece la revisión y lo mide el set de evaluación.

**Lo que una skill publicaría, va al documento.** Ni sub-issues por subtarea ni un spec
en el tracker: la sección del rol en el documento de tarea. El issue solo recibe el link
al PR y las preguntas para el humano (ADR-0011, ADR-0014).

## Opciones descartadas

- **Path a la caché de usuario** (`~/.claude/plugins/cache/...`): no es reproducible en
  otra máquina y el plugin se actualiza solo.
- **`skills="all"` y que cada rol elija**: contexto y costo sin control, y las skills
  interactivas a mano de un rol sin humano.
- **Todas disponibles, ninguna obligatoria**: una skill disponible se usa cuando el
  modelo se acuerda, y lo obligatorio es lo que hace medible ADR-0012.
- **Copiar las skills no invocables al repo sin la marca**: es un fork del plugin que
  la versión fijada ya no describe; su contenido cabe en un prompt.

## Adenda (2026-09-14): los subagentes de una skill corren en Sonnet

En la segunda corrida de GH-12 la revisión con `code-review` costó entre 5.5 y 10 USD por
pase (issue #7). El pase de aprobación tenía dos turnos del Tech Lead y casi el mismo
costo que el de devolución con veintisiete: el gasto estaba en los dos subagentes de la
skill, que heredan el modelo del padre, Opus, y releen el repo.

**Todo subagente corre en el modelo de subagentes**, `claude-sonnet-5` por default
(`AGENTIC_SUBAGENT_MODEL`), para cualquier rol: el padre razona en su modelo, los
subagentes leen y resumen. El runner lo pasa a cada proceso como
`CLAUDE_CODE_SUBAGENT_MODEL`, que Claude Code aplica a cada llamada de la herramienta
Agent, skills incluidas; ni la skill ni el sistema tienen que nombrar un agente. Cada
corrida de rol registra en el documento el costo por modelo (`cost_by_model`, del
`model_usage` del SDK), que es lo que explica una corrida de dos turnos y diez dólares:
`total_cost_usd` cuenta los subagentes, `num_turns` no. Sin gauge nuevo en Grafana:
`agentic.role.cost_usd` ya da el costo total de cada corrida de rol, con el modelo del
padre como etiqueta, y ese total es lo que tiene que bajar; el desglose queda en el
documento, para diagnosticar.

Dos correcciones al prompt del revisor, del mismo diagnóstico. El producto no tiene
`CODING_STANDARDS.md` propio y la skill busca estándares documentados en el repo: el
Tech Lead pega los `## Engineering standards` que recibe en el prompt del subagente
Standards. Y los subagentes de la skill bloquean hasta volver: el revisor no espera con
`sleep`, que además su jaula le niega y le costaba turnos.

Descartado revisar sin la skill, con `git diff` y ADR-0012 a mano: la revisión de GH-12
encontró un agujero real (imports relativos que esquivaban el test de arquitectura) y los
dos ejes separados son lo que hace comparable una revisión con otra. Descartado poner toda
la revisión en Sonnet: el veredicto queda en el modelo validador (README §10).
