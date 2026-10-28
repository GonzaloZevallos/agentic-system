---
status: accepted
date: 2026-10-16
---

# Toda llamada a `gh` tiene un límite de tiempo

El 2026-10-16 (~11:47Z) los dos pollers dejaron de escribir durante ~37 minutos. Cada uno
tenía un hijo `gh` colgado (`gh issue view 229 …` y `gh pr list --state all …`), dormido
en `poll_schedule_timeout`. Las llamadas de `GhTracker._gh`, `GhInbox.task` y
`GhInbox.open_issues` no pasaban `timeout=`: una lectura colgada bloqueaba la ronda para
siempre, con el flock del registro tomado y sin una línea en el journal.

## Decisión

- **Un solo ejecutor.** `adapters/github/run.py` es el único lugar que arranca `gh`.
  `run_gh(run, args, …)` llama a la `run` inyectada con los mismos argumentos de siempre
  (sin timeout), traduce `FileNotFoundError` y `CalledProcessError` al mismo `ValueError`
  de cada llamador, byte por byte, y traduce `TimeoutExpired` en `TrackerTimedOut` tras
  escribir exactamente una línea por expiración: `agentic: gh <args[0]> <args[1]> timed
  out after <N> s; this read fails and the next round retries`. La línea nombra la llamada
  y el límite, nunca stderr ni contenido del issue.
- **Drenaje acotado.** `bounded_run` (el `run` por defecto de `GhTracker` y `GhInbox`) lanza
  `Popen(start_new_session=True, stdin=DEVNULL)` y espera con `communicate(timeout=…)`. Si
  expira: `killpg(SIGKILL)` y `communicate(timeout=GH_DRAIN_S)`. Si el drenaje también
  expira (algo que salió del grupo retiene los pipes) cierra ambos pipes,
  `wait(timeout=GH_DRAIN_S)` y re-lanza el `TimeoutExpired` original. No escribe nada. Fuera
  de la expiración mantiene el contrato de `subprocess.run`. Es la forma de
  `Workspace._fetch` y `ShellCommands.run`, que conservan su copia.
- **`TrackerTimedOut(ValueError)`, declarada en `application/ports.py`.** Es un
  `ValueError` para que los manejadores que ya tratan una lectura ilegible como inocua la
  absorban sin edición (`_blocked`, `gather_closes`, `assess_merge`, reflect,
  `landing_plan`, `landing`/`merge`, `deliver`). Vive en el puerto para que `application/`
  y `cli.py` la nombren sin importar un adaptador.
- **Cláusulas estrechadas.** `issue_state` re-lanza `TrackerTimedOut` antes del fallback de
  "`gh` viejo sin `blockedBy`": un timeout nunca se lee como `gh` viejo ni da
  `blocked_by=None`. `GhTracker.merge`, paso `pr merge`: un `TrackerTimedOut` es
  `MergeCallFailed("gh_unreadable")`, nunca `gh_rejected`; no hay valor nuevo de
  `MergeFailure`. Un merge que expira pero sí ocurrió sigue el camino existente: la
  relectura de `merge.py` lo encuentra `MERGED`, conserva `auto_merge_head` y `close` lo
  clasifica `auto_merged`; `--match-head-commit` hace seguro el reintento.
- **El poller.** En `cli._poll`, `except TrackerTimedOut` imprime `agentic: poll: round
  ended early: …` y el `sleep` corre igual; cualquier otra excepción escapa como hoy. En
  `poll._spawn_resume`, el comentario posterior al spawn atrapa solo `TrackerTimedOut`,
  agrega `… -> comment timed out` y sigue esperando el registro.
- **Constantes.** `GH_TIMEOUT_S = 60` y `GH_DRAIN_S = 2`, declaradas, no medidas; sin campo
  de `Configuration` ni variable `AGENTIC_*`, como `FETCH_TIMEOUT_S`.

## Enmienda a ADR-0034

ADR-0034 dice que "`GhInbox` conserva su `subprocess.run` inline … quedan distintos hasta
que otra tarea los unifique". Esta tarea los unifica: `GhInbox.task` y `open_issues`
pasan por `run_gh` y su `run` por defecto es `bounded_run`. No se edita ADR-0034.

## Consecuencias

- §7: ninguna señal se agrega, mueve ni renombra; ningún prompt cambia. La salida nueva son
  tres líneas de log por stdout.
- §8: el costo baja. Una llamada colgada retenía el flock sin límite; ahora cuesta a lo sumo
  `GH_TIMEOUT_S + 2×GH_DRAIN_S` = 64 s. La retención máxima en una ronda es el número de
  llamadas `gh` de `gather_launches` y `gather_merges` × 64 s; escala con los candidatos
  abiertos, no con el tamaño de los datos.

