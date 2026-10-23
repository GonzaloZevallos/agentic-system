---
status: accepted
date: 2026-10-14
---

# El sistema aterriza sus propios pull requests

Entre 2026-10-08 y 2026-10-13 quedaron abiertos 16 pull requests aprobados durante cinco
días, porque `auto_merge_decision` los rechazó y el rechazo esperaba a un humano. Los
aterrizó a mano la sesión orquestadora: resolvió conflictos, corrió CI, corrigió hallazgos.
Nada de eso quedó trazado, costeado ni aprendido. Zeta (2026-10-13): "Todo lo debería
resolver el sistema agéntico solo, luego pensamos gates cuando llevemos al sistema al
siguiente nivel." Este ADR (GH-239) da a cada rechazo un camino que el sistema recorre solo,
acotado, trazado y anotado en el documento de la tarea.

## Qué enmienda

Enmienda por nombre, sin editar ninguna de estas frases (el historial queda donde se escribió):

- ADR-0045: la tabla de rechazos (suma `open_findings_blocking`, `open_findings` pasa a
  significar sólo `important`, y `guarded_path` sale), los caminos guardados (ya no
  rechazan) y GH-216, "no se reejecuta el CI" (el aterrizaje sí lo reejecuta).
- ADR-0034: la ausencia de un parámetro `label` en `Tracker.open_issue`.
- ADR-0039: el calendario de reanudación por límite de cuenta.

## La tabla: rechazo → paso

`domain/landing.landing_step(refusal, facts, spent, blocking_owners)` es el único lugar que
decide **qué hacer en vez de** mergear; `auto_merge_decision` sigue siendo el único que
decide **si** mergear. `LandingStep.kind` es un literal cerrado.

| Rechazo | Paso |
|---|---|
| `not_ready`, `pr_not_open` | `none` |
| `eval_set` | `none`: el merge humano de una tarea del set es la medición (ADR-0045) |
| `eval_set_unreadable`, `ci_missing`, `ci_unreadable` | `wait`; `diagnose` pasado `ci.CI_WAIT_MAX_S` |
| `not_approved`, `head_not_reviewed` | `re_review` |
| `open_findings_blocking` | `fix` sobre las unidades dueñas de los registros `blocking` abiertos |
| `open_findings` (sólo `important`) | `file_followups_and_land` |
| `ci_failed` | `rerun` una vez por head; después `fix` |
| `checks_failed` | `rerun` de los chequeos fallidos; después `fix`; con `failed_step == conflict`, `sync_master` |
| `attempts_exhausted` | `sync_master`; `diagnose` agotados los presupuestos |
| `merge_failed` | `diagnose` |
| `master_moved` | `wait`: la vuelta siguiente decide de nuevo |

Un `fix` sin dueños nombrados reabre la unidad revisada. Una tarea ya diagnosticada no tiene
paso (`none`).

Se eligió, de las dos salidas que ofrecía el issue para los hallazgos, ambas según la
severidad: un `blocking` manda a una pasada más de Developer → Tech Lead; un `important`
sobre una cabeza aprobada se archiva como seguimiento (un issue `agentic`, con la evidencia)
y el pull request aterriza.

## Presupuestos

- `LANDING_PASSES_MAX = 3` pasadas de modelo por tarea (`re_review`, `fix` y un
  `sync_master` que conflictúa).
- `LANDING_SYNCS_MAX = 6` `sync_master` limpios por tarea.
- Una pasada o un sync que excedería su presupuesto es el paso `diagnose`.

Cada paso se anota en `landing: [{head, step, refusal, at, cost_usd, resolved_at?}]`, bajo el
lock de corrida y **antes** de despacharse; la clave `(head, step)` nunca se despacha dos
veces. Es la forma de idempotencia: dos vueltas del poller sobre el mismo rechazo no duplican
trabajo.

## Caminos guardados y CI no confiable

`guarded_path` deja de ser un rechazo y pasa a ser un registro: un prefacio `Guarded changes`
que nunca se recorta en el cuerpo del PR, la clave `auto_merge_guarded` y una mención en el
comentario `Auto-merged:`. `rules.ci_untrusted(touched_paths)` es `None` si los caminos se
desconocen y `True` si alguno empieza con `CI_DEFINITION_PREFIX = ".github/"`: en ambos casos
un CI verde nunca decide `merge` (falla cerrado) y decide el chequeo local declarado en la
base de la tarea. El set de evaluación se sigue leyendo al sha de upstream.

