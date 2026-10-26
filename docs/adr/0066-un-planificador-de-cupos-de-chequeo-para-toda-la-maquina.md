---
status: accepted
date: 2026-10-26
---

# Un planificador de cupos de chequeo para toda la máquina

GH-361. Cada suite de chequeos supone que la máquina es suya: `pytest -n auto` arranca 16
workers, Playwright 3, vitest todos sus hilos. Varias puertas, merges y corridas de
desarrolladores las lanzan a la vez. El 2026-10-25 el load average llegó a 115 en 16 núcleos
con 8 corridas vivas y chequeos chocaron con el timeout de 1500 s. La mediana de una puerta
que pasa en apostrophe (20 al 26 de octubre) sube de 18,3 min sin otras puertas a 21,9 con
una, 25,9 con dos, 31,4 con tres y 38,9 con cuatro o más. La carga además alimenta flakes
(apostrophe #428).

## Decisión

- **La unidad: la puerta, no el comando.** El planificador da un cupo por puerta. Toda suite
  de chequeos pasa por `CheckoutPresubmit`, que se construye en cuatro sitios (`develop`:
  `subtask`; `full_checks`: `integration`; `merge` y `landing_slice`: `land`) y que maneja
  la puerta por `__call__`. El `acquire` va en un método privado que arranca donde ya hay
  commit y árbol limpio (testigo, chequeos, registro) y el `release` en su `finally`; una
  llamada rechazada por falta de commit o árbol sucio no hace cola. Las tres llamadas a
  `setup` corren `uv sync`, no una suite, y quedan fuera.
- **Puerto y adaptador.** `CheckScheduler` en `application/ports.py` (`acquire(request) ->
  Grant`, `release(grant)`), y un campo `check_scheduler` en `Ports`. El adaptador
  `FilesystemCheckLedger` lleva un ledger JSON por máquina bajo
  `$XDG_STATE_HOME/agentic/checks/` y un archivo de lock hermano; cada lectura-modificación-
  escritura toma `fcntl.flock`, que el kernel suelta si el dueño muere. Un semáforo dentro
  del proceso no acota nada cuando las puertas son procesos distintos.
- **Núcleo puro.** `domain/check_schedule.py` tiene el presupuesto, la reserva, el tamaño
  del cupo, el orden de la cola, el envejecimiento y el recurso vencido, como funciones sobre
  una instantánea inmutable del ledger. La cantidad de núcleos y la hora llegan como
  argumentos; el módulo no importa `os` ni `time`.
- **Qué guarda una entrada.** Producto, pid, boot id, token de instancia, prioridad,
  `enqueued_at`, cupo (0 mientras espera), `granted_at`, `expires_at` y `overdue`. Sólo
  campos cerrados: nunca el texto de un comando.
- **Cuándo se suelta.** Una entrada se mantiene mientras su pid vive en este arranque
  (`liveness.is_alive`) y no venció. Una entrada muerta de este producto se reapea primero,
  con `ProcessReaper.reap_run(CHECK_TASK, pid)`, y sólo después se quita; si un proceso
  sobrevive al reap, sus núcleos siguen reservados hasta el próximo intento o el
  vencimiento. Una entrada muerta de otro producto sigue reservada hasta que ese producto la
  reapee o venza: sólo su clave encuentra sus procesos. Una entrada vencida con pid vivo se
  quita sin reapear: el pid puede ser uno reutilizado, y reapear mataría los chequeos de una
  puerta viva nuestra. Ninguna entrada sobrevive a su `expires_at`.
- **`PerfLock` no se toca.** Sigue siendo el candado de las mediciones de rendimiento.

## Cupos, con `check_cores = 16`

Todos los topes son diales de `Configuration`: `check_cores` (por defecto los núcleos
afines al proceso), `check_share_min`, `check_share_max`, `check_share_max_subtask`,
`check_wait_max_s` y `check_ledger_dir`.

| Qué | Regla por defecto | Núcleos a 16 |
|---|---|---|
| Reserva, sólo `integration` y `land` | `check_cores // 4`, al menos 1 | 4 |
| Tope de una puerta `subtask` | `(check_cores − reserva) // 2` | 6: dos puertas de subtarea corren juntas y una tercera espera |
| Tope de `integration` y `land` | `check_cores − reserva` | 12; un `land` junto a una puerta de subtarea recibe 10 al instante |
| Cupo mínimo | `check_share_min` | 2 |

Con pocos núcleos la reserva nunca es todos (`min(max(1, n // 4), n − 1)`: una máquina de un
núcleo no tiene reserva, o ninguna puerta de subtarea correría nunca), y el mínimo de una
clase es `min(check_share_min, tope de la clase)`.

## Orden de la cola

- `CheckPriority` es un enum cerrado: `land` > `integration` > `subtask`.
- La cola ordena por `(rango efectivo, enqueued_at, pid)` y sólo la cabeza puede tomar
  núcleos: una puerta chica detrás de una grande no la pasa.
- Envejecimiento: tras `AGING_S` = 600 s de espera, una solicitud rankea como `land`. Sólo
  cambia su lugar en la cola; lo que puede usar lo decide su prioridad real, así que una
  `subtask` envejecida sigue sin tocar la reserva.

## Conservar el lugar y el techo de espera

- Una puerta conserva su lugar entre rechazos: la instancia de `CheckoutPresubmit` guarda su
  primer `enqueued_at`, el token y los segundos ya esperados, y los devuelve en cada llamada.
  `Grant.wait_s` es la espera de esa llamada (reloj monótono); quien llama los suma.
- Cada llamada espera como máximo `min(check_wait_max_s, techo − esperado)`, sondeando cada
  1 s y conservando su línea de espera en el ledger para que los demás vean su lugar.
- Si la espera se agota: un intento sin esperar, con el cupo normal y sin respetar el orden de
  la cola. Sólo si falla toma `check_share_min`, anotado en el ledger y marcado `overdue`.
  En un ledger libre el recurso da un cupo normal y no se marca.
- **El cupo vencido de una `subtask` no sale de la reserva.** Se anota por encima de la
  cota y no cuenta contra lo que puede tomar un `land` o un `integration`: así la reserva
  queda entera para las corridas más cercanas a terminar. Los vencidos de `integration` y
  `land` sí cuentan.

## Trabajadores

Un marcador `{workers}` en `checks` y `affected_checks` se reemplaza por una función pura
junto a `affected_command` (primero `{base}`, luego `{workers}`). Con un cupo concedido, todo
comando recibe `CHECK_WORKERS` y `PYTEST_XDIST_AUTO_NUM_WORKERS` por un `env` sólo-por-
nombre nuevo de `Commands.run`; no `AGENTIC_*`, que `without_own_configuration` borra. Un
comando sin `{workers}` corre igual byte a byte, más dos variables de entorno. Estas piezas
las construyen las otras subtareas; el núcleo del planificador no las toca.

## Timeout

El runner no cambia: `communicate(timeout=check_timeout_s)` empieza después de la concesión,
así que esperar un cupo no cuenta contra `check_timeout_s`. El presupuesto externo del hook
de entrega suma `presubmit.wait_max_s`. El watchdog de estancamiento (ADR-0057) ya cuenta un
hook de entrega en vuelo como actividad: una puerta en la cola no es un estancamiento. Un
hijo de merge o aterrizaje que espera un cupo no publica Espera de aterrizaje, no es
`Deferred` y no toca `LAND_WAIT_TTL`.

## Señales (estándar 7)

Este cambio agrega señales y no quita ni mueve ninguna: sobre el span existente de
presubmit / `full_checks`, `agentic.presubmit.wait_s`, `.share`, `.peers` (sólo ayuda de
depuración), `.priority` (enum cerrado) y `.overdue`; y un registro persistido de la puerta
(`started_at`, `wait_s`, `run_s`, `priority`, `overdue`) en la corrida del Developer y en los
registros de merge, aterrizaje y `full_checks`. Todo campo nuevo es opcional al leer
(ADR-0041): un documento escrito antes sigue cargando.

## Seguridad y rendimiento (estándar 8)

El ledger es un archivo nuevo que leen y escriben los procesos de todos los productos de la
máquina. Vive bajo el directorio de estado del usuario y no guarda texto de comandos. Cada
`acquire` cuesta tiempo lineal en las entradas vivas (unas pocas decenas) y sondea cada 1 s
mientras espera. Lo que escala es la espera, que crece con las puertas simultáneas: es el
objetivo, y se mide (`scripts/gate_concurrency.py`).

## Riesgos conocidos

- **Una puerta de subtarea sola puede correr más lento.** Pasa de unos 16 workers de xdist a
  6, y la reserva queda ociosa cuando no hay una corrida más cercana.
  `check_share_max_subtask` es el dial; el script informa la mediana por cupo para afinarlo.
- **La cota cubre núcleos reservados, no el load average.** El Bash de las sesiones de roles,
  el poller, lint, format y types están fuera. `check_cores` puede bajarse para dejarles lugar.
- **Huérfanos de otro producto.** Una puerta de apostrophe matada mantiene sus núcleos
  reservados hasta que apostrophe vuelva a pedir o venza la entrada. Reservar núcleos mientras
  puede haber huérfanos corriendo es el error seguro.
- **Cobertura de herramientas.** `PYTEST_XDIST_AUTO_NUM_WORKERS` depende de la versión de
  xdist instalada en el producto. Playwright y vitest siguen el cupo sólo por un `{workers}`
  declarado en el stack del propio producto.
- **Sustitución.** Un `{workers}` literal en un comando declarado se reescribe.
- **El reap ocurre bajo el lock.** Reapear la entrada muerta de un dueño (hasta unos 7 s con
  gracia y KILL) detiene a los demás que piden un cupo en ese lapso; es el caso raro y evita
  devolver núcleos antes de que los procesos hayan muerto.

## Coexistencia con las formas viejas

Los documentos de tarea anteriores no tienen registro de puerta: todo campo nuevo es
opcional al leer. El `stack.yaml` de este repo no se toca: las claves del stack se leen en la
base de la tarea, así que adoptar `{workers}` aquí y en apostrophe sólo rige desde la tarea
posterior al merge, y queda como seguimiento.

## Medición

`overlap_peers(intervals)` es la única función que calcula «otras puertas al lado»: el
promedio, ponderado por tiempo, de las otras puertas que se solapan con cada intervalo.
`scripts/gate_concurrency.py` la alimenta de las transcripciones de sesión (inicio y fin de
la llamada a la herramienta de entrega, la fuente del issue, usada en los dos lados de la
comparación) y de los documentos de tarea (el registro nuevo, que además da `wait_s` y
`run_s` por separado). El «antes» es la tabla del issue; el «después» se corre sobre apostrophe
tras una semana de ejecuciones.