## Riesgos conocidos

- `escalate_persisted_limits` guarda la escalación y luego llama a `gh label`/`comment`: un
  timeout deja el documento escalado sin etiqueta ni comentario, el mismo medio estado que
  deja hoy cualquier fallo de `gh`.
- Un proceso que `gh` arrancó y salió del grupo (`setsid`, un daemon de credenciales) no se
  reapea: solo se acota la espera por él.
- Un paso de `landing_fix` que expira no se reintenta para esa cabeza (ADR-0048 ya lo
  registra para cualquier fallo de `gh`).
- 60 s está declarado, no medido: si un incidente real muestra que es largo, es una
  constante.

## Enmienda 2026-10-20 (GH-290): un `gh` que no llega a GitHub termina una ronda, no el poller

Incidente: tras un reinicio, antes de que subiera la red, el poller de apostrophe salió con
estado 1 cuatro veces seguidas por `ValueError: gh issue view failed: error connecting to
api.github.com`. systemd lo levantó, pero cada caída pierde el estado en memoria y la
tormenta de reinicios esconde los fallos reales del journal.

### Decisión

- **La clasificación vive en `run_gh`** (`adapters/github/run.py`), el único lugar que ve el
  código de salida y el stderr de `gh`. La ronda del poller, en `application/`, solo ve un
  `ValueError` ya compuesto y no puede clasificar.
- **Familia de excepciones** (`application/ports.py`): base nueva `TrackerCallFailed(ValueError)`;
  `TrackerTimedOut` pasa a ser su subclase sin cambiar nombre, módulo ni ascendencia
  `ValueError`; `TrackerUnreachable(TrackerCallFailed)` es nueva. Todo `except ValueError`
  existente absorbe ambas sin cambios. Los que terminan una ronda nombran la base.
- **Reconocimiento.** La constante `GH_UNREACHABLE_MARKERS = ("error connecting to",)` en
  `run.py`, comparada sin distinguir mayúsculas con el stderr. El código de salida 4
  (autenticación) nunca se clasifica como inalcanzable. Todo lo que no coincide sigue siendo
  el `ValueError` plano con el `failed(stderr)` de cada llamador, byte por byte.
- **Línea de log.** Una sola, por `log`: `agentic: gh <args[0]> <args[1]> could not reach
  GitHub (<marcador>); this read fails and the next round retries`. Nunca contiene stderr.
- **Capturado** con el `gh` fijado (2.100.0), salida 1: `GH_HOST=nonexistent.invalid gh api /`
  imprime `error connecting to nonexistent.invalid` y `check your internet connection or
  https://githubstatus.com`. Es la misma forma que la del incidente con `api.github.com`.
- **Quién atrapa.** `cli._poll` y `poll._spawn_resume` atrapan `TrackerCallFailed` (la línea
  del segundo es `-> comment timed out` para un timeout, sin cambios, y `-> comment failed:
  <exc>` para el inalcanzable). `GhTracker.issue_state` re-lanza `TrackerCallFailed` antes
  del fallback de "`gh` viejo". `merge.py` atrapa `TrackerUnreachable` antes de
  `MergeCallFailed` y `ValueError`.

### Excepción declarada a ADR-0029 y a la sexta enmienda de ADR-0004

Esto compara texto de `gh` para clasificar, lo que ADR-0029 prohíbe. Se acepta porque es el
transporte de una lectura del sistema, no una razón de fallo de un rol; porque falla hacia
lo ruidoso (un fallo sin marcador sigue matando al poller, como hoy); porque ocurre en un
solo lugar (`GH_UNREACHABLE_MARKERS`, y ningún otro módulo compara texto de `gh`); y porque
un marcador exige un fixture capturado del `gh` fijado: uno que falta cuesta un reinicio,
uno de más esconde un error real.

### `merge`: lo que cambia y lo que no se puede saber

- Un fallo de conexión en `repo view` (una lectura, antes de cualquier cambio) se propaga
  como `TrackerUnreachable`. `merge.py` retira la intención (`auto_merge_head: ""`), no
  escribe `merge_failures`, no comenta, y devuelve `stopped/unreachable`. Antes registraba
  `gh_unreadable` y una llamada contada; ahora no registra nada.
- Un fallo de conexión en `pr merge` (la única escritura) antes era `gh_rejected` con el
  consejo de protección de rama; ahora es `gh_unreadable`, contado contra `MERGE_CALLS_MAX`.
  Una caída en el paso de escritura gasta una de las tres llamadas de la cabeza: es el
  precio de no confundir una respuesta perdida con "no se mergeó".
- Solo `merge` distingue "no se hizo nada" de "quizá se hizo"; ningún otro código trata
  `TrackerUnreachable` como prueba de que una escritura (merge, comentario, cierre,
  etiqueta) no ocurrió.

