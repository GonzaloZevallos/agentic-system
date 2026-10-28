---
status: accepted
date: 2026-10-23
---

# QA Performance, parte 1: el sistema mide, `agentic perf` lo corre sin tarea ni rol

GH-318 pide que el sistema pueda medir un producto contra presupuestos de rendimiento con un
volumen de datos realista. Esta primera parte entrega la medición y el verbo; la segunda
(el rol QA Performance) se apoya en ella.

## Decisión

- **QA Performance se sostiene sin QA Lead.** Es una salida del README: el camino de la
  plataforma "cierra solo con la aprobación de QA" sigue sin hacerse cumplir hasta que QA
  Lead exista. Esta parte no cambia ningún verbo, el presubmit, la compuerta de merge ni el
  aterrizaje.
- **El sistema mide y el rol lee.** El comando `measure` del producto imprime valores y el
  volumen que vio, como última línea, un objeto JSON
  `{"volume": n, "volume_unit": u, "metrics": {nombre: número}}`. Nunca juzga: el único
  lector de esa línea es `performance.parse_line` y el único lugar donde un valor se
  encuentra con un límite es `performance.verdict`.
- **Presupuestos absolutos**, declarados por el producto en la clave opcional `performance`
  de su `.agentic/stack.yaml`. Nunca se leen de `checks`; sin la clave el resultado es `{}` y
  todo producto existente carga como hoy. El sufijo de cada métrica coincide con su unidad
  (`_ms`, `_queries`, `_kb`).
- **Tres resultados y cinco razones.** `within`, `over` o `inconclusive`; este último siempre
  con una razón de `spread | broken | timeout | busy | volume`. Un volumen eco ausente,
  menor o en otra unidad hace `inconclusive/volume` todo el escenario.
- **Percentiles de rango más cercano.** Con n = 5 o 9, la dispersión (p90 - p10) / mediana es
  el rango sobre la mediana, no una banda de percentiles.
- **Reintento solo por `spread`:** una vez, con una nueva siembra y 9 corridas que
  reemplazan las primeras 5. `broken`, `volume`, `timeout` y `busy` son finales.
- **Códigos de salida:** 0 todo `within`, 1 algún `over`, 2 algún `inconclusive`, 3 rechazo
  (nada se midió). Con resultados mezclados gana `inconclusive`, luego `over`.
- **Arnés en HEAD, código en `--revision`.** Los escenarios, el volumen, los límites y los
  scripts son los del HEAD del producto; el código medido es `--revision` (HEAD por
  defecto). `--overlay PATH` (repetible) copia esa ruta del HEAD al checkout medido; sin
  él, los comandos deben bastarse con la revisión medida.
- **Ejecutor depurado.** `PerfShellRunner` corre cada comando con solo `PATH`, `LANG`/`LC_*`,
  `TZ`, el venv del checkout, `PERF_VOLUME`, `PERF_VOLUME_UNIT` y la marca del reaper; `HOME`
  y `TMPDIR` en un directorio de rascado que se borra en `finally`; el checkout como cwd y su
  propia sesión. Garantía exacta del reap: se detiene lo que lleva la marca del comando, o lo
  que no la lleva y tiene su cwd dentro del checkout, en el momento del reap.
- **Un lock por máquina** (`FilesystemPerfLock`, con boot-id y pid como los demás locks): dos
  mediciones no comparten CPU. Si no se obtiene en `perf_lock_wait_s`, todo escenario es
  `inconclusive/busy`, sin worktree.
- **Worktree desechable** `perf-<uuid>`, quitado en `finally`. Al tomar el lock se barren los
  `perf-*` que un kill duro dejó (se detienen sus procesos por cwd y se quitan).