## Camino terminal

`diagnose` corre un `landing_diagnoser` de sólo lectura, una vez por tarea. Luego el sistema
cierra el pull request, archiva la corrida y reinicia el issue con `--fresh`, con el
diagnóstico como sección del sistema. `landing_restarts` y `landing_cost_carried` viven en el
documento y los copia `--fresh`; los comentarios son sólo un registro. Con `RESTART_DEPTH_MAX
= 1`, un segundo agotamiento escala con clase `failure` y no archiva nada.

## Lo que se ensancha (README, sección de seguridad)

El sistema puede ahora: mergear caminos guardados, reejecutar el CI (`gh run rerun --failed`),
abrir issues con la etiqueta `agentic` (a lo sumo uno de seguimiento por tarea y uno de flake
por nombre de chequeo; una tarea de linaje ≥ 1 no archiva ninguno y los escribe en el cuerpo
de su PR) y cerrar sus propios pull requests. Con el tope de tarea desactivado no hay techo en
dólares: sólo la cota estructural de 2 corridas × (3 pasadas + 1 diagnóstico) a 10 USD por
intento. El peor caso del calendario de límite de cuenta es de 168 comentarios `Auto-resumed`
en una semana.

## Vocabulario

`Aterrizaje` entra al glosario de `agentic/core/process/CONTEXT.md`. Los tipos nuevos
(`LandingStepKind`, el valor `open_findings_blocking`) son literales cerrados con un test de
exhaustividad; las claves de span nuevas usan el prefijo `agentic.landing.` y no se agrega,
mueve ni renombra ninguna familia de métricas. Toda clave nueva del documento es opcional y se
lee con `.get`: un documento sin ella se renderiza idéntico.

## Adenda (2026-10-14, GH-242): `landing` tiene una sola forma en disco y un aterrizaje reabierto se recupera

**Diagnóstico.** `agentic land --issue 227` no fallaba al registrar el paso. Cuando el
documento no tenía la línea `landing:`, `record_closure` agregaba una línea de flujo y la
escritura funcionaba. Luego el paso `re_review` corría `landing_sync._reopened`, que llama a
`documents.save({**reopened, "landing": marked})`; `document.render` reescribía `landing` como
**bloque** YAML, y la siguiente llamada, `record_landing` (`record_closure`), se negaba porque
veía líneas de continuación. La reapertura y el `cost_base` ya estaban en disco, pero
`spawn_resume` nunca corría: la tarea quedaba en `developing` y un segundo `land` devolvía
`none`/`not_live`. `deliver.finish` y el camino `save` + `record_landing` de `landing_sync`
hacen la misma secuencia.

**Una sola forma.** `landing` se escribe siempre como una línea de flujo, y sólo
`agentic/core/process/domain/document.py` elige esa forma (un ayudante privado que usan
`render` y `record_closure`). Ningún módulo fuera de `document.py` la formatea.

**`RECORD_KEYS` y migración por reescritura.** `RECORD_KEYS = frozenset({"landing"})` es una
lista de permitidos junto a `RECORDED_AS`: sólo una clave de ese conjunto puede tener su valor
en bloque reemplazado por `record_closure`, y la línea de la clave más todas sus líneas de
continuación pasan a ser la línea de flujo. Esa sustitución es también la migración: un
documento que ya tiene `landing` en bloque (GH-227) se reescribe en flujo en su próxima
escritura, por `save` o por `record_closure`. Toda otra clave sigue levantando `ValueError`
(`runs` y `failures` incluidos), un documento sin `landing` queda byte a byte igual, y `parse`
lee ambas formas como la misma lista. El puerto, el adaptador de filesystem y el fake no
cambian.

**Regla de recuperación** (la construyen las subtareas `entry` y `wiring`, con estos nombres):
`LandingEntry` gana `resume_spawns` y `resume_spawned_at`; `landing.resume_owed(raw, now)` es el
único predicado que decide si hay que volver a lanzar el `resume` de un aterrizaje reabierto
(`LANDING_RESUME_SPAWNS_MAX = 2`, `LANDING_RESUME_GRACE_S = 300`; compara fechas con zona,
nunca cadenas, y es falso ante una marca presente que no se puede leer o no tiene zona).
`landing_sync.spawn_and_mark` lanza el `resume` y sólo escribe el contador si devolvió un pid.
Si sólo el tope de 2 está agotado, `land` devuelve `skipped/resume_spent`, la única razón
nueva de `LandResult`. El poller ofrece `land` para un candidato `not_ready` con `resume_owed`.

