---
status: accepted
date: 2026-09-22
---

# "Definición única" se renombra a PRD; lo que sobrevive a la corrida se congela byte a byte

El grill del 2026-09-15 decide dos movimientos de vocabulario. Este es el segundo: lo que
PM entrega por tarea deja de llamarse "definición única"/`Definition` y pasa a llamarse
**PRD**. El destilado por capacidad, `docs/product/<capacidad>.md`, no es este término —
sigue siendo la definición vigente de la capacidad, y nada en este diff lo toca.

## La línea (ADR-0008, adenda del 2026-09-16)

*Lo que sobrevive a la corrida conserva su nombre; lo que se reescribe sigue al dominio.*
Identificadores, nombres de clase, nombres de módulo, prosa de prompt, docs y glosario
siguen al dominio: `Definition` → `PRD`, `validate_definition.py` → `validate_prd.py`,
`DEFINITION_INTERVIEW` → `PRD_INTERVIEW`, `DEFINITION_RETURN` → `PRD_RETURN`, como
**nombres únicamente**. Cada cadena persistida y cada valor de label se congela byte a
byte, porque ninguna de las dos vive en el código que este diff toca: vive en documentos
ya escritos, en checkpoints de LangGraph ya corridos, y en series de Grafana Cloud ya
grabadas con esos bytes como valor.

## Lo que se renombra

- `agentic/core/process/domain/schemas.py`: `Definition(RoleOutput)` → `PRD(RoleOutput)`.
- `agentic/core/process/domain/roles.py`: `DEFINITION_INTERVIEW` → `PRD_INTERVIEW`. Su
  valor, `"Interview: Product Analyst→PM"`, no cambia un byte — sigue siendo la clave que
  `Role.reads` usa en las tres tuplas de Product Analyst, PM y PM_CONSULTANT, elemento por
  elemento.
- `agentic/core/process/domain/rules.py`: `DEFINITION_RETURN` → `PRD_RETURN`. Su valor,
  `ChangeCategory` `"definition"`, no cambia — sigue siendo lo que
  `rules.change_category`/`resumed_status` comparan contra `change_history[i]["category"]`,
  que un documento ya escrito ya tiene grabado con ese byte.
- `agentic/core/process/application/validate_definition.py` → `validate_prd.py`, función
  `validate_definition` → `validate_prd`, importado por `graph.py`. El nombre del nodo de
  LangGraph, `"validate"`, no se toca — un módulo puede cambiar de nombre; un nodo
  registrado con `graph.add_node("validate", ...)` es un string que un checkpoint ya
  serializado usa para decidir dónde retomar, y ese string no aparece en este diff.
- Prompts (prosa, no código): Product Analyst (entrevistador y autor), PM (entrevistado,
  validador, consultor), Tech Lead (las cuatro variantes, feature y platform) y Developer,
  donde la prosa nombra el entregable propio de PM por tarea. Sin tocar: `pm_bug.md` y
  `product_analyst_bug.md`, que nombran la definición propia de la capacidad bajo
  `docs/product/` — un bug no tiene PRD que consolidar — y toda frase "definición vigente
  de cada capacidad", el mismo concepto de `docs/product` sin renombrar en ningún prompt.
- `README.md` (§2, §3, §4, §6, §13) y `docs/adr/0014-*`, donde nombran el entregable de PM
  por tarea; `agentic/core/process/CONTEXT.md`, término **Definición única** → **PRD**, con
  `_Avoid_` y `_Campo_` al día. `docs/adr/0006-*` no necesitó cambio: ahí toda mención de
  "definición" ya nombraba el contenido propio de `docs/product`, no el entregable de PM.
- `tests/core/process/domain/test_schemas.py`,
  `tests/core/process/application/test_context_golden.py` (re-pin del golden por el cambio
  de prosa de prompt únicamente),
  `tests/core/process/application/test_technical_stage.py`,
  `tests/core/process/application/test_use_cases.py`: siguen el rename de identificador
  donde importaban `Definition`/`DEFINITION_INTERVIEW`/`DEFINITION_RETURN`.

