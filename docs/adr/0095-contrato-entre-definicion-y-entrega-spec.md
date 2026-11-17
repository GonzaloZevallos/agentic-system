---
status: accepted
date: 2026-11-13
---

# Contrato entre definición y entrega: la Spec

GH-594. Hoy la entrega de una feature reconstruye lo que quiso el dueño desde la cadena de
definición (PRD, entrevista, plan). La Spec es un archivo aprobado bajo `docs/specs/` que la
entrega lee en su lugar. Este documento fija el contrato entre las dos puntas y dice, regla por
regla, cuál ya tiene código en master y cuál todavía no.

## Contexto

El trabajo de GH-594 llega en partes. Algunas ya están en master (el formato de la Spec y su
lector); otras todavía no. Para que nadie lea una regla pendiente como vigente, la tabla de
cumplimiento de abajo dice qué aplica cada pieza y desde cuándo. Las reglas sin código se citan
solo por su marcador `GH-594 parte N`.

## Decisión

### Aplicado hoy

- **Formato y hash.** El formato de la Spec lo define y valida
  `agentic/core/knowledge/domain/spec.py`; el hash del cuerpo es `spec_delivery.body_sha256`
  (`agentic/core/process/domain/spec_delivery.py`). Este ADR los señala y no los reescribe.
- **Forma de la ruta.** La decide `spec_delivery.spec_path_ok`: `docs/specs/<nombre>.md`, un solo
  segmento.
- **Contrato de lectura.** Lo cumple `GitSpecReader`
  (`agentic/core/knowledge/adapters/git/spec_reader.py`) detrás del puerto `SpecReader`
  (`agentic/core/process/application/ports.py`):
  - solo un id de commit completo, de 40 o 64 dígitos, que `rev-parse` devuelve igual; un id
    abreviado es un error del llamador;
  - solo bytes commiteados, leídos con `ls-tree -l -z` y luego `cat-file blob <sha>`, nunca del
    working tree;
  - solo blobs de modo `100644` o `100755`: ni symlink, ni submódulo, ni árbol;
  - a lo sumo 64 KiB (`SPEC_MAX_BYTES`), chequeado antes de cargar el contenido;
  - todo `GIT_*` del entorno se descarta, y los replace refs y el lazy fetch quedan apagados;
  - toda falla es una `SpecUnreadable` con la causa cerrada `spec_unreadable` y un motivo
    cerrado, nunca texto de git. Los motivos son los de `SpecReadRefusal`, en
    `agentic/core/process/domain/spec_delivery.py`; no se copian acá. Los ancla el test
    `tests/core/knowledge/adapters/git/test_spec_reader.py::test_the_contract_of_a_known_and_an_unknown_spec`.

### Decidido, aún sin código

- **Fallback** cuando una feature no tiene Spec, con aprobación del dueño antes de construir:
  GH-594 parte 5c.

### Tabla de cumplimiento

| regla | aplicada por | parte | vigente hoy |
| --- | --- | --- | --- |
| 1a formato y hash | `spec.py`, `spec_delivery.py` | GH-594 parte 1a | sí |
| 1b lectura y forma de ruta | `spec_reader.py`, `spec_delivery.py`, `ports.py` | GH-594 parte 1b | sí |
| 2a supuestos y `spec_cause` | `spec_assumptions.py` (`schema_for`, `assumption_rows`, llamadas por `run_role.py`), `rules.escalation` (`spec_cause`) | GH-594 parte 2a | sí |
| 2b campos de supuestos en los esquemas de rol | `spec_conflict` en `schemas.py`, leído por `plan.py` (`spec_assumptions.conflict_step`) y por `interview.py` | GH-594 parte 2b | sí |
| 3 revisión de Tech Writer sobre la Spec | — | GH-594 parte 3 | no |
| 4a parseo de la línea `Spec:` y ruteo puro | `spec_delivery.parse_spec_line`, `spec_delivery.body_source`, `rules.spec_delivery` | GH-594 parte 4a | sí |
| 4b la ruta | `classify.py:spec_intake`, `spec_guard.py`, `review.py` (`spec-scope`), `landing_plan.py` (`spec_carry_ok`), `agentic/composition.py:_ports` | GH-594 parte 4b | sí |
| 4c eval compare y README | — | GH-594 parte 4c | no |
| 5a Spec borrador en la cadena de definición | — | GH-594 parte 5a | no |
| 5b reglas de aprobación | — | GH-594 parte 5b | no |
| 5c fallback y aprobación del dueño antes de construir | — | GH-594 parte 5c | no |

### Qué rige hoy, en palabras llanas

Master ya enruta por `Spec:`: un issue que trae la línea se entrega desde la Spec aprobada, sin
definición, y uno que no la trae no cambia (la entrada «Entrega desde Spec» de
`agentic/core/process/CONTEXT.md` lo detalla). Sigue en `no` la revisión de Tech Writer sobre la
Spec (fila 3), el `eval compare` y el README (4c) y la Spec borrador, las reglas de aprobación y el
fallback (5a, 5b y 5c). Hasta que salga GH-594 parte 5c, una feature se construye sin Spec, como hoy.

## Consecuencias

- La lectura usa `cat-file` por el sha del blob que da `ls-tree -l -z`, en vez de un
  `git show <commit>:<ruta>` literal, para que el modo y el tamaño chequeados sean los del
  objeto que se lee.
- El segundo chequeo, sobre los bytes ya cargados (`len(stdout) > SPEC_MAX_BYTES`), es defensa en
  profundidad.
- Cuando salga GH-594 parte 5c, la tabla y el párrafo «Qué rige hoy» dejan de ser ciertos y se
  actualizan en esa parte.

## Adenda (GH-809): la línea `Spec:` se lee del issue al reanudar

Una tarea rechazada por `spec_line_malformed`, `slice_unknown` o `slice_missing` se arregla
editando la línea `Spec:` del issue, pero al reanudar `description` se reconstruye desde la sección
«Original request» del documento y nadie vuelve a leer el issue.

- En una reanudación (la `pause_log` tiene una entrada `resumed`) de una tarea cuya `description`
  tenía una línea `Spec:` candidata, bien formada o mal formada, `spec_intake` lee esa línea del
  issue por el puerto `Inbox` y no de `description` (`spec_delivery.body_source`). El texto leído
  solo se parsea: no se escribe en el estado ni en el documento.
- Una lectura fallida (`ValueError` u `OSError`) o un cuerpo vacío es `spec_unreadable` con detalle
  `unreachable` y una frase fija; ningún texto del cuerpo ni de `gh` llega al motivo, al detalle ni
  al documento. Un cuerpo leído sin línea no enruta por la spec.
- «Original request» puede entonces mostrar la línea rota junto a una `spec` válida. La traza es el
  escalado de clasificación y la entrada `resumed` en la `pause_log`, y la `spec` registrada con su
  `path`, `slice` y `commit`.