**Riesgos conocidos.** Un hijo muerto en su primer rol no deja `RoleRun` y puede relanzarse; el
costo está acotado por 2 lanzamientos × el tope por intento, no entra en `cost_base`/`cost_usd`
de la entrada ni de la tarea, y es el mismo hueco que nombra la segunda adenda de ADR-0008 del
2026-09-18 para el dinero que un nodo pierde antes de devolver un parche. Los pasos que no
reabren (`rerun`, `file_followups_and_land`, `diagnose`) conservan la idempotencia por
`(head, step)`: una caída entre registrar y actuar parece un éxito y ese paso no se reintenta;
los presupuestos mueven al paso siguiente en lugar de bloquear. Los 300 s de gracia están
declarados, no medidos.

**Sin cambios de observabilidad.** No cambia ninguna métrica, etiqueta, atributo de span ni
archivo de prompt.

## Adenda (2026-10-15, GH-255): un conflicto con master se decide antes que el CI

**Diagnóstico.** Los pull requests #146, #182, #186, #193, #194, #196 y #206 de apostrophe
estaban aprobados y en conflicto con master, y el poller sólo los registraba como `ci_failed`,
`ci_missing` o `waiting: ci missing`. El conflicto sólo se conocía por
`merge_checks.failed_step == "conflict"`, que escribe `run_checks` después de que la compuerta
del CI deja pasar el PR; con el rechazo del CI primero, `landing_step` nunca elegía
`sync_master` y la rama no volvía a ponerse al día con master.

**El verbo.** `Workspace.merge_conflicts(head, master) -> bool | None` corre
`git merge-tree --write-tree --quiet` tras traer ambas shas, bajo `MERGE_TREE_TIMEOUT_S = 30`
(declarado, no medido): salida 0 es `False`, salida 1 es `True` y todo lo demás (otra salida,
un fetch fallido, `GitError`, `OSError`, `SubprocessError`, el timeout incluido) es `None`, sin
levantar nunca. Escribe sólo objetos sueltos en `.git`: no toca árbol de trabajo, rama ni
índice (ADR-0002).

**La fila nueva.** `merge_conflict` → `sync_master`. Enmienda por nombre el orden de rechazos de
ADR-0045: va después de `eval_set` y antes de `merge_failed`, de todo `ci_*` y de `wait`. Sólo
`conflicts is True` lo produce; `None` y `False` dejan la decisión de siempre.
`landing.CONFLICTING_SYNC_REFUSALS = ("checks_failed", "merge_conflict")` es la única lista de
los rechazos cuyo `sync_master` es conflictivo: cuenta como pasada, nunca como sincronización
limpia, de modo que "conflicto, sync, conflicto" lo acota `LANDING_PASSES_MAX` y luego
`diagnose`.

**La compuerta, derivada.** `rules.conflict_probe_wanted(facts)` es verdadera exactamente
cuando `auto_merge_decision` con `conflicts=True` rechaza con `merge_conflict`: el orden vive en
un solo lugar y nadie más lista nombres de rechazo.

**Dónde corre la sonda.** En `poll.assess_merge` (compartida por `gather_merges` y
`landing.land`), después de decidir sin sonda, sólo si la tarea no está diagnosticada y la
compuerta se cumple; un rechazo `merge_conflict` no hace ninguna llamada al CI. Y en el verbo
`agentic merge`, sólo en la rama `merge`, justo antes de escribir la intención: ante `True`
rechaza `merge_conflict` sin escribir intención, `merge_failures` ni comentario
(`merge_conflict` no está en `COMMENTED`) y sin llamar a `Tracker.merge`. No corre en la rama
`run_checks`: allí manda el `Workspace.merge` del árbol de trabajo.

**Una sonda no leída no cambia nada.** Con `None` la decisión es la de siempre y el poller
agrega la línea `auto-merge #N -> conflict unread (<head>)`.

