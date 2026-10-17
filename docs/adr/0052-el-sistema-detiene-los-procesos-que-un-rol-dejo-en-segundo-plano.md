---
status: accepted
date: 2026-10-17
---

# El sistema detiene los procesos que un rol dejó en segundo plano

El 2026-10-17 el GH-224 de apostrophe se mergeó a las 00:13Z y 14 procesos que había
arrancado siguieron vivos hasta 25 horas bajo el cgroup del poller: `chain5.sh`,
`/tmp/GH-224-evidence/cap.sh`, tres `scripts/design/sample_app.py` con sus servidores
uvicorn, y los padres `uv run`, que ignoraban SIGTERM. El poller llegó a 19,6 G de memoria,
4,9 G en swap. Un proceso que un rol arranca con `nohup`, `setsid` o `run_in_background`
sale del grupo de procesos que el sistema controla, así que nada de lo que el sistema
tenía (`killpg`, el cierre del worktree) lo encontraba.

## Decisión

- **Marca de entorno.** Cada intento de un rol recibe una variable,
  `ROLE_PROCESS_OWNER=<clave de producto>/<id de la tarea madre>/<pid de la corrida>/<token
  del intento>`. La clave de producto son los primeros 12 hex del sha256 de la ruta
  resuelta del producto; el id es el de la tarea madre, nunca el de una subtarea; el pid es
  el del proceso del sistema que corre el intento; el token es `uuid4().hex`. Todo hijo la
  hereda, también los de `setsid` y los scripts fuera del worktree. Los procesos propios del
  sistema (el poller, los hijos `run`/`resume`, los checks del presubmit, `ShellSampleApp`)
  nunca la llevan. Solo va en el `options.env` de un intento, nunca en `os.environ`, en el
  `self.env` compartido del runner ni en `role_env`.
- **Fuera del prefijo `AGENTIC_*`.** Los depuradores de entorno
  (`own_configuration_unset`, `without_own_configuration`) quitan ese prefijo, y una marca
  `AGENTIC_*` se perdería en los nietos de un check que un rol corre.
- **Coincidencia por segmento.** `domain/reaping.py` es el único lugar que construye y
  parsea la marca, y el único archivo bajo `agentic/` donde aparece el literal (un test lo
  verifica, con su entrada rechazada). La variable se busca por nombre exacto en el bloque
  `KEY=VALUE` separado por NUL, nunca como subcadena; el valor se compara segmento a
  segmento: producto igual; tarea igual a la pedida o que empieza con ella más `.` (el
  alcance `GH-12` toma `GH-12.sub`, nunca `GH-1` ni `GH-123`); pid y token iguales cuando el
  alcance los fija.
- **Secuencia TERM→KILL por pidfd** (`adapters/shell/reaper.py`). Se escanea `/proc` y se
  toma la foto `(pid, starttime)` de los que coinciden; a cada uno se le hace
  `os.pidfd_open`, se revalida marca y starttime y se manda SIGTERM con
  `signal.pidfd_send_signal`. Nunca `os.kill` por pid pelado ni `killpg`. Se re-escanea
  hasta `REAP_GRACE_S = 5` (un hijo nacido durante la gracia hereda la marca y entra); lo
  que queda recibe SIGKILL y se espera hasta `REAP_KILL_WAIT_S = 2`. Resultado por proceso:
  `term` (murió en la gracia), `kill` (murió tras SIGKILL), `survived` (sigue vivo: una
  línea en stderr con el pid, sin el comando) o `error` (una excepción inesperada, con
  `pid` 0 y el nombre de la clase). Un `environ` ilegible (otro usuario, ya muerto) o un
  zombi se saltan: nunca se señalan ni se registran. Las constantes son del dominio:
  declaradas, no medidas; sin campo de `Configuration` ni variable de entorno.
- **Tres disparadores, tres alcances.**

  | Disparador | Alcance |
  |---|---|
  | Fin de cada intento de un rol (runner, tras cerrarse el stream del SDK) | solo ese intento: `<clave>/<tarea>/<pid>/<token>` |
  | Tarea liberada (`release_task`: deliver, discard, archive con `--fresh`, `reflect_run`) | toda la tarea: `<clave>/<tarea>/`, subtareas incluidas |
  | Corrida hallada muerta (`cli._run`/`_resume` al retomar un lock; el poller al dejar a la persona una corrida muerta) | solo esa corrida: `<clave>/<tarea>/<pid muerto>/` |

  Un veredicto de vida nunca apunta a toda la tarea (alcance de corrida). El reap va antes
  de `git worktree remove`, nunca después. Este ADR y su subtarea construyen el reaper; el
  cableado en el runner y el ciclo de vida es de las subtareas `runner-reap` y
  `lifecycle-reap`.
- **Puerto requerido.** `ProcessReaper` (`reap_task`, `reap_run`, `reap_attempt`; cada uno
  devuelve `tuple[ReapedProcess, ...]` y nunca lanza) es un campo requerido de `Ports` y un
  argumento requerido de quien lo consume: sin `None` por defecto ni versión no-op. El
  adaptador se construye con la clave de producto (la compone `composition.py`), así que los
  llamadores nunca la pasan. `GitWorkspace` no cambia: `cleanup` sigue sin leer `/proc`.
