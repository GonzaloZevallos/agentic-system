---
status: accepted
date: 2026-11-03
---

# El brazo sale de la asignación de la tarea, y `eval compare` lo parte con cifras de tiempo

Enmienda el ADR-0030 (GH-470, parte 1). El ADR-0030 dejó el brazo con una sola fuente: el
`experiment` del documento, que `experiment_snapshot` escribe desde la `Configuration` del
proceso. Eso supone un experimento para todo el proceso: los interruptores viven en el `.env`
del poller y ninguna tarea tiene un brazo propio. Este ADR agrega la segunda fuente, la
**asignación** que el poller le da a cada tarea nueva, sin editar ninguna frase del ADR-0030
(el historial queda donde se escribió) y sin mover ningún documento ni ninguna etiqueta ya
emitida.

## El brazo tiene dos fuentes; la asignación gana, y se lee por un solo lugar

`TaskState.assignment = {experiment, arm, switches}` es la clave nueva del documento, que
`document.py` escribe y lee y **omite cuando no hay**. `metrics.arm_of(document)` es el único
lector: devuelve `arm_label(assignment.switches)` si la tarea tiene asignación y
`arm_label(experiment or {})` si no. `eval compare`, `playbook_window._eligible` y
`task_scores` lo usan; ninguno lee `experiment` por su cuenta.

**El nombre del brazo nunca es una etiqueta ni una clave de agrupación.** Se agrupa y se
etiqueta solo con `arm_label` sobre los interruptores. Dos brazos con los mismos interruptores
resueltos son el mismo brazo para la medición, y `parse_experiment` los rechaza. Así la
cardinalidad de la etiqueta `arm` sigue siendo la de los ADR-0008 y 0030: el nombre (texto del
dueño del producto) no entra.

**Los interruptores tienen una sola fuente**: `ARM_SWITCHES = ("review_mode",
"skip_ephemeral")` en `agentic/shared/configuration.py`. `EXPERIMENT_KEYS` es
`(*ARM_SWITCHES, "reviewer_model")`, `experiment_snapshot` decide «en el default» iterando
`ARM_SWITCHES`, `metrics.ARM_KEYS` es `ARM_SWITCHES` y el validador de la declaración lee de
ahí. Esto reemplaza la constante propia de `ARM_KEYS` del ADR-0030 por una derivada: la
razón de aquella (un cuarto interruptor no puede entrar ni saltarse la etiqueta en silencio)
sigue valiendo, y ahora la lista que lo impide está en un solo archivo.

## La declaración: `.agentic/experiment.yaml`

Un producto declara un experimento con un `id`, una regla `assignment: alternate|seeded`
(`seeded` exige un `seed` entero; `alternate` lo prohíbe) y `arms: [{name, share, switches}]`.
`parse_experiment(data, source)` es puro (no lee documentos, registro, entorno ni reloj) y
rechaza, con una sola línea que empieza por la fuente:

- una clave desconocida, arriba o en un brazo;
- un `name` fuera de `[a-z0-9-]{1,24}` o repetido;
- un `share` que no es un número positivo, o que no suman 1;
- un interruptor que no está en `ARM_SWITCHES`, o con un valor que la anotación de
  `Configuration` no admite (`review_mode` es un `Literal`; `skip_ephemeral`, un bool de YAML);
- dos brazos que resuelven a los mismos interruptores.

Un brazo puede ser el control con `switches: {}`. Sin archivo, o inválido, el sistema se
comporta exactamente como antes: el argv, el documento y la etiqueta (`default`) son
byte a byte los de hoy.

**Un experimento cambiado toma un `id` nuevo.** Las cuentas y `--fresh` comparan por `id`;
editar los brazos bajo el mismo `id` mezclaría en una cuenta tareas que corrieron bajo
configuraciones distintas.

## Cómo se elige el brazo

`assign(declaration, issue, counts)` es pura: recibe las cuentas y no lee nada.

- **`alternate`** es *deficit round-robin*: gana el brazo con mayor
  `share × (total + 1) − cuenta` (el más por debajo de su parte tras este lanzamiento), con los empates para el orden de declaración. Mantiene las
  partes exactas aun con pocos lanzamientos: tres lanzamientos de una ronda sobre 50/50 dan 2-1,
  nunca 3-0. Los nombres que la declaración no tiene se ignoran.
- **`seeded`** es *hash bucketing*: `sha256(f"{seed}:{issue}")` se ubica sobre las partes
  acumuladas. Es determinista por issue y no necesita cuentas.

**Las cuentas son por número de issue, contadas una vez**, de tres fuentes:

1. los documentos en disco (`TaskDocuments.ids()` más `load()`) cuya `assignment.experiment` es
   el `id` declarado;
2. los registros de lanzamiento con `arm` cuyo issue aún no tiene documento
   (`RunRegistry.record_launch(..., arm=)`, guardado en el JSON del lanzamiento y devuelto por
   `launch_attempts`);
3. cada lanzamiento decidido antes en la misma ronda.

Un relanzamiento de un issue cuyo registro ya tiene brazo conserva ese brazo. El `arm` de un
registro sobrevive a toda reescritura y se borra solo con el registro (`clear_launch`); un
registro sin brazo se escribe exactamente `{since, prior}`.

