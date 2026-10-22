---
status: accepted
date: 2026-10-03
---

# El poller de un producto retoma los cortes externos, cierra lo mergeado y lanza el próximo issue

ADR-0003 dejó la búsqueda de tareas por retomar "al poller de la etapa 5": `resume` nunca
busca solo. Este ADR registra el primer pedazo de ese poller (GH-137), el que mantiene
andando la cola de un producto sin un humano mirando: `agentic poll --product-dir <dir>
[--detach] [--interval S]`, un proceso de larga vida por producto que cada `S` segundos
(60 por defecto) da una vuelta. Los relojes de los roles proactivos quedan afuera.

## Decisión

### Un motivo propio para la red caída

`FailureReason` suma `network`. `rules.failure_reason` lo devuelve cuando
`terminal_reason == "api_error"` y `api_error_status is None`, después de presupuesto,
429, 5xx y salida estructurada agotada, y antes de `sdk`. Sigue la regla de ADR-0029: el
motivo se decide en el sitio donde se levanta el error y sobre campos estructurados,
nunca leyendo el texto. `EXTERNAL_REASONS = ("account_limit", "upstream", "network")` son
los tres motivos que no son culpa de la tarea; el scorecard excluye los tres de "fallos
propios" (`scorecard.py` y la entrada **Scorecard** del glosario de Medición).

### `pause_log` y la época

`TaskState` gana `pause_log: list[PauseEntry]`, append-only (reducer `operator.add`, que
`task.delta` ya sabe restar), persistido en el frontmatter y leído con
`.get("pause_log", [])`. Tres formas de entrada:

- `{"kind": "resumed"}`, que agrega `rules.resumed`;
- `{"kind": "escalation", "value": EscalationClass}`;
- `{"kind": "failure", "value": FailureReason, "section": str}`.

La **época actual** son las entradas posteriores al último `resumed`. Todo escalado se
compone con `rules.logged(patch, failures)`, que lee el `escalation_class` del patch y
concatena su entrada y una por cada `Failure` del rol, sin pisar nunca lo anterior.
`failures[-1]` no sirve para lo mismo: `failures` se pliega en el fan-in desde ramas
paralelas, y "el último" depende del orden del reducer.

`EscalationClass`, `rules.escalation`, `escalate.py` y el texto de cada comentario
`Escalated:` no cambian (ADR-0029 rechazó un octavo valor por eso mismo).

### Quién se retoma solo, y cuándo

- **Elegibilidad**, en una sola función pura: `rules.auto_resumable(state)` mira la época
  actual y exige al menos una entrada `failure`, que toda entrada `escalation` sea
  `failure` y que toda `failure` tenga un motivo en `EXTERNAL_REASONS`. No lee
  `escalation_class` sola ni prosa. Los demás escalados (`questions`, `returns`, `cap`,
  `unmergeable`, `setup`, `classification`, y `failure` por otra causa) quedan para el
  humano, como antes.
- **Espera**: `rules.next_attempt_at` sobre el calendario `AUTO_RESUME_SCHEDULE = (15, 30,
  60, 120, 120, 120)` minutos, contados desde el `createdAt` del último comentario
  `Escalated:`, nunca desde un "resets 7:30pm" parseado. `prior` es la cantidad de
  comentarios `Auto-resumed:` posteriores al último comentario que no es del sistema.
- **Límite**: `rules.auto_resume_limit` da 6 intentos, o 1 si algún fallo de la época cayó
  en una sección de entrevista (`roles.INTERVIEWS`). ADR-0014 (adenda del 2026-09-18)
  aceptaba que cada retake de una entrevista diera dos rondas "sin techo propio" porque
  cada ciclo necesitaba una decisión del humano; sin humano en el medio, el límite de 1
  restituye ese techo.
- **Un humano manda**: un comentario que no es del sistema, posterior al último del
  sistema, deja la tarea para el humano. El poller postea `Auto-resumed: <reason>` (prefijo
  en `resume.SYSTEM_COMMENTS`, así `resume` no lo toma por una decisión humana) y después
  lanza `agentic resume --issue N --yes --detach`.

### El orden de cada vuelta

`rules.poll_plan` es puro y decide:

1. todos los cierres (`agentic close --issue N` de cada tarea con PR mergeado y sin
   cerrar);
2. los retomes vencidos, del número más bajo al más alto, mientras quede plaza;
3. a lo sumo un lanzamiento (`agentic run`), el número más bajo, y ninguno mientras alguna
   tarea del producto tenga una época externa elegible cuya espera no venció: un límite de
   la cuenta afecta a toda corrida, no sólo a la que lo encontró.

### El tope por máquina

`AGENTIC_MAX_CONCURRENT_RUNS`, por defecto 1, cuenta corridas de **todos** los productos.
El registro vive en `Configuration.run_registry_dir` (`$XDG_STATE_HOME/agentic/runs`, o
`~/.local/state/agentic/runs`), fuera de todo repo. `run` y `resume` escriben su entrada
(pid, host, producto, tarea) dentro de su lock y la borran al salir; si escribirla falla,
avisan en stderr y la corrida sigue. El poller decide bajo `flock` sobre `runs/.lock`, y
después de lanzar un hijo espera hasta 60 s a que se registre antes de seguir, para que una
segunda decisión no pase el tope. Un lanzamiento que no llegó a registrar corrida queda en
`launches/<producto>-<issue>.json` y sigue el mismo calendario de esperas: ningún issue se
relanza en cada vuelta.