**§7.** No se agrega, mueve ni renombra ninguna métrica, etiqueta, atributo de span, clave de
documento ni archivo de prompt; `merge_conflict` aparece sólo en líneas del poller y en
`landing[].refusal`, clave existente.

**§8.** Nada nuevo pasa a ser alcanzable: la sonda lee shas que el sistema ya trae. El costo
por ronda es, como mucho, un fetch acotado más un `merge-tree` de a lo sumo 30 s por candidato
no diagnosticado que pasa los rechazos sin git, más uno por merge real en el verbo; crece con
la cantidad de candidatos abiertos, no con el tamaño de los datos. Un PR en conflicto se
ahorra además la llamada a `gh` del CI.

**Riesgos conocidos.** Un `None` persistente (git anterior a 2.38 sale con 129; un clon
superficial o una cabeza que no se puede traer falla igual cada ronda) conserva la conducta de
antes y puede gastar una pasada de `fix` en una cabeza en conflicto; sólo se ve en la línea
`conflict unread`. La sonda y el merge del árbol de trabajo son invocaciones distintas y pueden
discrepar: un `True` falso gasta una pasada y un `False` falso lo atrapa la vía vieja
`checks_failed`/`conflict`; ambos los acota `LANDING_PASSES_MAX` y se ven en `landing`. Los
presupuestos se comparten entre todos los rechazos: un PR que ya gastó sus pasadas va a
`diagnose` en su primer conflicto.

## Adenda (2026-10-15, GH-257): un diagnóstico que falla no es una escalación

**Causa.** El Landing Diagnoser corre fuera del grafo, en `landing_fix.diagnose`. Cuando su
`run_role` fallaba dos veces (típicamente HTTP 429), `diagnose` guardaba el parche de escalación
de `run_role`: `waiting_on_human`, `escalated`, `escalation_class` y entradas `failure` en
`pause_log`, sin etiquetar ni comentar el issue. El checkpoint del grafo estaba bien (la corrida
terminó en `ready_for_review`), así que `resume` rechazaba con `is not paused` (correcto, y
sin tocar), el poller leía `not_ready` y `land` devolvía `none` porque `diagnosed` ya era cierto.
La tarea quedaba varada sin dueño.

**Decisión.** Un diagnóstico que no entrega es un intento, no una escalación.

- `diagnose` guarda solo la contabilidad del parche (`cost_usd`, `runs`, `current_role`) y deja la
  tarea en `ready_for_review`; el motivo (`FailureReason`) se escribe en su propia entrada de
  `landing` con `record_closure`.
- Dos claves opcionales nuevas en la entrada `landing`: `delivered` (el rol entregó; se escribe
  con `record_closure` antes de escalar, cerrar el PR o lanzar nada, y ningún `save` posterior
  reescribe la instantánea vieja de `landing`) y `failure`. Se leen con `.get`: un documento
  sin ellas se lee y se muestra como antes. `spent.diagnosed` cuenta solo entradas `delivered` y
  `already_dispatched` ignora las entradas `diagnose` sin entregar.
- Los reintentos siguen el calendario compartido de los reintentos 429: `rules.retake_calendar`
  (el cuerpo de `_calendar`, extraído sin cambios), `rules.next_attempt_at` y
  `rules.auto_resume_limit`; no hay constante ni tope nuevos. `domain.landing.diagnose_retry`
  devuelve `none`, `due`, `waiting` o `escalate`. `since` es la hora de despacho del último intento
  (`at` de la entrada), unos segundos antes de que fallara: aceptado. Como ninguna corrida entrega
  entre dos intentos, la cadena del diagnóstico es el calendario plano `ACCOUNT_LIMIT_SCHEDULE` y
  nunca llega a la semana horaria. Con `prior = chase_prior =` cantidad de intentos, la cadena
  entera tiene `len(ACCOUNT_LIMIT_SCHEDULE)` intentos.
- Un motivo que ninguna espera cura, o un calendario agotado, escalan de verdad por un solo
  ayudante (`landing_fix._escalate`): etiqueta `needs-human`, comenta `Escalated:` y escribe
  `rules.logged(rules.escalation("failure", …))` sin `failure` en el registro, así que
  `auto_resumable` queda falso. La rama de profundidad >= 1 usa el mismo ayudante.
  `domain.landing.unescalated` es el único parche que limpia la escalación de un paso de landing.

