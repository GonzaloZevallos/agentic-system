---
status: accepted
date: 2026-11-03
---

# Un producto con `ci:` corre su nivel completo solo en GitHub

GH-462. Desde que ADR-0064 (adenda GH-376) hizo que `full_checks` empuje la cabeza integrada
a `agentic-ci/<id>` y espere los jobs de `ci:`, el nivel completo volvía a correr en la máquina
del dueño en tres casos: `no_run` (la CI nunca arrancó; la espera sondea cada 30 s hasta
`check_timeout_s`, 1500 s), `untrusted` (la tarea toca `.github/`, así que una corrida de su propio
workflow no puede juzgarla) y `publish_failed`. En apostrophe, desde el 2026-10-28, ninguno de
los 30 `full_checks` corrió en CI (25 `no_run`, 5 `untrusted`): unas 32 h de espera a una CI que no
arrancó, seguidas de corridas locales de 1100-2660 s que agotan el tiempo cuando varias
comparten máquina. En agentic-system, 7 de 33 corrieron en local por `untrusted`.

El dueño decidió el 2026-11-03: ningún chequeo se omite antes del merge, sólo cambia dónde corre; para
un producto que declara `ci:`, la CI de GitHub es el único lugar donde corre la suite completa
(D-2ed419a0). En local el Developer corre sólo el nivel rápido y los tests afectados, la puerta de
entrega de siempre.

## Decisión

- **Sin retroceso local.** Un producto con `ci:` cuyo `full_checks` no obtiene veredicto de la CI
  (`no_run`, `publish_failed`, `not_started`, `gh_unavailable`, `gh_unreadable`) escala con la clase
  nueva `ci`. No es una devolución, no escribe registro y no construye `CheckoutPresubmit` ni corre
  un comando de chequeo local. La reanudación no necesita decisión porque `full_checks_due`
  compara cabezas, y `rules.auto_resumable` sigue siendo la única fuente de qué retoma el poller. La
  causa es un valor cerrado (`CiCause`), elegido por el paso que falló o la clase de salida, nunca
  leído de la salida de `gh` o de git (ADR-0029); el motivo para el humano puede citarla.
- **Gracia y un reintento.** `Configuration.ci_start_grace_s` (por defecto 180 s, menor que
  `check_timeout_s`): si pasada la gracia no hay ninguna corrida sobre la cabeza, el sistema borra y
  recrea la ref una vez, espera otra gracia y escala. Una CI que no arranca se detecta en unos 6,5
  min en vez de 25. Una corrida que sí arrancó se espera hasta `check_timeout_s`; una `gh` ilegible
  conserva la cota de hoy.
- **Cabeza no confiable por despacho.** La clave opcional `ci_dispatch: {workflow, sha_input}` del
  stack (sólo junto a `ci:`; referencia en `docs/reference/stack-yaml.md`) dice qué workflow de la rama
  base se despacha (`workflow_dispatch`) con la cabeza como entrada. Una cabeza que toca `.github/`
  se juzga por esa corrida del workflow de la base, nunca por la copia de la tarea. La corrida se
  identifica por su evento (`workflow_dispatch`), su archivo de workflow y el sha completo en su
  título; la cota inferior `since` es sólo un margen con una constante con nombre en `domain/ci.py`,
  de modo que ninguna comparación entre el reloj local y una marca de GitHub decide si una corrida
  existe. Si `gh` rechaza el despacho, la causa es `dispatch_refused` y la tarea escala al instante,
  sin espera ni reintento. Si una corrida completa omite o no tiene un job declarado, la causa es
  `jobs_missing` y escala: nunca es rojo ni tiempo agotado. El registro `full_checks` suma `ran_on:
  ci_dispatch` y `run_id`. Un producto sin la clave conserva la corrida local para cabezas no
  confiables, y el registro dice por qué.
- **Confianza en el merge.** `rules.ci_dispatched(record, pr_head)` es el único lugar que compara el sha
  de un registro despachado con la cabeza del pull request (con `same_commit`, sha completo);
  `rules.ci_trusted` es lo que leen la puerta de merge y el aterrizaje. Una cabeza que el aterrizaje
  mueve vuelve por `integrate` y `full_checks`, y la cabeza sincronizada recibe su propio registro.
- **Requisitos del workflow.** El workflow despachable declara `permissions: contents: read`, falla
  salvo que la entrada sea de 40 caracteres hexadecimales (el valor pasa por `env`, nunca en línea en
  `run:`), hace checkout del sha de la entrada y su `run-name` lo contiene en el despacho. El sistema
  nunca corre un workflow definido por la cabeza; despachar exige `actions: write` en el token del
  dueño.