Ningún test se escribe contra el texto de un prompt (estándar §5; precedente ADR-0023): el
cambio de prosa se verifica por el golden de `test_context_golden.py`, no por un `assert`
sobre una frase.

## Lo que se congela, byte a byte, y por qué cada uno

- **`Status`** `"definition_ready"` y `"defined"` (`task.py`, `rules.py`): un documento de
  tarea ya escrito tiene `status: definition_ready` en su frontmatter; `resume.from_document`
  lo lee de vuelta sin pasar por ningún parser que sepa de nombres de clase, y
  `rules.resume_target`/`rules.FIRST_NODE` lo comparan contra el string literal. Cambiar el
  byte rompería la reanudación de cada tarea que hoy está pausada con ese status grabado.
- **`rules.PHASES`** y en particular la fase `"definition"`: es el label que
  `_emit_interview`/las métricas de fase escriben en series ya grabadas en Grafana Cloud
  (ADR-0008). Un cambio de byte partiría la serie en dos series distintas el día del merge,
  sin que ninguna corrida nueva lo declare.
- **La categoría de cambio** `"definition"` (el valor de `PRD_RETURN`): mismo argumento que
  `Status` — vive en `change_history[i]["category"]` de documentos ya escritos y en la
  métrica de devoluciones por categoría.
- **El título de sección** `"Interview: Product Analyst→PM"` (el valor de
  `PRD_INTERVIEW`): es la clave bajo la que `Role.reads` busca la sección en
  `state["sections"]`, y el título literal que `document.render` ya escribió en cada
  documento de tarea con una entrevista de definición. Renombrar el símbolo Python que la
  nombra no toca el string.
- **Los valores de etapa de entrevista** `"definition"`/`"solution"` (`Literal` en
  `roles.py`/`task.py`, el campo `interview` de `Interview`, emitido como label `interview`
  por `_emit_interview`): mismo argumento que las fases — es un valor de label ya grabado.
- **El checkpoint de LangGraph**, congelado **por inspección y por el invariante, no por un
  test**: todo nodo (`"classify"`, `"define"`, `"diagnose"`, `"validate"`, `"plan"`, y las
  constantes `PIPELINE`/`INTEGRATE`/`ESCALATE`/`PAUSE`) se registra con el mismo string
  literal antes y después de este diff — verificable por inspección de
  `graph.add_node(...)` en el mismo diff que este ADR describe. No se agrega un fixture que
  siembre un `AsyncSqliteSaver` para afirmar identidad de nombres de nodo: el invariante ya
  lo vuelve *greppable* sobre el diff (ningún `graph.add_node`, `as_node=` o valor de
  `PIPELINE`/`INTEGRATE`/`ESCALATE`/`PAUSE` cambia), y los tests unitarios de
  `rules.FIRST_NODE`/`resume_target`/las funciones `after_*` ya fijan esos valores por otro
  lado. Un test de checkpoint no agregaría cobertura que el invariante y esos tests no den
  ya, y sí agregaría un fixture binario frágil.

**Ningún `RECORDED_AS` se agrega**, porque nada persistido se mueve: `RECORDED_AS` existe
para decirle a `resume.from_document` bajo qué clave vieja leer un campo que cambió de
nombre en el documento; aquí ningún campo del documento cambia de nombre, solo el símbolo
Python que en algún módulo apunta al mismo byte de siempre. Agregar una entrada pagaría el
costo de una migración para un rename que no la necesita.

## El esquema estructurado: el título se mueve, y nada lo registra