### Bloqueantes

Salen de `blockedBy` de GitHub, o de la primera línea no vacía del cuerpo con la forma
`Blocked by: #N[, #M]`. Un bloqueante está resuelto sólo si su `stateReason` es
`NOT_PLANNED`, o si es `COMPLETED`, tiene PR y el merge commit de ese PR (el del documento
de tarea primero, si no el de `gh`) es ancestro del `HEAD` local (`Workspace.is_ancestor`,
que devuelve `False` para un objeto que no existe). Ante cualquier duda (un objeto
desconocido, un PR no encontrado, un error de `gh`) no se lanza.

### Lo que el poller no hace

No corre ningún rol en su propio proceso: todo pasa por hijos `close`, `resume` y `run`.
No mergea, y sobre el checkout del producto sólo usa verbos de git de lectura: nunca
`fetch`, `pull`, `checkout` ni `reset`. `run`, `resume` y `close` sin el poller conservan
sus códigos de salida, sus mensajes y las claves que escriben.

## Riesgos aceptados

- Una red caída que llegue por el `except Exception` de `runner.py`, y no como
  `api_error`, sigue siendo `sdk` y queda para el humano.
- Un `api_error` sin status que no sea red se reintenta solo; el techo es el calendario.
- "Fallos propios por tarea" no se compara de un lado al otro de este commit: antes los
  `EAI_AGAIN` contaban como `sdk`.
- La espera que bloquea lanzamientos mira sólo el producto de cada poller; entre productos
  sólo se comparte el tope por máquina.
- Heredado de ADR-0038: un checkout local atrasado no lanza hasta que el humano hace pull,
  y el log lo dice (`merged but not in HEAD, pull`).
- Un retake automático de una entrevista todavía da sus dos rondas una vez; se ve en
  `agentic.interview.rounds` de las tareas con comentarios `Auto-resumed:`.

## Considerados y descartados

- **Leer el motivo del texto de `escalated` o de `failures[].error`**: prosa (README,
  sección 14).
- **Un valor nuevo en `EscalationClass`**: mueve `rules.escalation`, el comentario del
  issue y `agentic.task.paused` (ADR-0029).
- **Esperar hasta la hora que dice "resets 7:30pm"**: prosa, y el mensaje cambia de forma
  sin aviso.
- **Un tope por producto**: los productos comparten la misma cuenta; dos corridas en
  paralelo chocaron con el límite de sesión (429) y con merges entre sí (apostrophe,
  2026-10-03).

## Adenda (2026-10-04): lo que cuesta leer el cuerpo de cada candidato

### Cambio de conducta de #142

Un candidato sin bloqueantes nativos (`blockedBy` ausente o con `blockedBy.nodes: []`)
ya no cuenta como "sin bloqueantes": se lee del cuerpo del issue con la regla de
`rules.blockers_from_body`, la misma función que decide qué línea cuenta y cómo se
parsea. `rules.blocker_numbers` trata `None` y `()` igual -- las dos caen al cuerpo;
quien sí distingue "el campo nativo no vino" de "vino vacío" es el adapter
(`tracker._blocked_by`, que devuelve `None` en el primer caso y `()` en el segundo).

### Error al leer el cuerpo

`rules.blocker_numbers` llama a su `body` (un thunk) sólo cuando hace falta, y ese
thunk es `ports.inbox.task(number)["description"]` en `poll._blocker_numbers`. Si
`GhInbox.task` lanza -- un cuerpo vacío (`ValueError("issue #N has no body...")`) o
una falla de `gh` -- la excepción sube hasta `poll._blocked`, que la atrapa junto con
`OSError`, `KeyError` y `GitError` y responde `True`: ese candidato queda bloqueado
"ante la duda, no lanza". Esto pasa en cada vuelta mientras el cuerpo siga vacío o
`gh` siga fallando, sin dejar ninguna línea en el log de `poll.run_once` -- ni
`execute` ni `gather_launches` imprimen nada por un candidato que ni llega a
`rules.poll_plan` -- así que ese issue no se lanza nunca, y nada en lo que el poller
escribe lo dice.

### Cifras de `poll.gather_launches`, por vuelta

Cada candidato recorrido en `gather_launches` paga, en el peor caso, esto -- ninguna
de estas cifras es el costo de la vuelta completa, sólo el de esta función:

- 1 `tracker.queue()`, una vez por vuelta, para enumerar los issues `agentic` abiertos.
- Por cada candidato sin documento todavía, 1 `issue_state` desde `poll._blocker_numbers`.
- +1 `inbox.task` cuando `blocked_by` llegó `None` o `()`: el thunk que
  `rules.blocker_numbers` recibe sólo se evalúa en ese caso, nunca cuando el nativo ya
  trae algo. Esto es el piso de 2·N de #142, con N = candidatos sin bloqueantes
  nativos.
- +1 `gh issue view` dentro de ese mismo `issue_state`, cuando el `gh` instalado no
  expone `blockedBy` en absoluto (el `except ValueError` de `GhTracker.issue_state`
  reintenta con un `--json` más chico).