### Frontera: dónde termina la ronda y dónde no

Una falla de conectividad termina la ronda solo donde ningún handler de `ValueError` queda
por encima de la llamada. Esos handlers no se editan y la línea de `run_gh` igual deja el
registro cuando uno la absorbe: `_blocked`, `gather_closes`, `assess_merge`, reflect,
`landing_plan` y `deliver`.

Llamadas `gh` de `run_once` sin handler de `ValueError` por encima (terminan la ronda):
`tracker.queue()` en `gather_launches`; `tracker.timestamped_comments` en `gather_resumes` y
en `gather_retakes`; `tracker.label` y `tracker.comment` en `escalate_persisted_limits`; las
lecturas de `GhInbox` fuera de `_blocked`.

### Riesgos conocidos

- **Un solo marcador.** Una frase de conectividad de `gh` que no hayamos visto sigue
  causando un reinicio (el statu quo). El marcador puede cubrir también casos `net.Error` de
  la fase de respuesta; el diseño no depende de eso porque el paso de escritura se trata
  como ambiguo.
- **Llamadas de red que no son `gh`.** `git fetch` en `upstream()` ya se vuelve una nota, y
  `fetch_commit` (sondeo de ADR y `gather_merges`) devuelve `False` o cae en `except
  GitError`. `push` y `ls-remote` no se alcanzan desde `run_once`: pertenecen a los hijos de
  entrega. Ninguna llamada de red que no sea `gh` queda sin absorber en la ronda.
- **Disciplina de marcadores.** Un marcador futuro necesita un stderr capturado del `gh`
  fijado.
- `escalate_persisted_limits` conserva su medio estado (documento escalado sin etiqueta ni
  comentario) si el fallo ocurre entre medio, como ya registra este ADR.

### Consecuencias

- §7: ninguna señal, etiqueta, atributo, clave de documento, valor de `MergeFailure` o
  `FailureReason`, ni prompt, se agrega, mueve ni renombra; la salida nueva son líneas por
  stdout.
- §8: nada nuevo se vuelve alcanzable. El costo por ronda no cambia; una caída de red
  cuesta una ronda fallida en lugar de un reinicio del proceso.

## Adenda (GH-389): reintentos acotados y opt-in

La corrida de GH-374 murió con un 502 de `gh issue comment`. Las reglas de arriba (falla
rápida, la próxima ronda reintenta) siguen siendo las del poller; los procesos que **son
dueños de una tarea** pueden pedir reintentos.

- **Opt-in.** `run_gh(..., retry: GhRetry | None = None)`. Sin política, todo es byte a byte
  lo de arriba: mismas excepciones, mismas líneas de `log`. Solo `run`/`resume` (tope de
  espera `GH_RATE_WAIT_MAX_S = 900`) y los hijos `merge`/`land` (`GH_EXEMPT_WAIT_MAX_S = 60`,
  para no retener el carril de las corridas exentas del tope) construyen el puerto con
  política (`default_ports(..., gh_retry=...)`). Poller, `inbox`, `close`, `system_repo()` y
  el resto de los verbos no cambian.
- **Qué se reintenta.** Hasta 3 llamadas con espera de 2 y 8 s (la tabla llega a 30 s si
  `attempts` sube): `GH_TRANSIENT_MARKERS` (hoy solo el 502 capturado del log de GH-374),
  `error connecting to`, el límite de tasa primario y, solo en lecturas, `TimeoutExpired`.
  El exit 4 nunca. Un stderr desconocido falla de inmediato.
- **Límite de tasa.** El reset sale de `gh api rate_limit`, llamado sin política: el mayor
  `reset` entre los recursos con `remaining == 0`. Si no se puede leer, backoff. Una espera
  mayor que el tope lanza `TrackerRateLimited` sin dormir.
- **Espera.** `sleep` y `now` se inyectan; se duerme en rebanadas de 1 s y se mira el
  `StopFlag` entre ellas: una bandera puesta corta la espera y relanza el tipo original.
- **Agotado.** `TrackerTransient`, `TrackerUnreachable`, `TrackerTimedOut` o
  `TrackerRateLimited` (todos `TrackerCallFailed`); con política, el rechazo de `gh` es
  `TrackerRefused(ValueError)`. Los mensajes nombran la llamada y la cota, nunca el stderr.
- **Qué escrituras.** Se reintentan lecturas, `comment`, `comment_on_issue`, `label`,
  `unlabel`, `ensure_label` y `update_pull_request_body`; **nunca** `open_issue`,
  `open_pull_request`, `merge`, `close_*` ni `rerun_failed_ci`. Queda aceptado que un
  comentario se duplique si el 502 llegó después de que se aplicó.