`schemas.json_schema` (`schemas.py`) reescribe únicamente `flags` y los campos de
lista-de-líneas; el resto del `model_json_schema()` de Pydantic pasa sin tocar, `"title"`
incluido — y `"title"` es el nombre de la clase. Renombrar `Definition` a `PRD` mueve
`json_schema(PRD, {})["title"]` de `"Definition"` a `"PRD"`, y ese movimiento no aparece en
ningún fingerprint: `prompts.fingerprint` (ADR-0008) hashea el prompt, no el
`output_format` que la CLI construye a partir del esquema. Así que **la tasa de rechazo de
esquema estructurado puede cambiar en este commit sin que ningún fingerprint lo señale** —
un cambio en esa tasa alrededor de este commit es este rename, no el modelo. Por eso
`json_schema(PRD, {})` queda pinneado entero, título incluido, en el mismo diff que renombra
la clase (`tests/core/process/domain/test_schemas.py::test_prd_json_schema_is_pinned`): es
el único artefacto que hace ese movimiento verificable después del hecho.

## `prompts.fingerprint` se rompe, y esto lo dice donde el invariante 5 exige que se diga

Este diff toca `agentic/core/process/domain/prompts/` (la prosa "consolidated definition" en
seis prompts: Product Analyst interviewer/author, PM interviewee/validator/consultant, Tech
Lead, Developer). `prompts.fingerprint` (ADR-0008) hashea el contenido de cada prompt, así
que el fingerprint de cada uno de esos seis prompts cambia con este commit: toda serie
segmentada por fingerprint pierde comparabilidad hacia atrás para esos seis roles a partir
de este commit. `agentic.review.findings` no tiene label de fingerprint y sigue comparable
sin cambios. Esta misma frase va también en el cuerpo del pull request que trae este diff
(invariante 5).

## Lo que queda explícitamente afuera de este diff

`graph.py`'s node registrations, `rules.FIRST_NODE`, `rules.resume_target`, las funciones
`after_*`, `document.py`'s `RECORDED_AS`, `BRIEF.md`, `docs/adr/0025-*`,
`preflight.py` (byte a byte igual — evidencia: `git diff --stat -- 
agentic/core/process/application/preflight.py` sin salida), y cualquier valor de campo de
`Role`. Ningún branch, default, orden de llamada o condición de runtime se mueve en este
diff: es identificadores, prosa, docs y glosario, nada más — verificable leyendo el diff
como texto, sin ejecutar nada.

## La prueba de que el comportamiento no cambió

- `tests/core/process/domain/test_schemas.py::test_prd_json_schema_is_pinned` — el esquema
  entero, título incluido.
- `tests/core/process/application/test_resume.py::test_a_document_written_before_the_prd_rename_still_resumes` —
  un documento con `status: definition_ready` escrito con el vocabulario viejo se parsea y
  se reanuda con `rules.resume_target` dando el mismo destino de siempre.
- `tests/test_language.py` — el glosario de `core/process` (`agentic/core/process/CONTEXT.md`)
  sigue siendo la única fuente de los términos que `TYPES` mapea; ninguna entrada quedó
  huérfana por el rename.
- `tests/core/process/application/test_context_golden.py` — el golden del ensamblado de
  contexto se mueve solo donde la prosa de un prompt cambió, nada más.

## Opciones descartadas

- **Congelar `Definition` como alias** de `PRD` para no tocar los call sites: deja dos
  nombres para el mismo concepto, exactamente el "nombre a medias" que el pedido descarta
  ("va entero o no va").
- **Un test de checkpoint de LangGraph** que siembre un `AsyncSqliteSaver` y afirme
  identidad de nombres de nodo: el invariante ya lo vuelve greppable sobre el diff, y los
  tests de `resume_target`/`after_*` ya fijan esos valores; el fixture solo agregaría
  fragilidad binaria sin cobertura nueva.
- **Reusar `JOBS_HEADING`** o cualquier constante de la sección `## Jobs` del Brief para el
  rename: el Brief y el PRD son contratos distintos que cambian por razones distintas
  (ADR-0025); este diff no los acopla.