- Por cada bloqueante que el candidato sí tiene, +1 `issue_state` desde
  `poll._blocker_resolved`.
- +1 `GhTracker.pull_request` (un `gh api graphql`) por cada issue que ese
  `issue_state` leyó con al menos una referencia en
  `closedByPullRequestsReferences` -- `GhTracker.issue_state` la paga sobre la
  primera de esas referencias cualquiera sea el `state` del issue, abierto o
  cerrado; nunca `poll._blocker_resolved` ni `poll._blocker_numbers` directamente.

### Qué corre bajo el lock

`poll.run_once` llama a `gather_closes` y `gather_resumes` antes de
`registry.exclusive()`; sólo `gather_launches`, de las tres lecturas de la vuelta,
corre dentro de ese bloque, y las cifras de la sección anterior se pagan ahí haya o no
plaza disponible -- `available` se calcula antes, pero `gather_launches` no lo
consulta para decidir si lee. Mientras el bloque dura, este poller retiene el `flock`
del registro de la máquina (`runs/.lock`) y cualquier otro poller, de este producto o
de otro, que intente su propia decisión bajo `registry.exclusive()` espera. El bloque
no termina cuando `gather_launches` vuelve: `execute`, con sus propios `launcher.spawn`
y su propia espera de hasta `REGISTER_WAIT_S` segundos a que cada hijo se registre,
corre también adentro.

### Lo que también se paga en cada vuelta, antes del lock

- `poll.gather_closes`: un `GhTracker.pull_request` por cada documento con
  `pull_request` y sin `closed_at` todavía -- uno por tarea, no por candidato de
  `gather_launches`.
- `poll.gather_resumes`: un `tracker.timestamped_comments` por cada tarea que
  `rules.auto_resumable` da por elegible para un retome.

Ninguna de las dos corre bajo `registry.exclusive()`, así que ninguna retiene el
`flock` de la máquina.

### Riesgos que quedan para otra tarea

- La lectura de `gather_launches` corre bajo el lock en vez de antes, como
  `gather_closes` y `gather_resumes`.
- Un cuerpo vacío deja el issue bloqueado para siempre sin ninguna línea de log que lo
  diga.
- `gather_closes` no tiene tope: un documento con `pull_request` y sin `closed_at`
  paga su `GhTracker.pull_request` en cada vuelta hasta que alguien lo cierre.

## Adenda (2026-10-05): tras un 429 el poller reintenta cada 15 minutos, no sobre un calendario creciente

### Qué cambia

`AUTO_RESUME_SCHEDULE` se parte en tres constantes de `rules.py`, y la constante vieja
deja de existir (sin alias):

- `LAUNCH_SCHEDULE = (15, 30, 60, 120, 120, 120)`: el calendario de los intentos de
  lanzamiento. No cambia: un lanzamiento que nunca se registró no es un 429.
- `RETAKE_SCHEDULE = (15, 30, 60, 120, 120, 120)`: el de los retomes de `upstream` y
  `network`. No cambia.
- `ACCOUNT_LIMIT_SCHEDULE = (15,) * 24`: el de los retomes de `account_limit`, plano.

`rules.schedule_for(reason)` es el único lugar donde un motivo se vuelve calendario, y
`next_attempt_at` y `_schedule_exhausted` reciben ese calendario como argumento
obligatorio (`schedule`, sólo por nombre, sin valor por defecto): cada lanzamiento pasa
`LAUNCH_SCHEDULE`, nunca el de un retome. El motivo que elige el calendario es el que ya
devuelve `auto_resumable` (el último fallo de la época). Sigue sin parsearse ningún
"resets at": la espera cuenta desde el `createdAt` del último `Escalated:`.

### Evidencia

El calendario creciente se pasaba del reinicio de la cuenta:

- 2026-10-04: el límite se reinició a las 01:20; el retome llegó a las 02:15 (55 min ociosos).
- GH-11: unas 2 h ociosas después del reinicio.
- 2026-10-05: el reinicio fue a las 07:40; el siguiente reintento programado hubiera sido
  a las 09:32.

En las últimas 17 tareas (2026-10-04 00:00 → 2026-10-05 17:15 UTC, una ventana de 41.2 h)
hubo 89 fallos por 429, unas 7.8 h detenidas por ellos, y la máquina estuvo ociosa 23.2 h
(56%). La cuenta corta a ≈ $165–200 por ventana de 5 horas (cortes vistos a $177, $184,
$200, $163). Un reintento que vuelve a chocar con el límite cuesta ≈ $0: el 429 llega en el
primer turno del rol. Una espera larga no compra nada; lo único que importa es qué tan
pronto después del reinicio cae el siguiente intento.

### Cuenta por ventana de la cuenta

Para `account_limit`, `prior` ya no es la cantidad de `Auto-resumed:` de toda la cadena sino
la de la ventana actual (`rules.window_probes`): los `Auto-resumed:` posteriores al último
comentario que no es del sistema **y** creados después del `ended_at` más tardío de
`state["runs"]` menos `CLOCK_TOLERANCE`. Una corrida entregada prueba que la cuota había
vuelto, así que abre una ventana nueva.

