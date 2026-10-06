---
status: accepted
date: 2026-10-06
---

# Los chequeos corren una vez por entrega, en paralelo en la puerta

Los chequeos que el producto declara corrían dos veces por entrega, las dos en
serie: el Developer los corría todos antes de entregar (`developer.md`) y la
puerta (`CheckoutPresubmit`) los volvía a correr uno tras otro. Esta tarea
(GH-175) hace que corran una vez completos -- la puerta --, y que la puerta
pueda correrlos a la vez. Este documento lo registra por secciones, una por
subtask: la llave y la evidencia de este repo (`stack-key`), y la puerta
(`gate`, bajo `## La puerta`).

## La llave `parallel_checks`

`.agentic/stack.yaml` gana una llave opcional, `parallel_checks`.

- **Opt-in.** Ausente es `False`: la puerta serial de hoy, byte por byte. Un
  producto que no escribe la llave no cambia. Opt-out se descartó porque el
  producto que comparte algo entre sus chequeos (un puerto, un archivo de base
  de datos, una caché) fallaría en rojo sin que nadie haya tocado nada, y el
  rojo parecería del código y no de la puerta.
- **Bool estricto.** Se valida con `isinstance(value, bool)`:
  `"true"`, `1`, `null` o una lista levantan `ValueError` que nombra la llave y
  la fuente, en la carga y en `preflight`; nunca se leen como un sí o un no. Es
  la misma regla de "declarado pero roto" de `witness`, con una tercera forma de
  validación al lado de la de `witness` y la de los mapas de comandos.
- **Leída en el base de la tarea.** Como `checks`: `ProductStack.parallel_checks(revision)`
  (puerto y `GitStack`) lee `.agentic/stack.yaml` en el commit que se le pide.
  Una tarea que edita la llave no cambia la barra contra la que se mide; aplica
  desde la tarea siguiente al merge.
- **Validada en `preflight`.** `declared_stack` la llama sólo para validarla,
  junto a `witness` y `design`; su valor de retorno sigue siendo `setup`.
- **La plantilla** (`agentic/core/knowledge/domain/stack.yaml`) lleva la llave
  comentada, con una línea que dice cuándo prenderla.
- **Por qué no grupos con nombre.** Un producto podría declarar "estos dos
  chequeos no corren juntos" (grupos o exclusiones). Se descartó: es una
  declaración nueva por cada colisión posible, que hay que mantener cada vez que
  se agrega un chequeo, y hoy ningún producto la pide: quien comparte algo entre
  dos chequeos deja la llave en su valor por defecto. Si un producto necesita
  mezclar, la llave puede crecer después sin romper a quien ya la escribió como
  bool.

## Evidencia para este repo

Medición, no chequeo (brief de `stack-key`): en el worktree de esta subtask,
después de `uv sync --locked`, una función de shell arranca con `&` los cuatro
comandos declarados bajo `checks`, espera cada pid con `wait` en la misma
llamada e imprime `nombre código` por chequeo más los segundos transcurridos.
Se corrió cinco veces a la vez (una sola llamada de Bash), y después los mismos
cuatro comandos en secuencia. Nada sobrevivió a la llamada.

La función, tal cual:

```bash
measure() {
  local tag=$1 start end name
  local -a pids names
  start=$(date +%s)
  uv run pytest -m "not llm" >/tmp/gh175_$tag.tests.log 2>&1 & pids+=($!); names+=(tests)
  uv run ruff check . >/tmp/gh175_$tag.lint.log 2>&1 & pids+=($!); names+=(lint)
  uv run ruff format --check . >/tmp/gh175_$tag.format.log 2>&1 & pids+=($!); names+=(format)
  uv run mypy >/tmp/gh175_$tag.types.log 2>&1 & pids+=($!); names+=(types)
  for i in 0 1 2 3; do wait "${pids[$i]}"; echo "$tag ${names[$i]} $?"; done
  end=$(date +%s)
  echo "$tag elapsed_s $((end - start))"
}

serial() {
  local start end code
  start=$(date +%s)
  for pair in 'tests:uv run pytest -m "not llm"' 'lint:uv run ruff check .' 'format:uv run ruff format --check .' 'types:uv run mypy'; do
    name=${pair%%:*}; cmd=${pair#*:}
    eval "$cmd" >/tmp/gh175_serial.$name.log 2>&1; code=$?
    echo "serial $name $code"
  done
  end=$(date +%s)
  echo "serial elapsed_s $((end - start))"
}
```

