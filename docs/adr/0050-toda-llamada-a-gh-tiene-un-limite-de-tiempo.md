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