- **Tolerancia de 120 s** (`CLOCK_TOLERANCE`): absorbe el viaje de `gh issue comment` tras el
  spawn, los segundos enteros de `createdAt` y el desfase NTP entre el reloj de la máquina
  (`started_at`/`ended_at`) y el de GitHub, y está muy por debajo de los 15 minutos entre
  dos slots. Todo error que permite se resuelve hacia gastar (un retome contado, un techo
  gastado): manda la tarea al humano antes y nunca paga un retome que no corresponde. Es un
  juicio, no una medición; es una constante con nombre.
- **Fallbacks que fallan hacia el humano**: si algún `ended_at` falta o no se puede leer, se
  usa la cuenta de toda la cadena; si algún `started_at` de una ronda de entrevista falta o
  no se puede leer, el techo de la entrevista cuenta como gastado.
- `upstream` y `network` no cambian: `prior` sigue siendo `_prior_auto_resumes`, y ese
  camino no lee `state["runs"]`.

### Tope de la cadena entera

`ACCOUNT_LIMIT_CHASE_MAX = 96` (cuatro ventanas de 24) se compara con la cuenta de toda la
cadena (`chase_prior`), que una corrida entregada nunca reinicia; sólo la reinicia un
comentario humano. Acota un lazo de cuota por modelo: un rol barato entrega corridas (y
abre ventanas nuevas) mientras otro rol sigue chocando con su 429. Costo aceptado: unas
26 h y unos 192 comentarios del sistema antes de preguntarle al humano. Es un juicio, no una
medición; es una constante con nombre. `rules.resume_exhausted` es la única decisión de
agotamiento de un retome (calendario gastado, tope de la cadena o techo de entrevista) y
`poll_plan` la usa tanto para filtrar los vencidos como para decidir si el retome sigue
bloqueando lanzamientos. `ResumeCandidate.limit` cuenta sólo slots de ventana y nunca es 0
como señal.

### El techo de las entrevistas

El límite de 1 de ADR-0039 existe porque cada retome que corre le da a la entrevista hasta
`INTERVIEW_RETAKE_ROUNDS` rondas más. Con un calendario de 15 minutos ese 1 casi siempre cae
dentro de la misma ventana y entrega la tarea a un humano sin que el retome corriera nada.
Para `account_limit`, `auto_resume_limit` da ahora los 24 slots, entrevista o no, y el techo
se gasta sólo con retomes *productivos* (`rules.interview_ceiling_spent`): uno es productivo
si una ronda de la entrevista (`rules.interview_rounds`, el único filtro de `state["runs"]`
que decide qué es una ronda, el mismo de `interview_progress`, cuyo `rounds_done` no
cambia) empezó entre el `createdAt` de su `Auto-resumed:` menos la tolerancia y el del
siguiente (la última ventana queda abierta). Un 429 que llega en el turno 1 no corre ninguna
ronda y no gasta el techo; con un solo retome productivo el techo está gastado y la tarea
pasa al humano. El techo no desaparece. Para `upstream` y `network` el límite sigue siendo 1.

### Épocas mixtas

El motivo del último fallo de la época elige el calendario. El `prior` de `upstream` y
`network` sigue acumulándose entre épocas como hasta ahora, de modo que un fallo `upstream`
después de una cadena larga de `account_limit` encuentra su calendario creciente ya gastado
y va directo a un humano. Se acepta.

### Costos aceptados

- **Período observado**: unos 16–17 min por intento (15 min más lo que corre el sondeo
  fallido más los 60 s de la vuelta del poller), así que 24 slots cubren unas 6.5 h.
- **Lanzamientos bloqueados**: se mantiene lo decidido arriba (un retome que espera bloquea
  los lanzamientos, porque un 429 es de la cuenta y no de una tarea): una sola pausa puede
  retener la cola unas 6.5 h. Un lanzamiento en esa ventana chocaría con el mismo 429.
- **Comentarios**: hasta dos comentarios del sistema por intento en el issue
  (`Auto-resumed:` y `Escalated:`).
- **Desfase de reloj**: el desfase entre el `started_at` del hijo y el `createdAt` de GitHub
  sólo está acotado por la tolerancia, no garantizado; todo error resuelve hacia gastar.

Nada observable se mueve: ningún métrico, etiqueta, atributo de span, clave de documento,
prompt ni texto de comentario se agrega, renombra o quita.

## Adenda (2026-10-05): `fetch` sí, y la base de cada corrida es el upstream (#180)

### Lo que cambia en "Lo que el poller no hace"

"Nunca `fetch`, `pull`, `checkout` ni `reset`" pasa a ser: **`fetch` sí; `pull`, `checkout`,
`reset`, `merge` y avance rápido del checkout del producto, nunca** (ADR-0002, segunda
adenda: el humano puede estar editando ese checkout mientras corre una tarea). `git fetch`
sólo escribe refs de seguimiento remoto y objetos. Sólo `GitWorkspace.upstream()`, a través
de su `_fetch` privado, lo corre; ningún otro módulo toca la red.

### La evidencia