- **Compatibilidad.** `FallbackCause` conserva sus seis valores para que los documentos viejos
  parseen; los registros nuevos sólo escriben `untrusted`. La deferencia de `ci_covers` (GH-453)
  sigue como está, sobre el camino local que queda.

## Rechazado

- Mantener el retroceso local y sólo acortar la espera: sigue corriendo la suite completa en la
  máquina del dueño, que es lo que el dueño decidió quitar.
- Despachar el workflow de la cabeza: ejecutaría código que la tarea controla con el token del dueño.
- Decidir si una corrida existe comparando `createdAt` con el reloj local: depende de dos relojes.

## Residuos aceptados

1. **CI vieja al mergear.** Si master se mueve entre `full_checks` y el merge, la CI del pull request
   queda vieja y la puerta de merge corre la suite local salvo que los chequeos registrados cubran el
   master actual (con la regla de ganancia inerte). Se registra como hallazgo `outside` en el issue de
   seguimiento de esta tarea: llevar un merge con CI vieja a una sincronización más una corrida de
   GitHub, con una cota sobre cuánto perseguir a master.
2. **Sin `delivery_checks`.** Un producto con `ci:` y sin `delivery_checks` no tiene nodo `full_checks`,
   así que sus cabezas no confiables siguen locales en el merge.
3. **La cabeza de esta tarea.** Su base no tiene la clave, así que toma el camino local que queda para
   cabezas no confiables. La primera corrida despachada real queda probada por el primer pull request
   verde que la use.

## Adenda GH-463: la puerta de merge pide una corrida fresca a GitHub

Para un producto que declara `ci:`, la puerta de merge ya no corre la suite en la máquina del dueño.
Donde antes devolvía `run_checks`, decide `ci_run`: una corrida fresca de GitHub sobre la cabeza
fusionada con el master actual, cuyo veredicto decide. Antes, #353 sigue mandando: si nada de lo que
la tarea toca cambió en master, no se vuelve a chequear.

**Mecanismo.** La puerta (`merge_task` en `agentic/core/process/application/merge.py`) arma `cabeza + master` sólo con git, escribe el par en el registro
`merge_ci` del documento (`MergeCiRecord` en `agentic/core/process/domain/task.py`) antes de disparar nada y lo publica. Una cabeza confiable va a
`agentic-ci/<id>` y se lee con `Tracker.branch_ci` (`agentic/core/process/application/ports.py`); una no confiable va a `refs/agentic-ci-dispatch/<id>`
(fuera de `refs/heads/`, no dispara `push`) y se despacha el workflow de la base con el sha probado.
Se rechazaron: actualizar la rama (mueve `pr_head` y rompe `head_reviewed`), repetir la corrida del
pull request (conserva un `GITHUB_SHA` viejo) y cerrar y reabrir el pull request (efectos laterales).

**Cotas.** Dos constantes en `domain/ci.py`, contadas por cabeza: `CI_STALE_PAIRS_MAX = 3` (master
adelantó una corrida verde con una ganancia no inerte) y `CI_NO_VERDICT_PAIRS_MAX = 1` (GitHub no dio
veredicto tras el único republicado o segundo despacho).

**Rechazos y pasos del aterrizaje.**

| Rechazo | Paso |
|---|---|
| `ci_stale_exhausted` | `sync_master` (sin conflicto) |
| `ci_no_verdict` | `escalate`, con `{refusal, head, upstream, cause}` |
| `ci_untrusted_no_dispatch` | `none`: queda para el humano de inmediato |

**Liberación de `ci_no_verdict`.** Una sola vez por par (cabeza, upstream): cuando el upstream es
otro que el de la escalación, o cuando el dueño comenta. Un base verde por sí solo nunca la libera. La
liberación estampa `released_at` en el par de `merge_ci` (`landing_plan.release` en `agentic/core/process/application/landing_plan.py`; la regla pura es `landing.ci_no_verdict_entry` en `agentic/core/process/domain/landing.py`), lo que concede un par más sin veredicto, y el
paso que da es `wait`: la puerta decide de nuevo en la ronda siguiente. Una corrida fresca roja se
reintenta con `Tracker.rerun_ci_run(run_id)` (`agentic/core/process/adapters/github/tracker.py`, llamado por `_rerun_fresh` en `agentic/core/process/application/landing_fix.py`) una vez por par, nunca con los runs del pull request; sin
`run_id` no se reintenta y la puerta republica dentro del par. El aterrizaje ve esa corrida roja por el par
registrado (`poll.with_recorded_red` y `merge_ci.decided_red`, en `agentic/core/process/application/poll.py` y `agentic/core/process/domain/merge_ci.py`), sin leer GitHub; si
master ya la superó, el par ya no es el vigente y no hay nada que reintentar. Al reintentar, `_rerun_fresh`
deja el par sin decidir (`merge_ci.reopened` borra `outcome` y `failing` y estampa `rerun_at`, desde donde
cuenta la espera), de modo que la ronda siguiente envía al hijo de merge, que lee la corrida de nuevo: verde
mergea, pendiente espera y un segundo rojo es un `fix`.