**§7.** Ninguna métrica, etiqueta, atributo de span, prompt ni `prompts.fingerprint` cambia.

**§8.** Un reintento cuesta como máximo el tope por intento (10 USD) por la longitud del calendario;
un 429 falla antes del primer turno y cuesta ~0 USD. Un fallo externo no 429 (`upstream`,
`network`) puede gastar dinero real por reintento, acotado por ese tope y por `RETAKE_SCHEDULE`.

**Fuera de alcance.** Una escalación deliberada de profundidad >= 1 sigue dejando una tarea que
`resume` rechaza (`is not paused`): es el comportamiento de un alto humano deliberado.
`agentic/cli.py::_resume` y su texto de rechazo no cambian.

**Migración de los documentos varados.** Los documentos que el diagnóstico dejó escalados antes de
este cambio (apostrophe GH-121, #132, #135, #144, #150) los libera `agentic land`, con una sola
escritura. El discriminador es `domain.landing.stranded_diagnose(raw, escalated_after)`: `waiting_on_human`
con clase `failure`, `pull_request`, el diagnóstico como rol actual, la última entrada `diagnose`
sin `delivered` ni `failure`, y una época del `pause_log` (después del último `resumed`) con al menos
un `failure`, todos del propio diagnóstico y con motivo en `rules.EXTERNAL_REASONS`, más la entrada
`escalation`. `escalated_after` lee los comentarios solo cuando el documento ya tiene esa forma: un
`Escalated:` creado después del `at` de la entrada dice que un humano fue avisado y la parada es
suya; una hora ingenua o ilegible también lo deja escalado. La escritura es un solo
`record_closure` con `domain.landing.migration`: el parche de `unescalated` más el `failure` de la
época que cierra, puesto en la última entrada `diagnose`; después rige el reintento ordinario.
Nunca escribe `cost_usd` ni borra historia: `record_closure` reemplaza una clave entera, así que
`pause_log` se escribe completo con `resumed` al final (`document.RECORD_KEYS` suma `pause_log` y
`escalated`, que el YAML parte en varias líneas). El poller ofrece `land` para estas tareas y
`gather_resumes` las salta, de modo que un solo camino es dueño de cada documento.

**Riesgo conocido.** La migración escribe documentos vivos: un error del discriminador desescalaría
una parada humana real. Los casos contrarios (escalación real con y sin comentario, `sdk`, rol del
grafo, `questions`, sin pull request) quedan fijados por tests.

## Adenda (2026-10-16, GH-267): una sincronización se resuelve solo si el head contiene su upstream

Enmienda este ADR y la adenda de GH-242 sin editarlos: dicen "una entrada se cierra tras la
re-entrega", y para `sync_master` eso dejó de ser suficiente.

**Diagnóstico (apostrophe GH-131, PR #252).** `sync_master` eligió dos veces el mismo paso y las dos
veces quedó `resolved_at` sin que master entrara en la rama. Había dos fallos. (1) Ante un conflicto,
`merge_upstream_into` dejaba `MERGE_HEAD` puesto, se reabría la unidad y se lanzaba `agentic resume`.
El resume arranca en `integrate`, cuyo `_merge_approved` corre `git merge <rama del subtask>` en el
mismo worktree; con un merge en curso git lo rechaza y `GitWorkspace.merge` hace `git merge --abort`,
con lo que el merge de master se tiraba sin que nadie lo viera. El integrador encontraba un árbol
limpio y un motivo que describía un merge que ya no existía. (2) `deliver` llamaba a
`landing.resolve_open` en toda re-entrega, sin comprobar ascendencia. GH-237 y GH-166 funcionaron
porque su merge fue limpio: `sync_master` empujó directo y no hubo resume.

**La regla.** `resolved_at` en una entrada `sync_master` significa "cerrada, y el head contiene su
`upstream`". Todo otro paso conserva "cerrada tras la re-entrega". `land` escribe `upstream` (el sha
de master de la ronda) en la entrada `sync_master` al despachar. `landing.sync_verdict(entry,
contains)` es pura: toma la entrada y la respuesta de ascendencia (`bool | None`), no llama a git, y
`None` es `unsynced`. `deliver` y el camino limpio de `sync_master` la consultan con
`Workspace.is_ancestor`; el head se entrega (push y PR) en todos los casos.

**Sincronización no lograda.** Un head entregado que no contiene el `upstream` cierra la entrada con
`closed_at`, `cost_usd` y `lacks` (el sha que faltó), nunca con `resolved_at`. `LandingSpent
.unsynced_for_upstream` cuenta las entradas con `lacks` igual al sha de master de la ronda, por sha y
no por head; `LANDING_UNSYNCED_MAX = 2` (declarado, no medido) y entonces el paso es `diagnose`, el
camino terminal que ya existe. Sin sha, el conteo es 0. Un master ocupado, que cambia entre pasadas,
agota `LANDING_PASSES_MAX` y llega al mismo `diagnose` por el otro camino.

**"En vuelo" se lee en un solo lugar.** Si una entrada terminó solo lo dice `rules.entry_closed`
(`resolved_at` o `closed_at`): `rules.landing_open` y la guarda de `resolve_open` leen a través de
ella, y ningún otro código lee `resolved_at` ni `closed_at` para eso. `already_dispatched` ignora las
entradas con `lacks`, de modo que un resume que no empuja nada puede despacharse otra vez y contarse.
`with_cost_base` escribe solo en la última entrada.

**Ningún paso deja un merge en curso.** `landing_sync.merges_cleared` envuelve el cuerpo de
`sync_master`, `re_review` y `renumber_adr`: al entrar y en toda salida llama a `Workspace.abort_merge`
sobre un `MERGE_HEAD` que reconoce como propio (`landing.merge_owner`). El aborto es propiedad de ese
bloque, no de cada rama; `abort_merge` solo se llama desde ahí y `merge_head` solo lo leen
`merges_cleared` y la guarda de `land`. Ante un conflicto, la unidad se reabre con un motivo que le
dice al Developer que corra `git merge <sha>`, resuelva y confirme. Si el merge en curso no es del
sistema (`merge_owner` responde `foreign`), `land` termina con `skipped/foreign_merge` y no registra
nada. `integrate._merge_approved` y `GitWorkspace.merge` no cambian, aborto-al-rechazo incluido: el
arreglo asegura que nunca se topen con un merge abierto.

**Glosario.** El término es **Sincronización no lograda**, en el glosario de Proceso.

**§7.** Ninguna métrica, etiqueta, atributo de span, prompt ni `prompts.fingerprint` cambia. Claves
opcionales nuevas en las entradas `landing`: `upstream`, `closed_at`, `lacks`, escritas solo cuando
existen y leídas con `.get`. Razón nueva de `LandResult`: `foreign_merge`.

**§8.** Nada nuevo se vuelve alcanzable. El sistema puede ahora correr `git merge --abort` en su propio
worktree de tarea, y solo sobre un merge que reconoce. Por ronda: un `rev-parse MERGE_HEAD` por paso
que fusiona y un `cat-file` más un `merge-base --is-ancestor` por re-entrega de una sincronización;
ninguno crece con el tamaño de los datos. Una sincronización que no trae master cuesta como mucho una
pasada más y luego `diagnose`; antes el ciclo no tenía cota (~6 USD por vuelta).

**Riesgos conocidos.**
- `merge_owner` toma cualquier `upstream` o `head` registrado como propio: un merge a mano de un master
  viejo registrado, en un worktree `ready_for_review`, se abortaría.
- Un merge ajeno repite `skipped/foreign_merge` en cada tick hasta que una persona lo termine.
- Un stage0 fijado antes de este cambio no conoce `closed_at` y lee una entrada cerrada sin lograr
  como en vuelo hasta que se re-fije.
- El Developer fusiona el sha fijado al despachar: un master ocupado puede costar una pasada por
  movimiento, acotada por `LANDING_PASSES_MAX` y luego `diagnose`.
- Un Developer que ignora la instrucción se detecta solo en la entrega, y cada vuelta cuesta un resume
  y una revisión.
- Las entradas escritas antes de este cambio no tienen `upstream` y se resuelven como antes; GH-131 queda
  cubierto desde su próximo despacho.
- Un SIGKILL entre el merge y el aborto solo lo cubre la limpieza al entrar de la pasada siguiente.
- Seguimiento: mantener el merge con conflicto dentro del nodo develop, como hace el camino de
  dependencias.

## Adenda (2026-10-22, GH-297): reiniciar es el último recurso; antes se aterriza por tramos

**Contexto.** En apostrophe GH-53 todas las subtareas y todas las revisiones finales aprobaron, pero el
merge fallaba en cada sincronización con un master que se movía. Tras dos rechazos el Diagnoser cerró el
pull request y reinició la tarea de cero, tirando trabajo ya aprobado, y las decisiones del humano de
la primera vuelta no llegaron a la segunda. Decisión del dueño (2026-10-21): reiniciar es el último recurso.

**`land_slice`.** Cuando un presupuesto del aterrizaje se agota, el paso ya no es siempre `diagnose`.
`landing_step` pide, en su única función de agotamiento, el veredicto de `landing.slice_or_restart`: un
**tramo aterrizado** o el motivo cerrado de reiniciar. El tramo es un prefijo estricto, en orden
topológico, de las subtareas aprobadas aún no aterrizadas, cerrado bajo `depends_on`: nunca incluye todas
(lo que queda es el pull request principal). Una sola regla decide si la revisión de una unidad vale para
el tramo, `landing.review_carries`, la misma que usa el pull request principal; ninguna regla de
ascendencia ni de rutas la reemplaza (la ascendencia y un build limpio son condiciones extra). Una unidad
que toca las rutas del trabajo del integrador no entra.

**El pull request del tramo.** Se arma desde master en `agentic/GH-N.slice-<k>`, corre los chequeos del
producto y se abre con `closes_issue=False`. Nunca se escribe en `pull_request` del documento y no tiene
resultado de entrega. El merge sigue siendo solo `auto_merge_decision` sobre los `MergeFacts` que arma
`landing.slice_facts`, y va únicamente por `Tracker.merge`.

**La ascendencia de git es la verdad.** Si una unidad ya aterrizó se lee de `Workspace.is_ancestor` del
`reviewed_head` de la unidad contra master (`landing.slice_landed`), nunca de `landed_slices` solo; esa
lista del documento es un registro, y un proceso que muera entre el merge y el registro se corrige en la
vuelta siguiente.

**La época de sincronización.** Tras aterrizar un tramo, el pull request principal abre una época nueva:
los presupuestos de sincronización se cuentan de nuevo desde ese tramo. Es una segunda
definición de «presupuesto gastado», cubierta por los tests del dominio y por el escenario del ejecutor.

**Los motivos de reinicio (`RestartWhy`).** Un conjunto cerrado: `single_unit` (hay menos de dos unidades
pendientes, nada que partir), `no_clean_slice` (ningún prefijo cumple `review_carries` y esquiva las
rutas del integrador, o las rutas no se pudieron armar), `slice_refused` (los prefijos limpios fueron
rechazados `SLICE_REFUSALS_MAX` veces) y `unreadable` (el documento no se pudo leer). Viaja en la
entrada `diagnose` como `restart_why`. El caso GH-53 es `no_clean_slice`: master tocó los mismos archivos
que las subtareas, y la otra salida era mergear sin revisión.

**El aviso del reinicio.** Al reiniciar, `diagnose` cierra el pull request, registra la profundidad y el
costo, comenta en el issue y lanza la corrida nueva, en ese orden, y no espera aprobación de nadie. El
comentario es `Restarted by landing: <restart_why>: <motivo> (cost so far: $X.XX; landed slices: #a, #b)`:
conserva el prefijo literal `RESTARTED_BY_LANDING` (así `human_decision` lo sigue leyendo como del
sistema), añade `(integration work)` tras el motivo cuando el trabajo del integrador bloqueó el tramo, y
omite el motivo cerrado en un documento anterior. Si el comentario falla, se registra y la corrida nueva
nace igual. La profundidad `RESTART_DEPTH_MAX` sigue escalando como antes.

**`Human` se hereda.** Todas las decisiones del humano (`sections[HUMAN]` del documento archivado) llegan
a la corrida nueva por `landing.carry_over` y `rules.entering`, como la sección `Human` de la corrida
fresca; ningún camino la concatena a `description` ni a otra sección. Los roles la tratan como resuelta.
La sección del Diagnoser sigue pegada a `description`, como hoy.

**Glosario.** El término nuevo es **Tramo aterrizado**, en el glosario de Proceso; **Aterrizaje** lo menciona.

**§7.** Ninguna métrica nueva. `LandingStepKind` suma `land_slice` (`run_kind_of("land_slice")` es
`land_checks`) y aparece el literal cerrado `RestartWhy`, que viaja en la entrada `landing`. Claves
opcionales nuevas, leídas con `.get`: `landed_slices` en el documento y, en las entradas, `slice_units`,
`slice_pull_request` y `restart_why`; un documento sin ellas se renderiza igual byte a byte.

**§8 (se ensancha).** El sistema puede ahora abrir y mergear un segundo pull request del mismo producto
por tarea, el del tramo. Siempre pasa por `auto_merge_decision` (rutas protegidas y `ci_untrusted
incluidos), siempre mergea con `Tracker.merge` y nunca empuja a master; nadie más gana acceso. Costo: solo
cuando el aterrizaje iba a terminar en `diagnose`, un merge en un árbol temporal, N consultas de rutas y
ascendencia (N = unidades aprobadas) y, para el candidato elegido, una armada más los chequeos; lineal en
las unidades y sin modelo. Un aterrizaje sano no paga nada.

**Riesgos conocidos.**
- Si master se mueve sobre los mismos archivos que las subtareas, ninguna unidad cumple `review_carries`:
  se reinicia igual, ahora con `no_clean_slice` escrito en el aviso.
- Si las rutas del integrador no se pueden armar, el resultado es `no_clean_slice`.
- Un SIGKILL entre el merge del tramo y su registro deja un pull request de tramo abierto; la entrada
  guarda `slice_pull_request` para cerrarlo en la vuelta siguiente.

## Adenda (GH-328): una decisión humana libera el `diagnose` escalado

Un `diagnose` que `landing_fix.escalate_failed_diagnosis` escaló (clase `failure`, con intentos sin
entregar) no tenía vuelta: `diagnose_gate` respondía `skip` mientras `escalated` estuviera puesto y ninguna
entrada llamaba a `unescalated`. Ahora la respuesta de una persona en el issue lo libera.

**Condiciones (todas).** Un comentario que no es del sistema después del último `Escalated:`
(`resume.decision_since`, el único lector); el issue `OPEN`; las etiquetas legibles y sin `needs-human` (una
etiqueta que nunca se puso cuenta como quitada); y `rules.task_spent` por debajo del tope de la tarea. Un
tracker que no contesta no libera. La decisión se decide solo cuando `step_for` eligió `diagnose` para la
cabeza actual, y se escribe en la misma llamada de `land` que agrega la entrada y despacha, después de las
comprobaciones de cabeza movida y merge ajeno: ningún camino deja un documento liberado sin despachar.

**Qué escribe.** `landing.released` es el único constructor del parche: `unescalated`, `released_at` en la
última entrada `diagnose` (los intentos se cuentan desde la siguiente, así que `diagnose_retry` no escala sin
un intento nuevo) y la decisión como un `### Decision N` más de la sección `Human`, que el Landing Diagnoser
lee. Dos aplicaciones dejan una sola decisión y una sola marca `resumed`.

**Sin respuesta.** Un `diagnose` escalado ya no registra `diagnosis not due` ni `already taken`: dice
`escalated, waiting on a human (...)` con lo que lo libera, y `LandResult.reason` es
`escalated_waiting_on_human`, `escalated_needs_restart` o `escalated_task_cap`.

**Lo que no cambia.** La escalación de profundidad >= 1, posterior a un diagnóstico entregado, sigue siendo
de la persona (`run --fresh`): un comentario no la libera. El tope de turnos del rol es el mismo
(`max_turns=12`), así que un segundo fallo escala tras exactamente un intento nuevo. Una decisión que pide
un merge la lee el Diagnoser como insumo de un reinicio, no se ejecuta: un diagnóstico entregado a
profundidad 0 cierra el pull request y reinicia con `run --fresh`. El aterrizaje lee la decisión desde el
último `Escalated:` e ignora los comentarios del sistema; la ruta de roles (`human_decision`) sigue
reiniciando en el último comentario del sistema.

**Costo.** Por ronda, una lectura de comentarios y una del issue por cada documento escalado con la forma
liberable; escala con esos documentos, no con todas las tareas. Un comentario ajeno puede liberar una tarea
sin la etiqueta `needs-human` (comportamiento elegido).