Y la llamada (cada corrida concurrente escribe su salida a un archivo propio,
para que las líneas de las cinco no se mezclen, y se imprimen juntas al final):

```bash
source /tmp/gh175_measure.sh
for t in c1 c2 c3 c4 c5; do measure $t >/tmp/gh175_out_$t.txt 2>&1 & done
wait
cat /tmp/gh175_out_c*.txt
serial
```

Las seis salidas, pegadas tal cual:

```
c1 tests 0
c1 lint 0
c1 format 0
c1 types 0
c1 elapsed_s 66
c2 tests 0
c2 lint 0
c2 format 0
c2 types 0
c2 elapsed_s 65
c3 tests 0
c3 lint 0
c3 format 0
c3 types 0
c3 elapsed_s 67
c4 tests 0
c4 lint 0
c4 format 0
c4 types 0
c4 elapsed_s 67
c5 tests 0
c5 lint 0
c5 format 0
c5 types 0
c5 elapsed_s 65
serial tests 0
serial lint 0
serial format 0
serial types 0
serial elapsed_s 49
```

Ninguna de las cinco corridas concurrentes dio rojo, y la serial tampoco: no
hay un rojo concurrente que la serial no dé. Con eso, este repo agrega
`parallel_checks: true` a su `.agentic/stack.yaml`, con un comentario que apunta
a este documento. Lo que la medición dice y lo que no: son cinco corridas de
veinte procesos a la vez sobre un mismo árbol (más carga que la que la puerta
real pone, que son cuatro por entrega), y comparten `.pytest_cache`,
`.mypy_cache` y `.ruff_cache` sin colisión; el tiempo concurrente (65-67 s con
cinco corridas compitiendo por CPU) no es el de una puerta sola y no se lee como
tal.

## Cuándo aplica

El stack se lee en el base de la tarea. La puerta de esta misma tarea corre,
por lo tanto, en serie: el `parallel_checks: true` que esta subtask agrega a
`.agentic/stack.yaml` aplica desde la tarea siguiente al merge, y la primera
puerta en paralelo es la de esa tarea.

## La puerta

`CheckoutPresubmit` gana `parallel: bool = False`, sólo por nombre y junto a
`witness`; `develop` se lo pasa desde `ports.stack.parallel_checks(task_base(...))`,
en el único sitio donde se construye (sirve al Developer y al integrador).

### Los dos modos

- **Serial (por defecto).** La lista por comprensión de siempre: un chequeo
  tras otro, en el orden declarado. Un producto que no escribe la llave corre
  igual que antes.
- **Concurrente (`parallel_checks: true`).** Los chequeos corren por un
  `ThreadPoolExecutor` con un hilo por chequeo. `map` devuelve en el orden
  declarado y cada `_run` mide su propio `duration_s` en su hilo. El testigo
  corre antes, primero y solo, como siempre; `WITNESS_REFUSALS` y `REFUSALS` no
  se tocan.
- **Por qué opt-in.** Lo dice la sección de la llave: un producto cuyos
  chequeos comparten un puerto, un archivo de base de datos o una caché
  fallaría en rojo sin que el código haya cambiado.

### `timeout_s` por modo

Serial: la fórmula de hoy, `check_timeout_s × max(len(checks), 1)` más
`check_timeout_s` si hay testigo. Concurrente: `check_timeout_s`, más
`check_timeout_s` si hay testigo; nunca menos. Cada chequeo sigue con su propio
`check_timeout_s` y su propio corte del grupo de procesos en `ShellCommands.run`
(que arma su entorno por llamada y no guarda estado: es seguro entre hilos).

### Un chequeo que levanta, en paralelo

Si un `_run` levanta una excepción, la salida del `with` espera a que los demás
chequeos ya arrancados terminen, y recién entonces la excepción sale de la
puerta. Ningún chequeo sobrevive a la llamada. No se escribe `head`, `results`
ni `invocations` para esa llamada, igual que en serial.

