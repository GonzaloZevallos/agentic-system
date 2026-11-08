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

## Adenda GH-487: los pushes del camino del CI se reintentan según lo que git dijo

Un push de `agentic-ci/<id>` que falla una vez ya no escala a la primera: GH-475 escaló con
`publish_failed` por un fallo de red que diez minutos después ya no existía, y no dejó escrito lo
que git había dicho.

- **La regla.** Cada paso de git del camino del CI (`publish_ci_ref`, `delete_ci_ref` y
  `publish_dispatch_ref`) se reintenta solo, nunca en secuencia: un `republish` que ya borró el ref
  no repite el borrado porque la creación falló. Todo push o borrado pasa por
  `application/ci_push.publish_with_retry` (o por `republish`, que lo usa paso por paso); ningún
  otro código escribe un bucle de reintento ni un número de intentos o segundos. Un `--delete` de
  un ref que el remoto no tiene cuenta como hecho.
- **Clasificación.** `domain/ci_push.classify` lee el stderr de git y devuelve `permanent`
  (permiso o scope que falta, ref protegido, hook que rechaza, no fast-forward, autenticación),
  `transient` (red, DNS, TLS, 5xx, el remoto cortó) o `unknown` (todo lo demás, también un error sin
  stderr). `permanent` gana sobre `transient`. Intentos: `PUSH_ATTEMPTS = 3` para `transient`,
  `PUSH_UNKNOWN_ATTEMPTS = 2` para `unknown` y uno solo para `permanent`, que nunca se reintenta.
  La espera antes del 2.º y del 3.º intento es `PUSH_BACKOFF_S = (2.0, 8.0)`. Los números viven sólo
  en `domain/ci_push.py`.
- **Lo que git dijo.** Cuando los intentos se agotan, `PushFailed` (subclase de `GitError`) lleva el
  stderr ya pasado por `domain/ci_push.scrub`: sin credenciales ni backticks, en una línea y con a
  lo sumo `GIT_SAID_LIMIT = 400` caracteres. Ese texto va al escalado y a `## Full checks`, y a los
  logs; nunca a un atributo de span, una etiqueta ni una métrica (ADR-0008), y la causa cerrada
  sigue siendo `publish_failed` (ADR-0029).
- **Push especulativo.** La revisión reintenta con la misma regla. Si aun así falla no escala ni
  escribe `ci_speculation` ni nada en el documento: deja una sola línea en el log
  (`speculative push failed after <n> attempt(s) (<kind>): <said>`) y `full_checks` publica como
  siempre. Una corrida especulativa consumida no anota intentos: el registro de `full_checks` sólo
  los anota cuando fue él quien empujó.
- **Merge.** La corrida fresca de la puerta (GH-463) reintenta igual, en modo push y en modo
  dispatch, y un push que no se cura sigue terminando en `MergeResult("stopped",
  "ci_publish_failed")`. Merge no gana escalado ni `reason`: el log agrega los intentos, el `kind` y
  el texto limpio.
- **Fuera de alcance.** `tracker.dispatch_ci` es `gh`, no un push, y no se reintenta aquí.
- **Peor caso de espera.** Unos 10 s por paso (2 s + 8 s), síncronos dentro de
  `anyio.to_thread.run_sync`. Es aceptable ante `agentic stop` porque el desenrollado no deja estado
  a medias: la marca se escribe antes de la señal, el push sólo toca el ref de la propia tarea y la
  reanudación vuelve a entrar al nodo sobre la misma cabeza.

## Adenda GH-492: el conflicto que git encuentra en el par fresco es un hecho

