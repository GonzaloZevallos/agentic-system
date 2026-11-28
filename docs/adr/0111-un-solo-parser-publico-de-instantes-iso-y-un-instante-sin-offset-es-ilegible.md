---
status: accepted
date: 2026-11-25
---

# Un solo parser público de instantes ISO y un instante sin offset es ilegible

GH-1060. El dominio de proceso tenía cinco parsers privados de instantes ISO-8601
(`rules._instant`, `rate_limit._instant`, `merge_hold._instant`, `hold_record._instant` y
`ci._instant`) que no coincidían ante un texto sin offset: `hold_record` y `ci` lo leían como
UTC, `rules`, `rate_limit` y `merge_hold` lo rechazaban. Este ADR fija una sola lectura y un
solo parser público. No reemplaza a ningún ADR: es coherente con ADR-0048 y con ADR-0106, que
ya rechazan un instante sin zona.

## Decisión

- **Un parser público**: `agentic/core/process/domain/instant.py` expone
  `parse_instant(value: object) -> datetime | None`. Es un módulo hoja (solo importa
  `datetime`) y no lee ningún reloj.
- **Un instante sin offset es ilegible**: da `None`, nunca UTC. Tampoco son legibles un valor
  que no es texto, `""` ni un texto que `datetime.fromisoformat` rechaza. Adivinar una zona
  decidiría una pausa o una retención sobre una hora que nadie escribió.
- **La zona de origen se conserva**: un texto con `Z` o con offset vuelve tal como se parseó,
  sin `astimezone` ni `replace`. Quien necesite UTC lo convierte en su sitio
  (`rules.escalation_at`).

## Alcance

Los cinco parsers nombrados y nada más. Los tres que ya rechazaban (`rules`, `rate_limit`,
`merge_hold`) pasan a `parse_instant` sin cambiar ninguna decisión. `ci` y `hold_record`
cambian a propósito en la parte siguiente (GH-1060, `ci-and-hold-record`): ahí se registran
la tabla por sitio de llamada y la evidencia de los documentos guardados.

Fuera de alcance, para un issue de seguimiento:

- `tracker._instant`: devuelve texto y no parsea.
- `budget_store._instant`: lee segundos epoch, no ISO-8601.
- `merge._instant`: lanza una excepción y su llamador depende de ello.
- `poll._instant`: lee la hora sin zona como UTC y es de la capa de aplicación.
- Las llamadas sueltas a `fromisoformat` del dominio (`landing`, `stacking`, `speculation`,
  `exempt_queue`, `landing_order`, `board`, `returns`, `scratch`): conviven con la forma nueva
  hasta el seguimiento.

## Evidencia del escaneo de documentos guardados

Escaneo del 2026-11-25 de los documentos de tarea guardados (`*.md`, front matter YAML; cuenta
de valores de texto sin offset sobre el total de valores de la clave):

| Clave | `/home/zeta/dev/repos/agentic-system/.agentic/tasks` (525 archivos) | `/home/zeta/dev/repos/apostrophe/.agentic/tasks` (494 archivos) |
|---|---|---|
| `holds.start` | 0 de 0 | 0 de 0 |
| `holds.end` | 0 de 0 | 0 de 0 |
| `pushed_at` | 0 de 861 | 0 de 785 |
| `node_started_at` | 0 de 310 | 0 de 287 |
| `run_ended_at` | 0 de 305 | 0 de 272 |
| `dispatched_at` | 0 de 0 | 0 de 0 |

Ninguna raíz quedó sin leer. 13 y 32 archivos tienen un front matter que YAML no parsea; para
ellos se contó por texto plano (`pushed_at`, `node_started_at` y `run_ended_at` con un valor sin
offset): 0 en ambas raíces. Ningún documento guardado trae todavía un registro `holds` (el único
hallazgo de `holds:` y de `dispatched_at` en cada raíz es prosa), así que 0 tareas quedan
excluidas de las cifras de etapa por un valor `holds` sin offset. La decisión es rechazar en
todos los casos; la captura está en la evidencia de la tarea (`ci-and-hold-record-scan.txt`).
