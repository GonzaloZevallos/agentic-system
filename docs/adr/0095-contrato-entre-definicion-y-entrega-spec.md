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

- **La línea `Spec:`**, con un `#<rebanada>` opcional, que dice a la entrega qué Spec leer y qué
  parte construir: GH-594 parte 4a (parseo y ruteo puro) y GH-594 parte 4b (la ruta).
- **Supuestos en lugar de preguntas** en una entrega con Spec, con `spec_cause` (`contradiction`,
  `conflict`, `data_loss`): GH-594 parte 2a y GH-594 parte 2b.
- **Fallback** cuando una feature no tiene Spec, con aprobación del dueño antes de construir:
  GH-594 parte 5c.

### Tabla de cumplimiento

| regla | aplicada por | parte | vigente hoy |
| --- | --- | --- | --- |
| 1a formato y hash | `spec.py`, `spec_delivery.py` | GH-594 parte 1a | sí |
| 1b lectura y forma de ruta | `spec_reader.py`, `spec_delivery.py`, `ports.py` | GH-594 parte 1b | sí, sin llamador |
| 2a supuestos y `spec_cause` | — | GH-594 parte 2a | no |
| 2b campos de supuestos en los esquemas de rol | — | GH-594 parte 2b | no |
| 3 revisión de Tech Writer sobre la Spec | — | GH-594 parte 3 | no |
| 4a parseo de la línea `Spec:` y ruteo puro | — | GH-594 parte 4a | no |
| 4b la ruta | — | GH-594 parte 4b | no |
| 4c eval compare y README | — | GH-594 parte 4c | no |
| 5a Spec borrador en la cadena de definición | — | GH-594 parte 5a | no |
| 5b reglas de aprobación | — | GH-594 parte 5b | no |
| 5c fallback y aprobación del dueño antes de construir | — | GH-594 parte 5c | no |

### Qué rige hoy, en palabras llanas

Hasta que salga GH-594 parte 4b, master no enruta por `Spec:`: el lector existe, pero nadie lo
llama. Hasta que salga GH-594 parte 5c, una feature se construye sin Spec, como hoy.

## Consecuencias

- La lectura usa `cat-file` por el sha del blob que da `ls-tree -l -z`, en vez de un
  `git show <commit>:<ruta>` literal, para que el modo y el tamaño chequeados sean los del
  objeto que se lee.
- El segundo chequeo, sobre los bytes ya cargados (`len(stdout) > SPEC_MAX_BYTES`), es defensa en
  profundidad.
- Cuando salgan GH-594 parte 4b y GH-594 parte 5c, la tabla y el párrafo «Qué rige hoy» dejan de
  ser ciertos y se actualizan en esa parte.
