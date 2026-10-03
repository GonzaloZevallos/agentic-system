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