- **Presupuesto del nodo.** `perf_node_timeout_s` (3600 s) acota la medición una vez que se
  tiene el lock, y `perf_timeout_s` (1800 s) cada comando, topado por lo que queda; un
  escenario que no alcanza a empezar es `timeout (not started)`. La espera del lock viene
  antes y solo la acota `perf_lock_wait_s`: el tiempo total de la invocación es, como mucho,
  `perf_lock_wait_s + perf_node_timeout_s` (5400 s por defecto). Un timeout nunca es una
  excepción.
- **Sin observabilidad nueva:** `agentic perf` no agrega span, métrica ni etiqueta (§7).

## Seguridad y costo (§8)

El sistema ejecuta, como el usuario del host, los `seed`, `measure` y `setup` que el
producto declara. Reciben el checkout de un worktree desechable, un home de rascado y la red
abierta, pero ninguna credencial del sistema. Solo corren cuando una persona invoca
`agentic perf`. Costo por invocación: un setup y, por escenario, una siembra y 5 a 14
mediciones, acotado por `perf_node_timeout_s` una vez tomado el lock, más a lo sumo
`perf_lock_wait_s` de espera; no se llama a ningún modelo.

`--revision` y `--overlay` los usa quien corre el CLI, como el usuario del host. `--revision`
alcanza cualquier commit del repo del propio producto. `--overlay` copia solo rutas
confirmadas en el HEAD del producto (`git archive`), tras rechazar las rutas vacías,
absolutas, con `..`, fuera del producto o ausentes del HEAD; la extracción usa
`filter="data"`, que rechaza miembros o enlaces que caigan o apunten fuera del checkout.
Ningún rol puede alcanzar ninguno de los dos en esta parte.

## Riesgos conocidos

1. `command_refusal` compara cadenas: no es un sandbox, y la red sigue abierta.
2. Un hijo que borra el entorno (`env -i`) y sale del checkout (`cd /`) sobrevive al verbo;
   una prueba fija esa brecha.
3. Un kill duro deja un worktree `perf-*` hasta la siguiente ejecución de `agentic perf` de
   ese producto.
4. `perf_spread_max = 0.25` no está calibrado: con n = 5 mide un rango. Lo fija la corrida
   humana sobre un commit viejo de apostrophe con un N+1 conocido, y su valor va en el PR.
5. Un `measure` que imprime segundos bajo una clave `_ms` da un `within` silencioso.
6. La carga del sistema se lee antes y después de cada corrida y es solo informativa.

## Addendum (GH-385): el stop de la corrida alcanza a perf

Un stop (SIGTERM o SIGHUP) durante QA Performance esperaba al hard deadline y terminaba en
`os._exit`: `measure_at` corría en el event loop, el runner y el lock de perf no miraban la
`StopFlag` y nadie reapeaba `PERF_TASK`. Ahora:

- `PerfShellRunner` y `FilesystemPerfLock` reciben la `StopFlag` de la corrida y levantan
  `RunStopped`; el verbo `agentic perf` les pasa `NullStopFlag()` y no cambia.
- `stop_run`, `hard_stop` y `release_processes` reapean `reaping.RUN_SCOPED`
  (`CHECK_TASK` y `PERF_TASK`), nunca un literal.
- El nodo mide con `measure_at` en un hilo (`anyio.to_thread.run_sync`), así el loop atiende
  la señal y el `finally` de `measure_at` borra el worktree `perf-*` y libera el lock. El
  nodo no escribe nada: no hay tabla, ni run, ni `perf_verdict`, y `qa_performance_due`
  sigue verdadera.

Costo: los reaps son secuenciales y suman a lo sumo
`|RUN_SCOPED| × (REAP_GRACE_S + REAP_KILL_WAIT_S)` = 2 × 7 s = 14 s antes de que corra el
reloj del hard deadline (hoy 7 s); es constante y solo ocurre con procesos que ignoran SIGTERM.

Brechas conocidas, fuera de alcance: el hijo con `env -i` y `cd /` sigue sobreviviendo, sin
cambio; y `FilesystemPerfLock._remove_dead` toma el lock de un dueño muerto sin reapear sus
hijos perf.