### Procesos y contención de CPU

Hasta `len(checks)` procesos por puerta en lugar de 1: como mucho
`AGENTIC_MAX_CONCURRENT_ROLES × chequeos` en vuelo (16 en este repo con los
valores por defecto), y sólo para los productos que optaron. La contención de
CPU puede empujar a un chequeo más allá de su `check_timeout_s`: se ve como un
rojo `timed_out` con su propio mensaje, nunca como un verde silencioso.

### El fin de turno del Developer

`developer.md` ya no pide "todos los chequeos una vez al final": pide, una vez
antes de entregar, los archivos de prueba de lo que cambió, las pruebas que
escribió y los chequeos declarados de tipos, lint y formato. La suite completa
es de la puerta, y su rechazo devuelve el fallo en el mismo turno
(`presubmit.REFUSALS`). Las reglas de correr en primer plano y de no sondear un
chequeo en segundo plano quedan como estaban. `developer_integrator.md` conserva
una corrida completa para encontrar lo que el merge rompió, y no la repite antes
de entregar: la puerta es, por diseño, su segunda corrida completa. La puerta
sigue corriendo **todos** los chequeos declarados en cada entrega, y el revisor
sigue reproduciendo con ellos (ADR-0016, adenda): la cobertura no baja.

No hay prueba que mire la prosa de esos prompts (ADR-0023); la guarda de que no
nombran una herramienta es `test_prompts_stack_flavour.py`.

### `REFUSALS = 2` no cambia: línea base

Ahora la puerta es la primera corrida completa, así que los rechazos por
entrega pueden subir. Línea base, una lectura de una sola vez (el script no se
commitea; `/tmp/gh175_baseline.py`) sobre `.agentic/tasks/*.md` de este repo y
del de apostrophe (`/home/zeta/dev/repos/apostrophe`, legible). Alcance: entradas
de `runs[]` con `slug: developer` y `started_at` ≥ 2026-10-04; "rojo" es una
sección `## Developer: …` (las `(vN)` incluidas) de esas tareas cuya tabla
`### Checks` tiene una fila `**failed**`; "trabajo" es
`ended_at − started_at − queue_wait_s`. Campos confirmados en
`domain/task.py` (`RoleRun`: `slug`, `started_at`, `ended_at`, `queue_wait_s`,
`presubmit_denials`).

```
$ python3 /tmp/gh175_baseline.py
agentic-system tasks 23 developer runs 40 sections 48
  mean presubmit_denials 0.05
  mean work seconds 411.6 over 40
  red share 0 / 48 = 0.0
apostrophe tasks 26 developer runs 142 sections 159
  mean presubmit_denials 0.42
  mean work seconds 797.6 over 142
  red share 9 / 159 = 0.06
```

Cota posterior al merge: `presubmit_denials` medio por corrida de Developer
≤ 1.0, y una proporción de entregas en rojo no mayor que la línea base de arriba.
Pasarse de la cota es un issue de seguimiento para subir `REFUSALS`,
dimensionado con esos números, no un cambio de esta tarea.

### Cuándo se lee la aceptación

Sólo después del merge, y en apostrophe sólo después de que su humano agregue
la llave. El tiempo de pared de la puerta con `parallel_checks` es el máximo de
los `duration_s` que el documento y `agentic.presubmit.duration` ya llevan; su
suma lo sobreestima (`observability/README.md` lo dice en esa fila). Los minutos
de Developer por entrega se leen como `ended_at − started_at − queue_wait_s` de
`runs[]` con `slug: developer`, en una muestra chica. Cuántas corridas de la
suite completa hace cada Developer no se puede leer del documento.

### La huella de los prompts

`prompts.fingerprint` se mueve para `developer` y `developer_integrator` en el
commit de merge de esta tarea. Las lecturas segmentadas por huella para esos dos
roles (costo, duración, turnos, `presubmit_denials`) no son comparables a través
de ese corte. El scorecard y `agentic eval compare` agrupan por
`system_baseline` y siguen siendo comparables entre cohortes. Se le pide al
humano un `bl-*` nuevo al mergear.

No se agrega, renombra ni quita ninguna métrica, etiqueta, atributo de span ni
llave de documento.
