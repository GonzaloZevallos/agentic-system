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
