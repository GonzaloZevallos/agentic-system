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
