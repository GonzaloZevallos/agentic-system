---
status: accepted
date: 2026-10-26
---

# Un resultado que pasó se reutiliza por contenido, no por historia

GH-359. El mismo código se chequea una y otra vez: el Developer corre la suite a mano
antes de entregar y la puerta la repite sobre el mismo commit; la puerta corre todo cada
vez que la cabeza se mueve, también tras un rechazo que no fue de un chequeo; la puerta de
merge y el corte de aterrizaje vuelven a correr sobre árboles ya verificados. Todos pasan
por `CheckoutPresubmit._run`: ahí se decide una sola vez.

## Decisión

- **La clave.** `domain/check_results.check_key(producto, nombre, comando exacto, árbol,
  huella)`: un digest hexadecimal sobre JSON, así que un campo con un separador no choca con
  el vecino. El árbol es `HEAD^{tree}` de un checkout limpio (`Workspace.tree`), no el
  commit: un merge cuyo árbol es igual a uno ya chequeado reutiliza su resultado. Una forma
  afectada lleva su propio comando, base incluida, así que nunca reemplaza a la completa.
- **La huella del entorno.** Los comandos de setup en el orden declarado, el sha completo de
  stage0 y `check_timeout_s`. Un stage0 `+dirty` no tiene huella: ni lee ni escribe, y el
  camino es el de hoy, byte a byte.
- **Deriva del entorno.** Tras pasar el setup, el sistema anota el árbol sobre el que corrió
  en el directorio git del worktree (`git rev-parse --git-path agentic-setup-tree`); nunca
  en los archivos del checkout. Una escritura exige esa anotación, que debe resolver como
  `tree` (`git cat-file -t`), y que `env_drifted` sea falso: ningún archivo de la lista
  cerrada (manifiestos, locks y archivos de versión de herramienta, a cualquier
  profundidad) ni ruta que un comando de setup nombre literalmente cambió entre el árbol del
  setup y el actual. La deriva no bloquea la lectura: un pase cuenta para la llamada en que
  corrió, sólo que no se guarda.
- **Sólo pases.** Nunca se guarda un fallo, un timeout, un árbol sucio, una corrida sin
  huella ni una corrida bajo deriva. Un fallo puede ser flaky y no se reutiliza como final.
- **Dónde vive.** Un almacén por máquina, `Configuration.check_results_dir`
  (`$XDG_STATE_HOME/agentic/check-results`), fuera de todo checkout; el producto es parte de
  la clave. Un `<clave>.json` por entrada, escrito de forma atómica (archivo temporal más
  `os.replace`). Elegido sobre el documento de tarea: el documento sólo vive dentro de una
  tarea y no sirve a la puerta de merge de otra rama ni al aterrizaje, y la clave por
  contenido hace innecesario atar el resultado a una historia.
- **Frescura.** `MAX_AGE_S = 86400`, comprobado en la lectura con un `now` que se pasa como
  argumento (ningún módulo de dominio lee el reloj). Hasta 5000 archivos: al escribir se
  podan primero los vencidos y luego los más viejos. El costo es lineal en el listado del
  directorio y sólo en escrituras.
- **Confianza.** Cada entrada lleva un HMAC-SHA256 sobre clave, instante y fila con un
  secreto de máquina de 32 bytes, `.key`, modo `0600`, creado por la primera escritura. Una
  entrada ausente, ilegible, sin verificar o vencida es un fallo de caché, nunca una
  excepción. El MAC nombra el problema del estado compartido entre escritores que no son la
  puerta: frena la edición accidental o a mano. **Residual:** todo corre bajo un mismo uid y
  sin sandbox de archivos; un proceso que lea el secreto por un desvío puede firmar una
  entrada. Esto detiene accidentes y el camino obvio, no a un adversario con el mismo uid.
- **Supuesto de determinismo.** Reutilizar por contenido supone que el chequeo es
  determinista sobre ese árbol y ese entorno. Un chequeo flaky que pasó una vez sigue verde
  hasta 24 h sobre el mismo árbol; es el costo aceptado.
- **`run_check` y `MCP_TOOL_TIMEOUT`.** El Developer y su integrador reciben una herramienta
  MCP en proceso, `run_check(name)`: corre un chequeo ya declarado, por el mismo
  `CheckoutPresubmit._run`, y guarda un pase bajo las mismas condiciones que `__call__`. La
  instrucción de correr a mano sólo lo que se tocó vive en la descripción de la herramienta,
  no en el prompt del sistema, así que el prompt y su hash de un producto sin niveles no
  cambian. El CLI espera por defecto un tiempo fijo a una llamada MCP; el intento que lleva la
  herramienta pone `MCP_TOOL_TIMEOUT` (ms) en su propio `options.env`, a `check_timeout_s ×
  (chequeos declarados + 1)`. **Supuesto sin verificar:** no se midió a mano que una llamada
  más larga que el valor por defecto del CLI complete (versión del CLI instalada al escribir
  esto: 2.1.310, SDK 0.2.159). Peor caso si es falso: una corrida que el CLI corta y que no se
  reutiliza, nunca un falso verde. Para medirlo: un producto de prueba con un chequeo que
  duerma más que el valor por defecto, y una llamada a `run_check` sobre él.