## Conflictos y declaraciones inválidas

- **El poller**: si la declaración es inválida, o si el `.env` fija un valor no default para un
  interruptor que algún brazo nombra (`experiment.conflicts`), la ronda imprime una línea con
  el motivo y lanza todas las tareas sin asignar. Nunca detiene la cola. El `.env` movería el
  interruptor en todas las tareas y el contraste del brazo ya no sería el que declara: por eso es conflicto.
- **`agentic run --arm`** se niega en el mismo caso: una línea, salida distinta de cero, antes
  de gastar nada.
- **`resume`** aplica solo los interruptores del documento. Si el `.env` ya no coincide,
  imprime una línea y conserva el brazo del documento: una reanudación nunca se niega.
- **`run --fresh`** lleva el brazo solo si el `id` del experimento declarado es el mismo. Bajo
  cualquier otro `id` la tarea arranca sin asignar y **sale de las cuentas** del experimento
  viejo. Es deliberado: una tarea reiniciada bajo otro experimento no puede seguir pesando en
  las partes del anterior.

El poller pasa `--arm <nombre>` solo en un lanzamiento asignado; los nombres de los flags
(`--arm`, `--experiment-file`) se escriben solo como constantes en
`domain/experiment.py`, que importan `poll._argv` y el argparse de `agentic run`. La raíz de
composición aplica `assignment.switches` a la `Configuration` con `model_copy(update=...)`.

## Costo y límites conocidos

- **Costo.** Un producto con un experimento declarado paga, en cada ronda del poller, una
  lectura y un parseo de cada `.agentic/tasks/*.md`: lineal en el historial de tareas y sin
  caché. Es aceptable con unos cientos de documentos y queda señalado para después. Un
  producto sin declaración no paga nada nuevo.
- **Lanzamientos que `poll_plan` no lanza.** Todo candidato sin bloqueo recibe brazo y suma a
  las cuentas de la ronda, aunque `poll_plan` (que lanza a lo sumo uno, el primero listo) no
  lo lance; un candidato en espera o agotado delante puede correr el brazo de ese
  lanzamiento. No sesga las partes a largo plazo, porque el brazo queda en el registro y el
  relanzamiento lo conserva.
- **`evaluation.baseline_id` sigue digiriendo `experiment`**, no `assignment`. Es una
  limitación conocida: el set de evaluación nunca se asigna, así que hoy no se cuenta mal
  nada. Si algún día se asignara, el `baseline_id` tendría que leer `arm_of`.

## `eval compare` parte por brazo y suma cifras de tiempo

Cada línea de base se parte por `arm_of`. Una línea de base con solo `default` imprime
exactamente lo de hoy más dos filas de tiempo; no se agrega ninguna señal ni valor de etiqueta.
`eval compare` lee solo documentos y git local: sin `gh`, sin candado, sin red.

- **`cycle_h`** va del primer `runs[].started_at` a `closed_at`, o, si la tarea no está
  cerrada, al último `runs[].ended_at`. **`active_h`** es la unión de los intervalos de todas
  las corridas (generaliza `parallel_factor._union_seconds`: lo que se solapa cuenta una vez).
- **`cycle_class`** (`closed`/`open`/`excluded`) decide qué tareas entran. Quedan
  `excluded` las de estado `split`, `discarded` o `tracker_only`, las de `closed_by_slices`, las
  sin ningún `started_at` y las cuya primera corrida sigue corriendo. Una tarea a la que le
  falta una cota se excluye; nunca se muestra como cero. Un pull request cerrado sin merge
  entra como `closed`.
- La fila imprime la mediana con su `n` y cuántas están cerradas y cuántas abiertas.
- **Ninguna de las dos cifras entra en `scorecard.verdict`.** Son una lectura de los documentos,
  no un atributo, ni una métrica, ni una etiqueta (como el factor paralelo).
- **`--experiment <id>`** compara los brazos de un experimento entre sí. Con menos de 10 tareas
  (`MIN_ARM_TASKS`) en algún brazo el veredicto es `insufficient (n/m)`; con tareas de más de una
  línea de base, `insufficient (mixed baselines)` y el `n` por brazo de cada una.

## Opciones descartadas

- **Un brazo por proceso (hoy)**: no permite comparar dos configuraciones sobre la misma cola
  en el mismo período; cualquier diferencia de tiempo se mezcla con la deriva del repositorio.
- **Agrupar por el nombre del brazo**: lo vuelve etiqueta (cardinalidad que el dueño elige) y
  parte una serie si se renombra. El nombre es de la declaración; la medición mira los
  interruptores.
- **Leer `assignment` en cada lector**: cada uno con su regla de precedencia; por eso
  `arm_of`.
- **Cuentas por documento en vez de por issue**: un issue reiniciado o con registro de
  lanzamiento pesaría dos veces.
- **Que el `.env` gane ante un conflicto**: el brazo declararía interruptores que no corrieron.
- **Cachear la lectura de documentos del poller**: invalidación y estado nuevo a cambio de un
  costo que hoy no duele.

## Addendum (GH-472, ADR-0086): el control debe nombrar `plan_review`

Un experimento donde unos brazos fijan `plan_review` y otros no es inválido. Ver ADR-0086.