**Cabezas no confiables.**

| Cabeza | Pila | Resultado |
|---|---|---|
| no confiable, fresca | declara `ci_dispatch` y hay un registro `ci_dispatch` vigente | mergea, como hoy |
| no confiable, vieja | declara `ci_dispatch` | una corrida fresca despachada |
| no confiable | sin `ci_dispatch` | `ci_untrusted_no_dispatch`, para el humano. **Cambio de comportamiento:** la suite local ya no corre |

**Residuo 1, ahora acotado.** La CI vieja al mergear ya no cae en la suite local: persigue a master
hasta `CI_STALE_PAIRS_MAX` pares y luego sincroniza.

**Hecho abierto.** Si `actions/checkout` puede traer un sha alcanzable sólo desde una ref que no es
rama es algo que sólo GitHub produce. Si no puede, el resultado es `ci_no_verdict`, nunca un push de
la cabeza no confiable a `refs/heads/`.

## Adenda GH-465: el empuje especulativo

Ninguna regla de arriba cambia: ningún chequeo se saltea y el veredicto sigue siendo el de
`ci.full_tier_verdict` sobre el sha. Cambia cuándo arranca el CI.

- **Quién empuja.** La revisión de la unidad final (`integration`, o la subtarea única) publica su
  cabeza en `agentic-ci/<id>` antes de lanzar al Tech Lead, cuando `speculation.push_due` lo dice:
  el producto declara `ci:` y `delivery_checks`, la cabeza es confiable (`ci_untrusted` es `False`),
  no hay pull request ni escalado, y no hay un `full_checks` pasado sobre esa cabeza. Un producto con
  `ci_dispatch` especula igual cuando su cabeza es confiable. Un `GitError` al empujar no falla ni
  reintenta la revisión: no se escribe registro.
- **Qué guarda el estado.** `ci_speculation: {head, pushed_at}`, el hecho del push y nunca un
  veredicto. Sólo lo escribe esa revisión: la aprobación con un sha de 40 hex, la devolución con
  `None`, que descarta la corrida. `full_checks`, `deliver` y el aterrizaje no lo tocan.
- **Reutilizar sólo por el mismo sha, no por árbol.** El CI juzga un commit; una corrida de GitHub no
  tiene la firma ni la huella de entorno de ADR-0065. Reutilizar por árbol obligaría a cambiar
  `full_checks_due` y `approved_head` y a que el registro lleve dos shas. Un árbol igual con otro sha
  corre como hoy. Un commit propio posterior a la revisión (`owner_rules`, `fix_important`,
  `close_docs`) mueve el sha y la tarea cae al camino de siempre.
- **Consumo.** Con `speculation.consumable`, `full_checks` no publica: lee la ref una vez y espera.
  Si `speculation.event_lost` dice que GitHub nunca arrancó una corrida (ninguna visible pasado el
  período de gracia desde `pushed_at`), republica de inmediato. Si la espera sobre la corrida
  especulada termina en `no_run` o `not_started` (cancelada o ausente, por ejemplo porque un push
  más nuevo la pisó), no es un veredicto ni un escalado: republica y espera como hoy, y sólo esa
  segunda espera puede escalar con clase `ci`. El registro gana `speculative: true`; `ran_on` sigue
  siendo `ci`.
- **Costo.** A lo sumo un push por pase de la unidad final antes del pull request: 1 + el máximo de
  devoluciones por par (`max_changes_requested_per_pair`) por cada decisión humana que reinicia el
  par, más uno por el `pre_pr_fix`. Este repo no cancela corridas viejas (`cancel-in-progress`), así
  que cada pase devuelto paga una corrida completa de CI. Agregarlo al `ci.yml` queda como
  seguimiento `outside`: tocar `.github/` en esta tarea volvería no confiable su propia cabeza.
- **Fuera de alcance.** El rojo especulativo no llega al Tech Lead como evidencia: lo recoge
  `full_checks` con sus filas por chequeo.
- **Medir los aciertos** (sobre los documentos de tarea, sin span ni etiqueta nuevos): acierto es
  `ci_speculation.head == full_checks.head` y `full_checks.speculative`; empuje perdido es
  `ci_speculation` presente con otro `full_checks.head`.
