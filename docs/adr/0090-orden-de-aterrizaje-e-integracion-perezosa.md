# ADR-0090: Orden de aterrizaje e integración perezosa

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

`HUMAN_OWNED_REFUSALS` (`domain/landing.py`) es la única lista de negativas que dejan la tarea a una persona; `landing_step` y `waits_on_human` la leen. Una Cabeza escalada (o con `needs-human`, o parada) no bloquea: sale del orden y la segunda aterriza. Un diagnóstico atascado (`stranded`, GH-257: nadie fue avisado) es del sistema: `land` lo libera y lo reintenta, así que `waits_on_human` lee su documento sin `escalated`, igual que el de una escalación que el dueño ya liberó (el documento como lo dejaría `landing_plan.release_patch`). Ambos entran por el documento que lee la misma función; el poller no repite su definición.

### Ancla del turno

El poller (segunda fase de `gather_merges`) es el único que escribe `head_since_at` y `head_since_head` por `Documents.record_closure`, cuando la Cabeza cambia o no tiene ancla. `with_ci` cuenta la espera de CI desde el mayor entre el ancla de hoy y `head_since_at` si `head_since_head` coincide con el head. Una escritura perdida por un `save` concurrente se vuelve a escribir la vuelta siguiente con un instante posterior: solo atrasa el ancla, es decir, cuenta menos espera; nunca adelanta un `diagnose`.

### Aterrizaje manual

`agentic land --issue N` sobre una tarea que no es la Cabeza corre igual y dice una vez `not the head of the landing order: run by the owner`. Calcula el orden con la misma función que el poller (`evaluate_landing_order`).

### Negativas y pasos

Cada negativa de `MergeRefusal`, con el paso de la Cabeza (`landing_step` con `turn="head"`) y el de quien espera (`turn="waiting"`). Quien espera nunca llega a un presupuesto, un pase, un tramo ni un reinicio.

| Negativa | Cabeza | Espera |
| --- | --- | --- |
| `pr_not_open` | `none` (de una persona; no es Cabeza) | `none` |
| `eval_set` | `none` (de una persona; no es Cabeza) | `none` |
| `perf_unverified` | `none` (de una persona; no es Cabeza) | `none` |
| `criteria_over_cap` | `none` (de una persona; no es Cabeza) | `none` |
| `criteria_unreadable` | `none` (de una persona; no es Cabeza) | `none` |
| `ci_untrusted_no_dispatch` | `none` (de una persona; no es Cabeza) | `none` |
| `not_ready` | `none` (aún no está en el aterrizaje) | `none` |
| `adr_number_taken` | `renumber`, `apply_adr_decision`, `escalate_adr` o `none` | `wait` |
| `not_approved`, `head_not_reviewed` | `re_review` (o `diagnose` agotado el presupuesto) | `wait` |
| `open_findings_blocking` | `fix`, `settle` o `escalate_ledger` | `wait` |
| `open_findings` | `fix_important` o `file_followups_and_land` | `wait` |
| `eval_set_unreadable`, `ci_missing`, `ci_unreadable` | `wait`; agotada la ventana, `diagnose` o `land_slice` | `wait` |
| `ci_blocked_by_conflict`, `ci_stale_exhausted`, `attempts_exhausted`, `merge_conflict` | `sync_master` (o `diagnose` agotado el presupuesto) | `wait` |
| `ci_no_verdict` | `escalate` (o `wait` si ya se liberó) | `wait` |
| `ci_unknown_names` | `escalate` una vez por (head, nombres), luego `none` | `wait` |
| `ci_failed`, `checks_failed` | `rerun`, `sync_master`, `fix`, `escalate` o `diagnose` | `wait` |
| `merge_failed` | `diagnose` o `land_slice` | `wait` |
| `master_moved` | `wait` | `wait` |

### Flujo de ADR (GH-579/582/585)

Corre solo para la Cabeza: `adr_number_taken` da `renumber` o `escalate_adr`; un PR que espera con colisión espera; una Cabeza con `escalate_adr` pendiente de dueño sale del orden y no bloquea.

### Rendimiento

Las lecturas no crecen salvo en una tarea escalada: una `assess_merge` y una lectura del issue por tarea entregada (más los comentarios de un diagnóstico atascado y, en una tarea escalada, la lectura de la liberación, que la Cabeza repite en su paso), lineales en los PR que esperan; los despachos son O(1) por producto.

### Límites conocidos

Merges fuera del orden (dependabot, el dueño, tramos aterrizados) por encima de `MERGE_ATTEMPTS_MAX` / `CI_STALE_PAIRS_MAX`; documentos en vuelo con `attempts == 3` reciben un `sync_master`; una ventana de CI por PR, en serie.

## Adenda GH-621: presupuesto de la Cabeza

Una Cabeza que no avanza no retiene el orden. Cada miembro lleva el tiempo que fue Cabeza (`head_charged_s` más el tramo desde `head_seen_at`, acotado por `GAP_MAX` = 15 min, para no cobrar un poller parado) y los pasos sin resolver sobre su sha. Al llegar a `HEAD_BUDGET` (45 min) o `HEAD_STEPS_MAX` (2) cede: pasa detrás de todo el que no cedió y su cuenta vuelve a cero. Niveles, el primero que coincide gana: (1) quien conserva el turno (ocupado y visto como Cabeza dentro de `GAP_MAX`); (2) pase vivo entre los que no cedieron; (3) `landing_queued_at` más viejo entre ellos; (4) los que cedieron, menos cesiones primero. Una Cabeza ocupada (hijo vivo) o con solo el merge pendiente no cede: la acotan el vigía de estancamiento (ADR-0057) y el tope por intento.

**Tiempo observado.** Solo cuenta lo que el poller vio. `landing_order.resumed` (una vez por evaluación, antes de `advance`) devuelve el turno a la Cabeza ocupada que quedó sin observar por una ronda retenida o un poller parado, sin cobrarle ese hueco. `landing_order.turn_patch` decide qué claves `head_*` se escriben (la escritura de `head_seen_at` se regula a cada `HEAD_SEEN_EVERY`), y `_record_turn` en `poll.py` es el único escritor, con `record_closure`; el reinicio del aterrizaje solo las borra. Una ronda retenida no observa nada. Cada cesión agrega `{at, head, reason}` a `head_yields` (las últimas 10, idempotente por turno) y se dice en una sola línea, solo tras escribir.

**Lectura de aceptación.** El segundo PR llega a ser Cabeza dentro de un `HEAD_BUDGET` de tiempo observado de la Cabeza, más su propio aterrizaje.

**Límites.** El límite de pasos no ve ciclos que cambian el sha: el freno es el presupuesto de tiempo. La rotación está acotada en el orden, no en el gasto; lo que frena el gasto son los presupuestos de aterrizaje de cada tarea. Una Cabeza libre paga a lo sumo `GAP_MAX` por retención. Un `save` completo de un hijo vivo puede pisar una escritura del poller con un estado anterior: solo cobra de menos.