**Contexto.** En GH-475 (PR #490) la puerta de merge construía la cabeza fusionada con master, git
encontraba un conflicto y el verbo rechazaba `merge_conflict` sin escribir nada. GitHub seguía
diciendo `UNKNOWN` más de una hora, así que `conflicts` quedaba en `None`, la decisión llegaba a
`ci_run`, `land` respondía `skipped: decision_ci_run` y el poller volvía a lanzar `merge` cada
~90 s: nadie resolvía el conflicto.

**Decisión.**

- **Se registra.** El conflicto que git encuentra al construir el par fresco (`merge._build_pair`,
  sólo si el merge dejó rutas sin resolver: `MergeConflict`) se guarda como un hecho de ese
  `(cabeza, master)` exacto: `merge_conflict: {head, master, at}`, una sola fila que cada escritura
  reemplaza, por el camino de patch y reductor, nunca reescribiendo el documento. Un fallo del
  merge sin rutas sin resolver (`MergeFailed`) no es un conflicto: no se registra y la ronda se
  detiene con `ci_publish_failed`.
- **Gana sobre GitHub.** `rules.merge_conflicts_from` lee, en orden, el `CONFLICTING` de GitHub sobre
  esta cabeza y esta base, el conflicto registrado y la sonda. El registrado vale `True` sobre
  `MERGEABLE` y sobre `UNKNOWN`. `UNKNOWN` es no leído, nunca limpio: con git limpio y sin registro,
  sigue siendo `ci_run`.
- **Sólo para su par.** Vale sólo para el par exacto, comparado con `same_commit` y nunca por texto
  (`domain/merge_conflict.applies`): si se mueve la cabeza o el master, se ignora.
- **Dónde se aplica.** En la lectura base de `conflicts`: `merge.decide()` y la primera `facts` de
  `poll.assess_merge`, así un par `unlandable` también rechaza `merge_conflict`.
- **El bucle termina sin contador.** En la ronda siguiente, `assess_merge` rechaza `merge_conflict`:
  el poller sigue el camino del aterrizaje y deja de lanzar `merge`, y `land` entra al mismo paso de
  conflicto, con el mismo presupuesto, que cuando GitHub reporta `CONFLICTING`. La regla de GH-255
  (un rechazo no escribe nada) vale para todo otro rechazo.
- **Fuera de alcance.** `integrate.py` y la fusión de `run_checks` siguen tratando todo fallo de
  `Workspace.merge` como conflicto: es la misma clase de defecto y queda como seguimiento.

## Adenda GH-504: se publica la cabeza antes de despacharla y un checkout caído no es una suite roja

**Contexto.** En GH-479 las tres corridas despachadas (GH-462) fallaron en `actions/checkout` con
`fatal: remote error: upload-pack: not our ref <sha>`: el sistema despachaba el `ci.yml` de master con
`sha=<cabeza integrada>`, pero esa cabeza no estaba en ningún ref de GitHub. El nivel completo lo leyó
como un `check` rojo y lo devolvió dos veces al Developer.

**Decisión.**

- **Se publica antes de despachar.** `full_checks._dispatched_full_checks` llama a
  `Workspace.publish_dispatch_ref(<checkout de la tarea>, <id>)` antes de cada `dispatch_ci`, en cada
  intento: el mismo orden que el camino de merge (GH-463). La ref es `rules.ci_dispatch_ref(<id>)`,
  `refs/agentic-ci-dispatch/<id>`, y nunca va bajo `refs/heads/`: un ref `agentic-ci/**` dispara el
  evento `push` y correría el `ci.yml` de la propia cabeza no confiable, justo lo que el despacho evita.
  Esto reemplaza la sugerencia del issue de usar `agentic-ci/<id>`.
- **Si la publicación falla** (`GitError`) no se despacha nada: `publish_failed`, escalado con clase
  `ci`, sin devolución. El mensaje nombra la ref de despacho y el sha, no el archivo del workflow.
- **`full_checks` nunca borra esa ref**, ni al leer el veredicto. Sólo la borra el barrido
  (`ci_ref_sweep`).
- **`checkout_failed`.** Valor nuevo de `CiCause`. El adaptador `gh` clasifica el primer paso fallido de
  cada job con `ci.failed_stage`: `Set up job`, exacto, es `setup`; un nombre que empieza con
  `Run actions/checkout@` es `checkout`; todo lo demás (incluido `Post Run actions/checkout@…`, un paso
  que sólo menciona checkout o pasos ilegibles) es `suite`, es decir, rojo. `ci.dispatch_verdict` da
  `unreachable/checkout_failed` cuando todos los jobs declarados que fallaron lo hicieron en `setup` o
  `checkout`, junto a la regla de `not_started` y antes de `red`. Es la única fuente de la causa: ningún
  otro módulo la asigna ni la infiere de texto. Escala con clase `ci`, sin fila de devolución y sin
  contar contra el par; el mensaje (`_DISPATCH_DETAIL`, que ya no hereda de `_CI_DETAIL`) nombra la
  etapa, la ref de despacho y el sha, todos datos del sistema.

**Límites conocidos.**

1. **Supuesto no probado contra GitHub real.** El diseño supone que `actions/checkout` con `ref: <sha>`
   alcanza un commit que sólo está en `refs/agentic-ci-dispatch/`; el camino de merge (GH-463) ya
   depende de lo mismo. Si es falso, el resultado es un `checkout_failed` explicado que escala con clase
   `ci` y nunca se le cobra al Developer.
2. **Nombre del paso de checkout.** Un paso de checkout con un `name:` propio sigue leyéndose rojo.
3. **Ref compartida con el par del merge.** Si un par de merge de la misma cabeza todavía no pasó su
   checkout y un `full_checks` vuelve a publicar en ese intervalo, el par se lee `no_verdict`, la puerta
   rechaza `ci_no_verdict` y el aterrizaje escala una vez con clase `ci`; el humano lo libera
   (`released_no_verdict`). Se acepta sin guarda: retener la publicación dejaría a `full_checks`
   esperando una corrida cuyo veredicto quizá no decide nada.

## Adenda GH-531: una cabeza empujada sin corrida se juzga con el workflow despachado

Una rama anterior al cambio que agregó `agentic-ci/**` a los disparadores lleva su propio `ci.yml`
viejo: GitHub usa el de la cabeza empujada y no arranca ninguna corrida. El nodo terminaba en `no_run`
y escalaba con clase `ci`, y reanudar repetía lo mismo con la misma cabeza.

- **El respaldo.** Cuando el camino del push termina en `no_run` (después del republish, tanto en el
  camino directo como en el especulativo) y el upstream declara `ci_dispatch`, la misma cabeza se juzga
  con `_dispatched_full_checks`. La decisión la toma sólo `ci.dispatch_after_no_run`. No hay clase ni
  causa de escalado nuevas.
- **Por qué esperar el `no_run`.** Se podría leer el YAML de la rama para saber si dispara, pero eso
  duplicaría la lógica de GitHub. Esperar el `no_run` ya acotado por dos períodos de gracia es el
  único juicio que no puede equivocarse. Costo: hasta ≈3 min más y un `git fetch`, sólo para cabezas
  sin corrida.
- **Lectura en el upstream, excepción a la adenda del ADR-0016.** El stack se lee en la base de la
  tarea, pero el workflow que `dispatch_ci(workflow, "", …)` corre es el de la rama por defecto. Por eso
  `_fallback_dispatch` lee `ci_dispatch` y `ci` en una sola revisión, el sha del upstream traído, y la
  corrida despachada se juzga con esos nombres. El camino de cabeza no confiable (GH-462) y
  `full_checks_mode` siguen leyendo en la base.
- **El registro.** Lleva `ran_on: ci_dispatch`, `run_id`, `dispatch_cause: "no_run"` y `stack_at` (el sha
  completo del upstream). Nada decide con esos dos campos: `rules.ci_untrusted` sigue derivándose de las
  rutas. La puerta de merge no cambia: `_full_checks_stands` ya acepta `ci_dispatch` si las filas cubren
  los nombres que la puerta pide.
- **Cuando no puede correr.** Se escala `ci`/`no_run` como hoy, con un sufijo de un conjunto cerrado:
  `no_upstream`, `stack_unreadable` o, sin sufijo y con el texto idéntico al de hoy, `undeclared`
  (más «(the upstream was not fetched)» si el fetch falló). Nunca entra texto de git.
- **Supuesto.** El upstream del checkout del producto es la rama por defecto, como en el poller de
  apostrophe sobre `master`.

## Adenda GH-539: una corrida cancelada no es un rojo

Un `ci-ok` con `needs:` falla cuando los jobs de los que depende se cancelan, y
`cancel-in-progress` de apostrophe cancela la corrida vieja cuando llega un empuje nuevo a
`agentic-ci/<id>`. Eso no es una suite roja y no gasta una devolución.

- **Regla a/b, antes de `_NOT_STARTED`.** Una única función pura de `domain/ci.py`
  (`superseded_or_cancelled`) decide si un check rojo es de una corrida superada o cancelada:
  (a) su corrida tiene un check `CANCELLED` y existe otra corrida de la misma cabeza, con run id
  mayor y el mismo nombre declarado; o (b) el run id está en `CiReport.cancelled_runs`, la suite
  que concluyó `CANCELLED`. Esa clasificación corre antes de `_NOT_STARTED`, que queda para
  `STARTUP_FAILURE`, un `CANCELLED` de una corrida que no es superada ni cancelada, y lo que
  sobrevive al reintento.
- **Reintento único por cabeza.** `full_checks` consume `FullTierVerdict.state == "cancelled"`
  con un `match` exhaustivo. Sin hecho `ci_cancel_rerun` para esa cabeza llama a `rerun_ci_run`,
  escribe `{head, run_id, rerun_at}` (un solo hecho, no una lista, que no cuenta para el par) y
  vuelve a esperar. Un segundo `cancelled` escala `ci`/`not_started` solo si después del reintento
  se vio la suite fuera de `COMPLETED` o venció `ci_start_grace_s` desde `rerun_at`
  (`speculation.second_cancel`); antes, espera. Si `rerun_ci_run` rechaza, escala. Nunca hay una
  devolución, un hallazgo ni un registro `full_checks` fallido. En el empuje especulativo un
  `cancelled` no se convierte en republish: otro empuje volvería a cancelar la corrida.
- **Puerta de merge.** `_fresh` mapea `cancelled` sobre el reintento por par: sin `rerun_at`,
  `rerun_ci_run` y `stopped ci_wait`; con `rerun_at`, espera hasta `ci_start_grace_s` y después es
  `no_verdict` con causa `not_started`. Nunca `outcome: red`.
- **Cambio deliberado.** Una corrida con un rojo real y hermanos cancelados por `fail-fast`, seguida
  de otra más nueva del mismo sha, se juzga por la nueva: con datos estructurados no se separa ese
  caso de una cancelación por concurrencia. Una corrida roja sin ningún `CANCELLED` sigue roja.
- **`red_on_base`/`ci_escalation`.** Una base cuya corrida se canceló ya no cuenta como roja; lee
  `pending`, igual que `landing_slice`, `ci_review` y `poll`.
- **Límite conocido.** Si la suite no concluye `CANCELLED` y la corrida cancelada no tiene sucesora
  con el mismo nombre declarado, sigue roja como hoy. Tampoco se midió con qué conclusión terminó la
  suite del incidente de GH-548.
- **Seguimiento fuera de esta tarea.** Es probable que el sistema cancele sus propias corridas:
  `has_run` en `_speculated_wait` y `no_run` en `full_tier_verdict` cuentan solo los nombres
  declarados, y un `ci-ok` con `needs:` no tiene check-run mientras corren sus dependencias, así que
  se republica la misma cabeza y `cancel-in-progress` cancela la corrida en vuelo. Es una inferencia
  del código, sin log que la confirme.

## Adenda (GH-547, 2026-11-07): la puerta de merge juzga lo que el merge trae

Una tarea larga que integró master después de empezar se rechazaba con `ci_untrusted_no_dispatch`
aunque su rama no tocara el CI: la puerta leía los caminos de `task_base...head`, que incluyen los
commits propios de master (entre ellos `.github/`), y `ci_dispatch` en un `task_base` anterior a la
declaración (apostrophe GH-396, PR #616).

- **Rango.** Los caminos tocados son `merge-base(head, master)...head`. El merge base se lee una
  sola vez por llamada del verbo y es a la vez el inicio de ese rango y la base de la lectura de
  `gain`; no hay segunda lectura (tampoco la de `criteria`). Un merge base ilegible detiene el merge
  como `diff_unreadable`; nunca vuelve a `task_base`.
- **`ci_dispatch` se lee en master.** Es una excepción nueva a la adenda de ADR-0016, con la razón
  de GH-531: el dispatch corre el workflow de la rama por defecto. Los nombres `ci:`, los checks y
  el `setup` siguen leyéndose en `task_base`. El `stack.yaml` de la cabeza nunca aporta `ci_dispatch`
  ni `ci` a una decisión de confianza.
- **Lectores de `touched`.** `ci_untrusted` mira solo lo que la rama cambia; `touches_adrs` decide
  la sonda de colisión de ADR sobre lo mismo; `masters_covered` ya no cuenta como de la tarea un
  cambio de `.agentic/stack.yaml` que vino de master; la lista `guarded` del intent ya no muestra
  los caminos de `.github/` y `.agentic/` que trajo master.
- **Tarea corta.** Si `merge-base(head, master) == task_base`, el rango y la decisión son los de
  antes.
- **Límite conocido.** Los nombres `ci:` se leen en `task_base` y el workflow que corre es el de
  master: si master renombra un job después de empezar la tarea, la tarea larga se rechaza con
  `ci_unknown_names` (fijado por un test).
- **Fuera de esta tarea.** `full_checks._head_trust`, `full_checks_mode` y la especulación siguen
  leyendo en `task_base`; tras un `sync_master` del aterrizaje pueden mostrar el mismo síntoma.

## Adenda (GH-552): un par `no_verdict` del master actual se relee una vez

En apostrophe GH-396 el par de `merge_ci` terminó `no_verdict` (`no_run`) dentro de la gracia, pero
su corrida terminó verde unos minutos después, y nadie volvió a leerla: la puerta rechazó
`ci_no_verdict` con un verde disponible.

- **Una relectura por par.** Antes de rechazar `ci_no_verdict`, el último par de la cabeza que
  terminó `no_verdict` sobre el master actual (`merge_ci.rereadable`: ids completos, sin
  `reread_at` ni `released_at`, la corrida pertenece a la cabeza, mismo master, sin ganancia inerte)
  hace que la decisión sea `ci_run`. La puerta escribe `reread_at` en el par **antes** de llamar a
  GitHub, así que un fallo o una caída no reabren el bucle, y lee la corrida del par una vez.
- **Verde o rojo decide.** Un veredicto verde o rojo reescribe el par desde el ya marcado (`reread_at`
  se conserva): un verde queda `green` y se mergea, sin `released_at` (es un hecho del aterrizaje); un
  rojo es el rechazo de siempre. Cualquier otro estado no escribe nada más ni republica, ni vuelve a
  correr, ni empuja ni despacha: `ci_no_verdict` sigue siendo el rechazo.
- **Quién la dispara.** El par que la propia llamada acaba de cerrar `no_verdict` se rechaza en esa
  misma llamada, como siempre; su relectura la hace una llamada posterior, que el poller lanza porque
  la decisión sobre el documento (también el de una tarea escalada) es `ci_run`.
- **Costo.** A lo sumo una llamada más a `gh` por par `no_verdict`; sin gasto de modelo.

## Adenda (GH-565): una sola regla de confianza para el nodo, el modo, la especulación y el merge

El nodo `full_checks` juzgaba la confianza desde `task_base`: una tarea larga que había integrado los
cambios de `.github/` de master corría su suite local como `untrusted` (apostrophe GH-407 y GH-396),
y su web-e2e local fallaba bajo carga. GH-547 lo había arreglado solo en el merge.

- **Una sola regla.** `application/ci_trust.py` es el único lugar que decide si el CI del producto
  puede juzgar una cabeza. `read_range` lee `merge-base(cabeza, master)...cabeza` y el `ci_dispatch`
  de master; `read` es la lectura perezosa que usan el nodo, `full_checks_mode` y
  `review._push_speculatively`. El merge llama a `read_range` con el master de su verbo, así que ya
  no pueden divergir. `rules.py` no recibe nada nuevo.
- **Perezosa, solo de no confiable a confiable.** `read` pregunta primero `task_base...cabeza`: una
  cabeza que esa diferencia confía no cuesta `upstream()` ni `merge_base`. Solo una cabeza que
  esa diferencia desconfía resuelve el master (a lo sumo un fetch, acotado por `FETCH_TIMEOUT_S`) y
  relee el rango. Sin master real (`upstream` en `HEAD` o sin sha) o con un merge base ilegible, el
  veredicto nunca es mejor que el de `task_base`.
- **`ci_dispatch` se lee en master**, porque el despacho corre el workflow de la rama por defecto
  (como en GH-547). Un `ValueError` es "sin despacho", nunca una excepción. Una cabeza que la lectura
  de `task_base` ya confiaba lee el despacho en `task_base`, como siempre.
- **La línea `CI not used (untrusted)`** aparece menos veces. Lleva `(the upstream was not fetched)`
  solo cuando el master no se pudo resolver o traer, nunca cuando falló la lectura del merge base.
- **Desacuerdo posible.** En el caso de un revert (la diferencia de `task_base` es confiable y la del
  merge base es más ancha) el nodo conserva la respuesta de hoy y el merge juzga como en GH-547: pueden
  discrepar, y decide la puerta de merge.
- **Un job renombrado** en master ahora escala con clase `ci` y causa `unknown_names` en lugar de
  correr local, porque los nombres `ci:` se siguen leyendo en `task_base`.
- **Cierra** la viñeta «Fuera de esta tarea» de GH-547, cuyo texto no se edita.
