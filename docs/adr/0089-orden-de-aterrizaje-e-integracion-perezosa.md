# ADR-0089: Orden de aterrizaje e integración perezosa

Estado: aceptado. Enmienda ADR-0048 (aterrizaje sin puerta humana); no edita ninguna de sus frases.

## Contexto

Con PRs aprobados en paralelo, cada merge a master mandaba a los demás por un `integrate` completo: merge del modelo, chequeos completos (15–25 min en apostrophe) y una revisión nueva. Medido el 2026-11-10/11: 20, 8, 7 y 6 pases en GH-407, 409, 403 y 406, la mayoría tirados porque master volvía a moverse antes del turno del PR. Merge queue, merge trains, Bors, Zuul y Graphite aprueban un diff una vez y prueban el merge con main una sola vez, justo antes de aterrizar.

## Decisión

### Orden y Cabeza

Un orden por producto (`domain/landing_order.head`, único lugar que ordena). Miembro: `pull_request` registrado, sin `closed_at` y PR abierto, sea cual sea el `status`. Precedencia de la Cabeza: nunca la tarea de una persona (`landing.waits_on_human`); entre el resto, la que tiene un pase de aterrizaje vivo; si no, el `landing_queued_at` más viejo, desempate por issue, sin sello al final. El sello lo escribe `deliver` (si falta, así una reentrega lo conserva) y `restore` (nuevo); solo el reinicio del aterrizaje lo borra. Ningún paso del aterrizaje lo reescribe.

### Integración perezosa

Un PR que espera recibe de `landing_step` solo `wait` o `none`, y el poller no lo pone en `found` ni en `lands` (tampoco como candidato `resume_owed`, `unstarted` o `stranded_now`). Su única lectura es el `merge-tree` (`git merge-tree --write-tree`, solo lectura): limpio, no hay `sync_master`, ni chequeos, ni revisión; con conflicto, nada hasta ser Cabeza; ilegible, nunca un pase de modelo (invariante 9). La Cabeza se evalúa una vez contra el master actual: `merge_ci` si el producto declara `ci:`, los chequeos del merge si no; un merge-tree limpio no pide `sync_master`. Si master se mueve en esa ventana, solo la Cabeza re-integra.

«Integrado exactamente una vez» se lee como: una vez por PR mientras es Cabeza contra el master que ve; un PR que se vuelve Cabeza con conflicto recibe exactamente un `sync_master`. Un master que se mueve de nuevo durante la ventana de la Cabeza es otra integración de la Cabeza, nunca de los demás.

### Tareas de una persona

`HUMAN_OWNED_REFUSALS` (`domain/landing.py`) es la única lista de negativas que dejan la tarea a una persona; `landing_step` y `waits_on_human` la leen. Una Cabeza escalada (o con `needs-human`, o parada) no bloquea: sale del orden y la segunda aterriza. Una tarea con un diagnóstico atascado (`stranded`) es del sistema, no de una persona: `land` la reintenta.

### Ancla del turno

El poller (segunda fase de `gather_merges`) es el único que escribe `head_since_at` y `head_since_head` por `Documents.record_closure`, cuando la Cabeza cambia o no tiene ancla. `with_ci` cuenta la espera de CI desde el mayor entre el ancla de hoy y `head_since_at` si `head_since_head` coincide con el head. Una escritura perdida por un `save` concurrente se vuelve a escribir la vuelta siguiente con un instante posterior: solo atrasa el ancla, es decir, cuenta menos espera; nunca adelanta un `diagnose`.

### Aterrizaje manual

`agentic land --issue N` sobre una tarea que no es la Cabeza corre igual y dice una vez `not the head of the landing order: run by the owner`. Calcula el orden con la misma función que el poller (`evaluate_landing_order`).

### Negativas y pasos

| Negativa | Cabeza | Espera |
| --- | --- | --- |
| las de `HUMAN_OWNED_REFUSALS` | `none` (y no es Cabeza) | `none` |
| `not_ready` | `none` | `none` |
| el resto | la tabla de ADR-0048 | `wait` |

### Flujo de ADR (GH-579/582/585)

Corre solo para la Cabeza: `adr_number_taken` da `renumber` o `escalate_adr`; un PR que espera con colisión espera; una Cabeza con `escalate_adr` pendiente de dueño sale del orden y no bloquea.

### Rendimiento

Las lecturas no crecen: una `assess_merge` y a lo sumo una lectura del issue por tarea entregada, lineales en los PR que esperan; los despachos son O(1) por producto.

### Límites conocidos

Merges fuera del orden (dependabot, el dueño, tramos aterrizados) por encima de `MERGE_ATTEMPTS_MAX` / `CI_STALE_PAIRS_MAX`; documentos en vuelo con `attempts == 3` reciben un `sync_master`; una ventana de CI por PR, en serie.