- **Dos registros, ausentes cuando están vacíos** (los documentos existentes se renderizan
  byte a byte igual): `reaped` por corrida o fallo, con `{pid, command, outcome}` y el
  comando cortado a 120 caracteres, y `reaped_at_cleanup` en la tarea. `RECORD_KEYS` no
  cambia.

## Opciones descartadas

- **Coincidir por cwd o por línea de comandos.** No encuentra los scripts de `/tmp` y
  alcanzaría los checks propios del sistema, que corren en el worktree.
- **Grupo de procesos.** `setsid` lo abandona: es justo el problema.
- **Un scope de cgroup por intento.** Ataría cada corrida a systemd y cambiaría lo que
  significa `KillMode=process` (ADR-0039).
- **Un barrido periódico del poller.** El lock ya decide la vida de una corrida; el tercer
  disparador lo usa.

## Observabilidad (§7)

No cambia ninguna señal: ni métrica, ni etiqueta, ni atributo de span, ni prompt, ni
`prompts.fingerprint`. Los únicos registros nuevos son las dos claves opcionales del
documento; el sistema escribe una línea más en stderr por sobreviviente.

## Seguridad y costo (§8)

- **Qué se ensancha.** El sistema puede enviar SIGTERM y SIGKILL a procesos de usuario que
  lleven la marca de un rol que él corrió, y las líneas de comandos de esos procesos (cortadas
  a 120 caracteres) llegan al documento de la tarea y al comentario de archivo. El permiso
  de señalar sigue siendo del sistema; `signals.py` sigue siendo la regla de lo que un rol
  puede señalar, no de lo que el sistema mata.
- **Costo.** Un escaneo de `/proc` (lineal en la cantidad de procesos de la máquina, no en el
  tamaño de los datos) por intento de rol, por liberación de tarea y por corrida muerta que
  el poller ve en cada ronda; más hasta 7 s de reloj en un hilo cuando hay algo que matar.

## Riesgos conocidos

1. **La compuerta.** Solo el spike verifica que la marca sobrevive al tool Bash del CLI
   real. Si falla, el reaper no sirve y la fuga sigue. Resultado en `## Gate result`.
2. **Escapes.** `env -i`, `systemd-run`, contenedores y procesos que cambian de privilegio
   pierden u ocultan la marca.
3. **Ventana tras un corte duro.** Una corrida matada por falta de memoria deja sus procesos
   vivos hasta la próxima ronda del poller, 60 segundos como máximo; un producto sin poller
   los deja hasta `--fresh`, `deliver` o `discard`. Un lock de otro host nunca se sondea.
4. **Reuso de pid.** Un pid reutilizado dentro del mismo arranque por una corrida nueva de la
   misma tarea, entre la lectura del poller y el reap, coincidiría.
5. **Secretos.** Una línea de comandos puede contener secretos. El corte a 120 caracteres lo
   limita, no lo elimina.
6. **El spike no se puede reproducir por quien revisa.** La salida pegada abajo es la
   evidencia; que falte la reproducción es una observación (ADR-0016).
7. **Un intento cancelado pierde su registro `reaped`.** El reap en sí corre hasta el final.

## Gate result

**Pasó.** `uv run pytest -m llm tests/llm/test_role_process_mark.py -v -s`, con el CLI
`claude_agent_sdk._cli_version.__cli_version__` = `2.1.281`, el SDK en `0.2.159` (el pin del
proyecto es `claude-agent-sdk>=0.2.159`), modelo `claude-sonnet-5-5` y un costo de
USD 0,009835.

Dos hallazgos que cambiaron el test respecto del encargo:

- **El pid del resultado de Bash no es el del `sleep`.** El tool Bash del CLI corre un
  trabajo en segundo plano como líder de grupo, así que `setsid` hace fork y `$!` es el
  envoltorio, que muere enseguida (en la salida de abajo, `1394978` el eco y `1394980` el
  `sleep`). El test toma el pid del resultado solo como prueba de que la llamada ocurrió, y
  encuentra el sobreviviente por su marca y su comando, que es como lo encuentra el reaper.
- **El propio CLI lleva la marca** (`options.env` es su entorno), así que un reap con el
  alcance del intento lo alcanza si aún no salió: el test lo hace después de cerrar el stream,
  que es cuando lo hace el runner. Un reap en mitad del stream mató al CLI (exit 143).

Y el `sleep` sobrevivió al cierre del stream: la fuga es real y el reaper la recoge.

```
CLI version: 2.1.281
SDK version: 0.2.159
Bash calls: {'toolu_01B1REtpdqatfGcrXH9ZMD8Q': 'setsid nohup sleep 300 >/dev/null 2>&1 & echo $!'}
Bash results: {'toolu_01B1REtpdqatfGcrXH9ZMD8Q': '1394978', 'toolu_01VgeLUWkN5LV3JeD7es89ea': 'Structured output provided successfully'}
pid echoed by the Bash result: [1394978]
marked processes at tool-result time: {1394218: '/home/zeta/dev/repos/agentic-system/.agentic/worktrees/GH-282.reaper/.venv/lib/python3.13/site-packages/claude_agent_sdk', 1394980: 'sleep 300'}
marked processes when the stream closed: {1394980: 'sleep 300'}
reaped: ({'pid': 1394980, 'command': 'sleep 300', 'outcome': 'term'},)
cost_usd: 0.009835
PASSED
============================== 1 passed in 5.91s ===============================
```