- **Rechazado.** Guardar el resultado sólo en el documento de tarea (no cruza ramas ni
  pasos de merge); un `cache_inputs` que cada producto declare (pide que cada producto
  enumere todo lo que un chequeo lee, y un olvido es un falso verde); reutilizar fallos
  (pueden ser flaky).

## Reemplaza

Esta decisión reemplaza el punto «Rechazado por ahora» de ADR-0064 sobre que la puerta de
merge reutilice un registro `full_checks` verde: la clave por contenido resuelve la
frescura. Un árbol de merge igual a uno chequeado reutiliza; cualquier otro corre todo.

## Consecuencias

`CheckoutPresubmit` lee el almacén en `_run` y escribe solo en `__call__` (o en el
manejador de `run_check`), con las condiciones de arriba; los registros `merge_checks` y
`full_checks`, el bloque de checks del Developer y la señal `agentic.presubmit.checks`
marcan `reused`, y las filas reutilizadas nunca entran en una muestra de duración. La
herramienta `run_check` comparte con la puerta la misma `Reuse`, así que lo que guarda es lo
que la puerta lee. Un producto con un stage0 `+dirty` no recibe la herramienta y corre
exactamente los comandos de antes.

## Adenda (2026-10-31): verificaciones declaradas, sus topes de pared y `not_ready`

GH-451. Tres tareas de apostrophe escalaron con el código terminado porque la verificación del
brief dura más que un paso del Developer (10 min en primer plano) y un proceso en background no
se puede leer ni sobrevive al cierre del stream (ADR-0052): lo corrió una persona a mano.
`run_check` ya espera dentro de `HookActivity`, así que se amplía la herramienta en lugar de abrir
el background al modelo. Esta adenda fija el contrato; la ejecución, la línea de evidencia y la
lectura de carga son de las subtareas siguientes de la misma tarea.

- **Verificación declarada.** `stack.yaml` puede declarar `verifications: {nombre: {command,
  wall_s}}`. El modelo sigue pasando solo un nombre; el comando sale del archivo del producto, nunca
  de un texto de un rol. Un nombre que coincide con un chequeo de `checks` (incluido el reservado
  `docs`) se rechaza al cargar. Sin `verifications`, `check_timeouts` ni `not_ready_exit` el
  producto resuelve los mismos topes, la misma `DESCRIPTION` y el mismo hash de prompt de Developer
  que antes.
- **Dos límites, dos dueños.**
  1. **Por chequeo o verificación: lo declara el producto.** `wall_s` en cada verificación y
     `check_timeouts: {chequeo: segundos}` en cada chequeo, ambos positivos y finitos. Sin
     declaración vale `Configuration.check_timeout_s`, como hoy.
  2. **Techo: lo fija el sistema.** `Configuration.verification_wall_max_s` (3600 s por defecto,
     nunca menor que `check_timeout_s`), que el dueño ajusta por `AGENTIC_VERIFICATION_WALL_MAX_S`
     en su `.env`. Ningún producto ni rol lo cambia.
- **Fuera de rango se rechaza, nunca se recorta.** Un valor sobre el techo corta la corrida en la
  Puerta de arranque (`preflight`) con el nombre y las dos cifras. La resolución de todo tope es la
  función única `resolve_wall` de `process/domain/run_check.py`; ningún adaptador ni hook escribe un
  literal de segundos. El tope de una llamada de `run_check` es `call_timeout_s` = mayor tope × 2 +
  2 × espera máxima + testigo, para que `MCP_TOOL_TIMEOUT` cubra el reintento.
- **`not_ready`.** `not_ready_exit: {nombre: 1..255}` declara el código de salida que dice «el
  entorno no estuvo listo» (la convención es 75). Un chequeo o verificación que sale con él se
  reintenta una vez. La carga se lee antes y después por el puerto `LoadReader` (el de `perf_runner`)
  y `saturated(antes, después, núcleos)` es `max(antes, después) ≥ núcleos`; una lectura que no se
  pudo tomar cuenta como no saturada. Un 75 sin saturación es `failed`; saturado en los dos intentos
  es `unverified`. El resultado es el enum cerrado `Outcome`: `passed`, `failed`, `not_ready`,
  `unverified`, `not_declared`.
