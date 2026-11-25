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

Pendiente: lo completa la parte `ci-and-hold-record` con la ruta absoluta leída y el conteo
por clave.