2026-10-05, apostrophe: el poller lanzó GH-45, GH-46 y GH-47 entre las 14:17 y las 14:24,
las tres con `agenticBase` `6cc7bb2`, mientras `origin/master` ya tenía `f08013c` (#88) y
`78adf48` (#89, GH-58, los diseños de `docs/product/design-system/pantallas/`) y el checkout
no se había actualizado. El PM de GH-47 escaló preguntando por los diseños; GH-45 y GH-46
corrieron sin ellos; las tres se reiniciaron con `--fresh`. El riesgo "un checkout local
atrasado no lanza hasta que el humano hace pull" ya costó corridas reales, y del lado de la
corrida nadie lo había aceptado.

### La regla de base

- **Una rama de tarea que se crea** (y sólo entonces) parte del upstream de la rama actual
  del checkout, `@{upstream}` tras un `fetch` del remoto configurado, no del `HEAD` local;
  `agenticBase` registra ese commit. Una rama que ya existe nunca dispara un `fetch` ni
  cambia de base; un checkout de subtarea nunca hace `fetch` (parte de la rama de la tarea).
- **Sin remoto, sin upstream, `HEAD` suelto o un upstream local (`remote = .`):** `HEAD`,
  como hoy, con una nota.
- **`HEAD` con commits que el upstream no tiene:** la base es el upstream (la entrega es un
  PR contra el remoto) y la nota nombra los commits que quedan fuera por sha corto (hasta 10
  y "and N more").
- **El `fetch` falla o vence** (30 s, `FETCH_TIMEOUT_S`; se mata el grupo de procesos entero,
  sin stdin, `GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS` vacío): se usa el último ref traído
  *sólo* si `HEAD` está atrás de él o divergió; sin último ref, o con `HEAD` por delante, se
  usa `HEAD`. **Esto se aparta a propósito de la letra del pedido** ("caer al último ref
  traído si lo hay"): un `fetch` fallido no aprendió nada nuevo del remoto, así que sin red
  el comportamiento queda igual al de hoy en vez de pasar a descartar commits locales por un
  ref viejo. La nota da la razón.
- **El poller** juzga el commit de merge de un bloqueante contra ese mismo sha
  (`is_ancestor(..., ref=sha)`), con un `fetch` por vuelta como máximo (`functools.cache`) y
  ninguno en una vuelta sin un bloqueante cerrado con commit de merge. La nota va como una
  línea de la salida de la vuelta. Ante la duda sigue sin lanzar: un objeto desconocido es
  `False`, y un `sha` ausente cae a `HEAD`.
- Todo falla a una nota, nunca a una excepción: `upstream()` devuelve siempre un `Upstream`.
  La nota de una corrida va a stderr, que en una corrida desacoplada es su log.

### Convivencia con ADR-0038

La revisión de stack sigue leyendo `HEAD` (no `origin/HEAD`), y `preflight`, `stack.product()`
y los avisos del cli también: esta adenda no los mueve. Lo que pasa por `checkouts.task_base`
(checks, setup, witness) y por la raíz del checkout (conocimiento) sigue la base nueva sin
cambios de código.

### Lo que cuesta

`registry.exclusive()` es un `flock` bloqueante sin timeout, y el `fetch` corre dentro de
`gather_launches`: en una vuelta que juzga un bloqueante puede retener el lock hasta ~30 s más
el fallback. `REGISTER_WAIT_S = 60` corre en `execute`, después del `fetch`. Del lado de la
corrida, `cli._registered` envuelve `_invoke` (el primer nodo que pide un checkout), de modo
que el hijo ya está registrado cuando hace su `fetch`: la ventana de registro no se come el
`fetch` de la corrida. Un `fetch` por creación de rama de tarea.

### Riesgos nuevos

- Los lectores que siguen en `HEAD` (`preflight`, `stack.product()`, avisos del cli, revisión
  de stack): un `stack.yaml` roto que sólo existe en origin escapa a `preflight` y falla
  dentro de un nodo si el checkout está atrasado.
- Refspec angosto: un clon de una sola rama, o un `branch.<b>.merge` fuera del refspec del
  remoto, puede dejar `@{upstream}` viejo tras un `fetch` "exitoso", sin nota.
- Lock: un `fetch` lento retiene el lock del poller de la máquina hasta ~30 s.
- Un `fetch` exitoso deja fuera de la base los commits locales que el upstream no tiene; es
  lo buscado y la nota los nombra.

## Adenda (2026-10-06): los pollers y las corridas sobreviven a un reinicio (#176)

El 2026-10-05 la máquina estuvo apagada de 08:36 a 12:16 UTC: mató tres corridas y los dos
pollers, y nada volvió solo. Esta adenda registra lo que se cambió para que un reinicio no
exija recuperación manual. Ningún cuerpo ni adenda anterior se edita.

### El id de arranque y la vida de un registro

`/proc/sys/kernel/random/boot_id` es el id del arranque (glosario: **Arranque**). Lo lee un solo
módulo, `adapters/filesystem/liveness.py` (`current_boot_id()`), y ese mismo módulo tiene la única
sonda de pid, `is_alive(pid, recorded_boot, current_boot)`. `FilesystemRunLock` y
`FilesystemRunRegistry` escriben el id en cada registro y reciben el actual por un callable de
constructor, de modo que ningún test toca `/proc`. El veredicto es puro: `domain/boot.py:
boot_verdict(recorded, current)` da `other_boot`, `same_boot` o `unknown`. Un registro de otro
arranque está muerto, diga lo que diga `os.kill`; uno sin id (anterior a este cambio, o una máquina
sin `/proc`) es `unknown`: decide `os.kill`, como hasta ahora, y el poller nunca lo retoma. El poller
no importa `liveness` (la arquitectura lo prohíbe a `application/`): pregunta la vida al propio lock
con `RunLock.refusal`, que usa la misma `is_alive` que `acquire`, así que ambos no pueden discrepar.
Un lock de otro host se lee como vivo, igual que en `acquire`.

### La marca de apagado

Cuando `_signal_guarded` recibe SIGTERM o SIGHUP corre una sonda (`systemctl is-system-running`,
2 s de timeout, verdadera sólo si la salida es exactamente `stopping`) antes de cancelar. La
respuesta llega a la liberación del lock como un valor por invocación (`StopCause`), nunca como
estado de módulo. Si es verdadera, `RunLock.keep_at_shutdown` reescribe el registro propio con
`stopped_at_shutdown: true` y su id de arranque; en cualquier otro caso el lock se libera como
siempre. Como `acquire` ya toma un registro muerto, conservarlo no filtra el lock (glosario:
**Parada por apagado**).

### La regla de retoma y su tope

`rules.interrupted_run(raw, lock, prior_retakes)` decide, pura, junto a `poll_plan`. No es una
corrida interrumpida si no hay registro de lock, o si el documento tiene `closed_at`, `escalated` o
`pull_request`, o su `status` es `ready_for_review`, `discarded` o `waiting_on_human`. Si el lock
está vivo, sigue corriendo. Con el lock muerto: de otro arranque y con menos de
`RETAKE_AFTER_REBOOT_MAX = 3` retomas previas, `retake`; en cualquier otro caso (mismo arranque,
arranque desconocido, un documento sin `status`, o el tope gastado) es del humano.
`poll.gather_retakes` lee el lock con `RunLock.read` (nunca `acquire`) antes de que algo lance, de
modo que la decisión se toma sobre el registro tal como lo dejó la corrida muerta. Las retomas
previas son los comentarios `Retaken after reboot: ` posteriores al último comentario que no es del
sistema; el prefijo (`poll.RETAKEN`) está en `resume.SYSTEM_COMMENTS`, así que un comentario humano
reinicia la cuenta y `agentic resume` nunca lo lee como decisión. `poll_plan(retakes=...)` pone las
retomas después de los cierres y antes de las reanudaciones vencidas, y gastan los mismos
`available_slots` que cualquier lanzamiento (el tope de `AGENTIC_MAX_CONCURRENT_RUNS`).
`poll.execute` tiene ahora cuatro ramas explícitas (`close`, `resume`, `retake`, `launch`) y termina
en `assert_never`; `resume` y `retake` comparten `_spawn_resume`: lanzan `agentic resume --issue N
--yes --detach`, comentan sólo después de que `spawn` devolvió un pid, y esperan el registro. Una
retoma nunca llama a `record_launch` ni a `launch_attempts`. Una corrida del mismo arranque que
murió (la mató un humano, o se cayó) se deja al humano con una línea por vuelta:
`#N died in this boot (lock pid P since T), left to the human: agentic resume --issue N`
(`stopped at shutdown` si el registro tiene la marca; `retake cap spent` si fue el tope; `died (boot unknown)` si el registro no trae id de arranque).

### Unidades, reinicio y linger

`scripts/install-poller.sh` renderiza `scripts/systemd/agentic-poll.service.in` en una unidad de
usuario por `--product-dir`, bajo `~/.config/systemd/user`, sin raíz. La unidad lleva rutas
absolutas, ningún especificador `%`, `WorkingDirectory=<stage0>` (para que `load_dotenv()` lea
`.env`) y un `PATH` resuelto al instalar con `git`, `gh`, `uv` y `claude` (si falta uno, la
instalación falla). Política: `Restart=on-failure`, `RestartSec=30`, `StartLimitIntervalSec=0`,
`KillMode=process` (los hijos desprendidos de `run`/`resume` no mueren con el poller al
reiniciarlo). El humano corre una vez `loginctl enable-linger $USER` para que arranquen al
encender sin sesión; el script imprime la instrucción.

### El inhibidor

`run` y `resume` toman un lock de inhibición de logind (`systemd-inhibit --what=sleep:shutdown
--mode=block`, sostenido por un pipe de stdin) mientras viven, dentro de `_locked` y `_registered`,
de modo que hay un solo pid. Si no está disponible, escribe una línea en stderr y la corrida sigue.
No detiene un apagado forzado ni un corte de luz: eso lo cubre la retoma.

### Lo que no cambia

`.agentic/stack.yaml`; los prompts y su `fingerprint`; ninguna métrica, etiqueta, atributo de span
ni clave del documento de tarea (los únicos campos nuevos persistidos son `boot_id` y
`stopped_at_shutdown`, en el archivo de lock y el de corridas vivas, leídos con `.get`);
`EscalationClass`; `ROLE_RETRIES`; el tope de 10 USD por intento y el tope de tarea.

### Lo que cuesta (§8)

Una retoma reejecuta el nodo interrumpido con el `resume --yes` existente: como mucho 3
reanudaciones extra por tarea abierta y por decisión humana, cada una bajo los mismos topes. El
costo no crece con el tamaño de los datos. Una retoma rechazada antes de registrarse retiene el
`flock` del registro hasta 60 s, como mucho 3 vueltas por tarea; se informa como `retake #N -> not
registered (retake k of 3)`.

### Seguridad (§8)

El script escribe unidades de usuario y las habilita con `systemctl --user`: no necesita raíz. Una
corrida viva ahora puede bloquear la suspensión y el apagado ordenado del usuario; un apagado
forzado lo supera.

### Riesgos nuevos

- Un pid reutilizado dentro del mismo arranque sigue leyéndose vivo. Registrar el tiempo de inicio
  del proceso lo cerraría; no se pidió.
- Si el bus del sistema ya cayó cuando llega SIGTERM, o el administrador de usuario se detiene sin
  apagado del sistema, el lock se libera y la tarea no se retoma.
- La línea del mismo arranque se repite en cada vuelta: el poller no guarda estado.
- Una retoma rechazada antes de registrarse retiene el `flock` del registro hasta 60 s, como mucho
  3 vueltas por tarea.
- Dos pollers sobre un mismo producto se reinician cada 30 s hasta que se detenga uno.
- `deliver` ejecutado dos veces cae al humano: `gh pr create` rechaza una cabeza ya existente, así
  que no crea un pull request duplicado, pero la tarea queda del humano. Hacer que encuentre el pull
  request existente es un cambio aparte, nombrado en el cuerpo del PR.
- Un lock de otro host con un id de arranque ajeno se lee como vivo (`refusal` no lo sondea): nunca
  se retoma desde otra máquina.


## Adenda (2026-10-06): una segunda corrida lanza solo si el gasto de las últimas 5 h va bajo un ritmo sostenible (#177)

### Evidencia

`AGENTIC_MAX_CONCURRENT_RUNS` es un tope fijo, pero lo que limita el rendimiento es el gasto de la
cuenta por ventana de 5 horas. La adenda del 2026-10-05 (17 tareas, 41,2 h) vio los cortes en
$177, $184, $200 y $163 por ventana, es decir ≈ $33–40/h sostenibles.

- Las tareas grandes ya superan ese ritmo en serie: $50–57/h (apostrophe #58, $181, 9 subtareas).
  En paralelo solo adelantan el 429: 3–5 corridas gastaron $100–195/h y chocaron con el límite a
  las 1,5–1,8 h, contra 2,9 h en serie.
- Las tareas chicas corren en serie a ≈ $30/h (#65: $8 en 16 min; #67: $9 en 19 min), así que la
  ventana se reinicia con presupuesto sin usar (lo informado en el issue #177). Estimación: hasta
  +20–25 % de tareas por ventana si se usa esa holgura.

La regla correcta no es «N a la vez» sino «otra corrida solo mientras el gasto reciente va bajo un
ritmo sostenible».

### Decisión

- Solo se regula `launch`. Un cierre, una retoma tras reinicio o una reanudación nunca se frenan.
- La primera corrida nunca se frena: con ninguna corrida viva (ni lanzada antes en la misma
  vuelta) el lanzamiento sale aunque el ritmo esté superado. Solo se regula la segunda en adelante.
- Una pausa que aún espera su calendario sigue bloqueando todo lanzamiento, como hoy.
- Apagado por omisión: `AGENTIC_SPEND_PACING=true` lo enciende. `max_concurrent_runs` sigue como
  tope duro y con su valor 1. Con el interruptor apagado `poll_plan(pace=None)` devuelve lo mismo
  que antes, no se lee ni se escribe ningún archivo de gasto y la salida de la vuelta es idéntica.
- El ritmo (`AGENTIC_SPEND_PACE_USD_PER_H`, 35) es un valor de partida tomado de una muestra de
  41 h, no una constante medida. Se revisará con `agentic eval compare` contra la primera línea
  base que corra con el ritmo encendido.
- Cada poller lee solo los documentos de su propio producto; el gasto de los demás llega
  únicamente por `run_registry_dir/spend/`. Ningún camino lee el directorio de otro producto.

### Archivos de gasto

Cada poller publica `spend/<12 hex del sha256 del directorio de documentos resuelto>.json` en
`run_registry_dir`, con `product`, `root` (para la persona), `written_at` y `runs` como lista de
`[started_at, ended_at, cost_usd]`. La clave es el directorio, no el nombre del producto: dos
checkouts con el mismo nombre se cuentan los dos. La escritura es atómica (archivo temporal en el
mismo directorio y `os.replace`) y ocurre bajo `registry.exclusive()`. `spend/` solo se crea al
publicar, así que `live()` (glob `*.json` del nivel superior) y `launch_attempts` (rutas exactas bajo
`launches/`) no lo ven.

La fusión (`rules.merged_spend`) clava cada corrida en `(started_at, ended_at)`: el costo recién
publicado reemplaza al ya publicado, y una corrida publicada que ya no está en los documentos se
conserva mientras siga dentro de la ventana. Archivar, cerrar o `--fresh` nunca bajan el gasto
reciente y ninguna corrida se cuenta dos veces. Solo se suma `RoleRun.cost_usd`; `subagent_cost_usd`
y `failed_cost_usd` no se leen. El gasto es el costo prorrateado por el solape con `[ahora-5 h,
ahora]`, dividido por el tiempo cubierto (mínimo 30 min, máximo 5 h).

### Filtro por `mtime` y su costo

Cada vuelta hace un `stat` por documento propio y solo parsea los tocados en las últimas 5 h, todo
fuera del candado. Bajo el candado cuesta una lectura-fusión-escritura de su propio archivo más una
lectura por producto con poller.

### Reglas de marcas de tiempo

Toda fecha que reciben las reglas es consciente de zona horaria y en UTC. Una corrida sin
`ended_at`, o con uno ingenuo o ilegible, se omite y se cuenta en una línea
(`N runs without timestamps not counted`). Un archivo de gasto ilegible, o una entrada ingenua o mal
formada, se omite con una nota y nunca lanza.

### Riesgos conocidos

Todos se equivocan hacia lanzar; el tope y el calendario del 429 siguen de respaldo.

- Un rol aún corriendo no registró su `cost_usd`.
- El gasto de intentos fallidos que escalaron sin `RoleRun` no se cuenta.
- Un producto corrido a mano sin poller en esta máquina no se cuenta.
- La cifra de cada producto es tan fresca como la última vuelta de su poller.
- Un archivo de gasto ilegible se omite con una nota.
- Una corrida sin `ended_at` utilizable se omite y se cuenta.

### Lo que no se mueve

Ninguna métrica, etiqueta, atributo de span, clave de documento, prompt ni `prompts.fingerprint` se
agrega, mueve o renombra. Los archivos de gasto viven en `run_registry_dir`, fuera de todo
repositorio, y solo con el ritmo encendido.

## Adenda (2026-10-22): el poller retoma una corrida que cayó por una excepción del grafo (#308)

### Evidencia

La primera corrida de GH-295 cayó en la planificación (`ValueError: ... no value for placeholder
{{cost_history}}`). El documento quedó en `status: open`, sin `escalated`, sin corrida viva y
sin lock. `gather_launches` la salta porque el documento existe, `gather_resumes` porque no
está escalada y `gather_retakes` porque no hay lock: la tarea quedó invisible hasta que un
humano corrió `agentic resume`.

### Decisión

- **La huella**: una excepción del grafo deja el registro del lock marcado
  (`RunLock.keep_crashed(task_id, system_version)` escribe `crashed_version`, con el id de
  arranque intacto). La escribe `cli._locked`, en su `finally`, solo si `_invoke` marcó
  `StopCause.crashed` (únicamente para lo que sale de `app.ainvoke`) y `StopCause` no registró
  señal ni apagado. `keep_at_shutdown`, `keep_crashed` y `release` son excluyentes, con ese
  orden de prioridad. Una señal, `CannotStart` o una excepción fuera del grafo siguen
  liberando el lock.
- **Quién decide**: `rules.interrupted_run`, la única. Da `retake_crash` si el registro tiene
  huella (se evalúa antes de `alive`: la huella prueba que el proceso terminó), la clave de la
  versión actual no es `None` y ambos topes siguen sin gastar. El poller solo le pasa hechos.
- **Los dos topes y su clave**: como mucho `CRASH_RETAKE_PER_VERSION_MAX = 2` retomas por
  versión de stage0 y `CRASH_RETAKE_TOTAL_MAX = 6` en total, ambos contados desde el último
  comentario que no es del sistema. La clave es `rules.crash_version_key` de la versión que la
  vuelta leyó con `telemetry.fresh_system_version` (inyectada por `cli`, sin memo): el sha sin
  `+dirty`. `unknown` no produce retoma nunca: la tarea queda al humano, con su línea, sin
  gastar tope. Con un tope gastado y un registro de otro arranque, cae a la retoma por reboot.
- **El comentario**: `Retaken after crash: ` (registrado en `resume.SYSTEM_COMMENTS`) más el
  texto de `rules.crash_retake_comment`, que dice la versión en que cayó y la clave bajo la
  que reanuda; el mismo módulo lo escribe y lo cuenta. `gather_retakes` cuenta una sola vez y
  `execute` recibe el conteo en `CrashRetake`.
- **Por qué no una `EscalationClass` transitoria**: ADR-0029 cierra la lista de clases, y
  escribir el documento en plena excepción pisaría lo que los nodos ya escribieron. El lock
  no tiene ninguna de las dos objeciones.

### Riesgos conocidos

- **Checkpoint del código viejo**: la primera retoma tras un arreglo corre sobre un checkpoint
  escrito por el código anterior; si falla distinto, gasta una de las 2 retomas de la versión
  nueva.
- **Ventana al salir**: entre escribir la huella y que el proceso salga, el `acquire` del hijo
  puede ver el pid vivo y lanzar `LockHeld`. Esa retoma cuesta un comentario y se reintenta la
  vuelta siguiente.
- **Versión fijada por entorno**: con `AGENTIC_SYSTEM_VERSION` fijada, la clave no cambia nunca
  y el conteo por versión solo lo reinicia un comentario humano.
- **Contador de reboot**: sigue calculándose dos veces (en `gather_retakes` y en `execute`);
  es deuda nombrada y no se toca aquí.

### Costo

Cada retoma repite solo el nodo que cayó (reanuda desde el checkpoint) y ocupa una plaza de
`available_slots`: el peor caso son 6 repeticiones por comentario humano, independiente de los
merges. El poller no corre nada nuevo en una vuelta sin huellas.