- **Sin registro en `flakes`.** Un `not_ready` o `unverified` nunca se escribe en `flakes`, nunca abre
  un issue de flakiness, nunca se informa como `passed: no` y nunca cuenta como devolución. La regla
  es heurística (la propia verificación sube la carga de «después»): a veces dará `unverified` donde
  correspondía un rojo, y el humano lo ve en el PR.
- **Nombres.** Toda clave que nombra un chequeo o una verificación (`delivery_checks`,
  `affected_checks`, `rerun_checks`, `check_budgets`, `check_timeouts`, `not_ready_exit`) se valida
  con la misma función, `stack_checks.require_declared`, contra los nombres declarados.
  `check_timeouts` solo nombra chequeos (una verificación lleva su `wall_s`); `not_ready_exit` nombra
  ambos.
- **Orden de despliegue.** (1) se mergea esta tarea; (2) se actualizan explícitamente los stage0 de
  los dos productos; (3) recién entonces apostrophe declara sus claves y hace que `compare.sh` salga
  con 75 cuando la app no está lista. Si un producto declara antes, sus corridas fallan al cargar
  porque las claves desconocidas se rechazan.

## Adenda (2026-11-24, GH-1096): `run_check` corre bajo el plan afectado de la puerta

`run_check` sobre un chequeo declarado deja de correr siempre el comando completo: corre bajo el
mismo `AffectedPlan` que la puerta de entrega, y dice qué forma corrió. Es un cambio observable
deliberado.

- **Afectado primero, completo en rojo.** Un chequeo con forma afectada corre esa forma; si no
  pasa, corre el comando completo en la misma llamada. Un afectado `not_ready` (salida 75) cae
  directo al completo con causa `affected_red` y sin reintento propio; el reintento por `not_ready`
  sigue siendo el del comando completo, como antes. La fila decisiva es la última de
  `final_results(filas)`.
- **Sin reintento ni compuerta de flakes.** La herramienta no recibe `rerun_forms` ni `flake_gate`:
  el manejo de flakes de la herramienta queda como estaba (ADR-0066 vale sólo en la puerta).
- **`stuck_full` propio.** La herramienta lleva su propio conjunto, separado del de la puerta: un
  rojo sobre un árbol que todavía se edita no debe obligar al árbol entregado a correr completo.
  Tras un rojo completo, la llamada siguiente corre completo con causa `stuck_full`. Sólo la
  herramienta lo libera, y sólo cuando la fila decisiva es un completo que pasa; el
  `_planned_rows` de la puerta nunca lo libera.
- **HEAD actual más rutas pendientes.** Cada llamada lee `workspace.head` (nunca el `before` del
  inicio del intento, que sólo decide `committed`) y, con el puerto `PendingPaths` cableado, las
  rutas pendientes del árbol. Un archivo de glob de fallback o el del stack sin commitear da
  `fallback_glob` o `stack_file` con su ruta. Una lectura que falla (`GitError` u `OSError`) da
  `paths_unknown`: nunca un éxito vacío; y como el árbol no pudo leerse, el pase no se guarda
  (`the tree could not be read`). Sin el puerto, se usa `is_clean`: `()` pendientes si está
  limpio, `None` si no. `pending_paths` usa `--untracked-files=all`, más estricto que `is_clean`:
  sólo puede negar un guardado, nunca permitir uno.
- **Se guarda la fila decisiva, bajo su propio comando.** La clave sale del comando declarado de la
  fila (`_declared_of`), no del completo. Esto corrige el defecto de que un pase afectado se
  guardara bajo la clave del comando completo. Un pase afectado de la herramienta lo reutiliza la
  puerta para el mismo comando afectado sobre el mismo árbol (el plan es el mismo objeto, armado una
  vez en `develop.py`) y nunca cuando la puerta pide la forma completa.
- **La línea `form:`.** La respuesta lleva, justo después de `check:`, `form: affected-only` o
  `form: full (cause: <causa>)`, con `glob` y `path` cuando los hay. Un chequeo sin plan dice
  `full (cause: no_affected_form)`. Una verificación no lleva la línea.
- **Plazo de la llamada.** `timeout_s` cuenta las corridas por chequeo con `call_runs` (3 si tiene
  forma afectada, 2 si no), en las dos ramas; sin plan da el número de antes.
- **Formas registradas.** Las formas de la herramienta (`via: tool`) van en `RoleRun.forms` antes
  de las de la puerta, en orden de llamada y con el tope de 50 (`cap_forms`). El alcance es la
  invocación del nodo de desarrollo; un intento fallado no deja formas.
- **Costo.** Un `git status` (`pending_paths`) y un diff de nombres (`changed_paths`) por llamada
  a un chequeo declarado; lineal en las rutas cambiadas.
