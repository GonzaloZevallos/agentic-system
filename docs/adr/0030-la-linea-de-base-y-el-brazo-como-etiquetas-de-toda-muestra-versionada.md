---
status: accepted
date: 2026-09-23
---

# La línea de base y el brazo viajan como etiquetas: de dónde sale cada valor y qué no puede entrar

ADR-0028 decidió el eje: la línea de base es el hito que el humano declara con un tag de
git, y el brazo es la configuración de experimento bajo la que corrió una tarea. Este ADR
no vuelve a decidir eso; decide **de dónde sale cada valor en tiempo de ejecución**, qué
forma tiene el que se emite, y qué queda explícitamente afuera. Son decisiones difíciles de
revertir porque una etiqueta emitida vive trece meses y ADR-0008 las agrega, nunca las
renombra: si el brazo sale mal formado hoy, el arreglo parte la serie en dos.

## La línea de base se resuelve como la versión, no por un canal nuevo

`telemetry.system_baseline()` es el espejo exacto de `system_version()`:
`AGENTIC_SYSTEM_BASELINE` como override, si no `git describe --tags --abbrev=0 --match
"bl-*"` con `git -C REPO` (el mismo repositorio que lee `_git_version`), si no el literal
`untagged`; misma memoización a nivel de módulo y mismo `except (OSError,
subprocess.CalledProcessError)`.

**`stage0.sh` no exporta nada.** El pedido decía que el script dejara
`AGENTIC_SYSTEM_BASELINE` "disponible para las corridas", y ese camino no existe: el script
clona, fija, sincroniza e **imprime** el comando que el humano corre después, así que su
entorno muere con él. Y la versión tampoco llega por una variable: se resuelve en el momento
de la llamada, contra el checkout que está corriendo, y por eso no puede quedar vieja. El
script imprime la línea de base que resolvió, al lado de `stage0 at <dir>, fixed to <sha>`,
y **no** prefija el comando. Un prefijo impreso se copia a la historia del shell y sobrevive
al siguiente re-pin: pasado un tag `bl-` nuevo, ese valor viejo le gana a git y toda muestra
de toda tarea aterriza bajo la línea de base anterior, en silencio y sin ningún `+dirty` que
lo delate — bajo la única etiqueta que es el eje de comparación. El caso que el prefijo
protegía, que los tags se muevan entre el script y la corrida, se resuelve solo: al
re-resolver gana la verdad más nueva. `AGENTIC_SYSTEM_BASELINE` queda siendo lo que
`AGENTIC_SYSTEM_VERSION` es, un override para los tests y para un checkout sin git, nunca el
camino normal.

**`--match "bl-*"` no es cosmético.** Hoy `git tag` devuelve `bl-2026-09-23` y nada más, así
que la forma sin filtro es correcta por accidente de un namespace vacío. Sin el filtro, el
primer `v0.1`, un tag de spike o cualquier tag que alguien empuje se convierte en un valor de
`system.baseline` y parte una línea de base en dos sin que nadie la haya declarado, que es
justo lo que ADR-0028 existe para terminar. `BASELINE_TAG_GLOB` es una constante con nombre al
lado del literal `untagged`, y el mismo test que fija el fallback fija el argumento, con su
entrada rechazante: un repositorio cuyo único tag está fuera de la forma resuelve `untagged`.

`untagged` y `unknown` no se funden: `untagged` significa *git contestó y no hay línea de base
alcanzable*, un valor de etiqueta real que se emite; `unknown` significa *el documento no lo
dice*, que es lo que `close` ya tiene para un `system_version` ausente. `pre-baseline` no se
escribe ni se emite acá: es la etiqueta de cohorte de `eval compare`, donde ADR-0028 la puso.

## El brazo son dos interruptores, y `reviewer_model` no entra

`arm_label(experiment: Mapping[str, str]) -> str` en `core/measurement/domain/metrics.py`, un
solo punto de entrada sobre la forma persistida. Devuelve `default` cuando los dos
interruptores están en su default y `review_mode=<v>,skip_ephemeral=<v>` si no, con los
defaults resueltos adentro desde `Configuration.model_fields[key].default` y nunca desde
literales — la misma fuente que `experiment_snapshot` lee para su propia regla de omitir en el
default, así que la omisión del frontmatter y el `default` de la etiqueta no pueden discrepar
si algún día un default se da vuelta. "Clave ausente ⇒ default" vive adentro de esa función y
en ningún otro lado: `close` le pasa `raw.get("experiment") or {}` verbatim y el camino vivo
le pasa `{"review_mode": cfg.review_mode, "skip_ephemeral": str(cfg.skip_ephemeral)}`.

**`reviewer_model` queda afuera de la etiqueta.** Es texto libre que sale de `model_overrides`
o de `verifier_model`, y meterlo volvería falsa la frase de `observability/README.md` que dice
que `system.version` y `prompts.fingerprint` son las dos únicas etiquetas sin cardinalidad
acotada. El contrato de cardinalidad cerrada de ADR-0008 no es lo que esta tarea gasta. Sigue
en el frontmatter, donde `evaluation.baseline_id` ya lo digiere y donde la pregunta más fina
pertenece; la asimetría queda escrita acá y en la fila del README.

`ARM_KEYS = ("review_mode", "skip_ephemeral")` es su propia constante y no `EXPERIMENT_KEYS`
menos uno: un cuarto interruptor agregado mañana, o se saltea la etiqueta en silencio, o entra
en silencio, y si es un nombre de modelo o una ruta entra como etiqueta sin acotar — el
invariante roto por una edición en un archivo que el invariante no nombra. Un test unitario
exige que `ARM_KEYS` sea una subsecuencia de `EXPERIMENT_KEYS` (el orden tiene una sola
definición) y que cada clave nombre un campo de `Configuration` anotado `bool` o `Literal`.

La grafía es la de `experiment_snapshot`, verbatim, `skip_ephemeral=True` con mayúscula. El
`skip_ephemeral=true` del pedido se corrige en este ADR y en el README, no en
`experiment_snapshot`: su salida es frontmatter persistido, y bajarle la caja movería
documentos para hacer verdadero un ejemplo. Que el documento y la etiqueta coincidan byte a
byte es lo que hace joinable `eval compare` contra Grafana.

## El camino vivo lee `Configuration`; la entrega lee el documento

`arm` viaja al lado de `product`: `role_span(role_name, role_slug, product, *, arm)` y
`task_span(task_id, title, product, *, arm)`, keyword-only y **requerido**, ensanchados también
en el protocolo `Telemetry` y en `_NoSpan`. Requerido y sin default: un `arm="default"` dejaría
que un cableado olvidado etiquetara mal un brazo entero sin que nada pueda verlo, y la etiqueta
existe justamente para que un brazo no quede sin atribuir.

La fuente es `Configuration`, no la patch ni el estado. La patch es el defecto que esta
decisión evita: `experiment` lo escriben sólo `plan.py` y `review.py`, y `RoleRun.finish`
etiqueta sobre la patch, así que bajo `review_mode=light` cada Developer habría emitido
`arm=default` — el costo por subtarea del experimento, que es justo lo que #28 lee. Reescribir
`experiment` en el primer nodo para volver viable al estado es peor: `reviewer_model` no se
conoce en `classify`, así que una corrida no-default persistiría un `experiment` de dos claves
que `plan`/`review` después pisan con tres, moviendo el frontmatter del brazo mismo que el
experimento está leyendo.

Los dos spans llevan los dos valores, **desnudos** (`system.baseline`, `arm`), aplicando la
regla de ADR-0008 del 2026-09-17 como está escrita: una clave que ya se escribe igual en span y
en métrica conserva la grafía desnuda, y sólo la que difiere toma el prefijo `agentic.`.
Puestos **al abrir**, al lado de `product`: un span de rol que se cae nunca llega al estampado
de `finish`, y "una corrida caída sigue siendo atribuible a uno de los dos" es la razón por la
que `product` se pone al abrir. Son dos toques de traza y ninguno más: `agentic.delivery.*` no
tiene span, porque `close` no corre ningún rol.

**La entrega lee el documento**, no el checkout que cierra: `raw.get("system_baseline") or
UNKNOWN_SYSTEM_VERSION` y `arm_label` sobre `raw.get("experiment") or {}`, plegados en
`_delivery_labels`, que es el único helper del que su familia lee. Es la razón que el docstring
de ese helper ya da para la versión: una muestra de entrega nombra la corrida que juzga.

**La asimetría, dicha antes de que aparezca en un panel.** Toda muestra que una invocación
emite nombra el brazo vivo de esa invocación; una muestra de entrega nombra el brazo que quedó
en el documento, que es el brazo que planificó la tarea. Para una tarea planificada en
`default` y reanudada en `light`, los dos difieren — exactamente como difieren sus versiones, y
un `resume` desde otro stage0 ya emite otra versión. Queda en el docstring de `_delivery_labels`
y fijado por un test: `close` alimentado con un documento cuyo `experiment` nombra un brazo
mientras el entorno nombra otro, y la muestra sigue al documento.

## Qué familias, y las dos que no se mueven

Las familias se enumeran por la regla mecánica "su fila de `observability/README.md` lista
`system.version`", nunca por `VERSIONED_METRICS`, que es una lista escrita a mano que lleva a
propósito un control adentro.

**`agentic.role.duration` no gana ninguna etiqueta**: conserva exactamente `product`, `role`,
`role.outcome`. ADR-0008 la congeló dos veces, y la novena adenda mudó su panel a
`run_duration` + `queue_wait` en vez de darle la etiqueta. Su dict de tres claves en el test de
control sobrevive verbatim; lo único que se mueve en esa prueba es el argumento de la línea
`role_span(...)`, como en los ~60 sitios de llamada que este cambio toca mecánicamente. Ningún
conjunto de etiquetas esperado cambia salvo por las dos entradas agregadas.

**El contrato del README se extiende adonde no llegaba.** `VERSIONED_METRICS` cubría rol,
presubmit y entrevistas; `agentic.task.*`, las familias de `agentic.invocation.*`,
`agentic.task.invocations`, `.paused`, `.human_decisions` y `agentic.review.findings` listan
`system.version` en su fila y no estaban fijadas por `_documented()` contra nada. O sea que la
métrica de aceptación de este mismo pedido —`label_values(agentic_task_finished,
system_baseline)`— vivía en el rincón sin contrato: una fila editada y una emisión olvidada
pasaban en verde, y el revisor, que no puede correr una consulta de Grafana, no tenía contra
qué verificar. Una segunda lista al lado de la primera las cubre, con la misma igualdad y la
misma cláusula de que una familia que no emite ninguna muestra falla con su nombre en el
mensaje.

`document.render` gana `system_baseline` **keyword-only** y su único sitio de llamada pasa cada
argumento del registro de versión por palabra clave. Cuatro `str | None` posicionales seguidos
son indistinguibles para mypy: un intercambio escribiría `bl-2026-09-23` como versión del
sistema y un sha como línea de base, invisible para el type checker, para la puerta de entrega y
para la lectura del revisor, con un eje de Grafana lleno de líneas de base con forma de sha como
primer síntoma.

## Riesgos conocidos

- **Un `AGENTIC_SYSTEM_BASELINE` viejo sigue ganando.** Sacar el prefijo de `stage0.sh` cierra
  el camino que el flujo realmente recorre; la variable no se puede cerrar sin perder el camino
  de los tests y el de un checkout sin git.
- **`agentic.role.duration` es la única familia que nunca va a llevar brazo**, así que un panel
  de duración no se puede partir por brazo: esa lectura vive en `run_duration` + `queue_wait`.
- **Un `sum by (arm)` sobre una ventana con pausas** puede leer dos experimentos donde hubo uno,
  por la asimetría de arriba.
- **La telemetría ya emitida no se rellena** (ADR-0008): en Grafana la comparación por línea de
  base arranca en `bl-2026-09-23`, y una muestra anterior no tiene ninguna de las dos claves —
  un panel con una línea de base concreta elegida no la ve.
- **~60 ediciones mecánicas de sitios de llamada** son el hunk más grande del diff. Van
  declaradas en el brief, con la regla de que ningún conjunto esperado cambia salvo por las dos
  entradas: el brief es la única defensa contra leerlas como alcance de más.

## Opciones descartadas

- **Que `stage0.sh` exporte o prefije `AGENTIC_SYSTEM_BASELINE`**: el script no puede hacer
  cumplir lo que imprime, y un comando copiado etiqueta mal una línea de base entera sin tell.
- **`git describe` sin `--match`**: cualquier tag se vuelve un eje de comparación.
- **Leer el brazo de la patch o del estado**: los Developers de un brazo no-default habrían
  salido como `default`.
- **`arm` con default `"default"` en la firma del span**: un cableado olvidado etiqueta mal en
  silencio y ningún invariante lo ve.
- **`reviewer_model` adentro de la etiqueta**: rompe la cardinalidad acotada de ADR-0008 por una
  pregunta que el frontmatter y `baseline_id` ya contestan.
- **`arm_label(*, review_mode, skip_ephemeral)`**: obliga a que el default de `Configuration` se
  escriba también en el sitio de llamada de `close`, un segundo decisor de cuál es el brazo por
  default.
- **Bajar a minúscula `skip_ephemeral` en `experiment_snapshot`**: movería documentos
  persistidos para hacer verdadero un ejemplo del pedido.
- **Dos subtareas (telemetría y documento)**: es una cadena estricta, así que no compra
  paralelismo y sí compra una barrera de fan-in, un merge de dependencia y una revisión más para
  nueve archivos, con un solo escritor real de `telemetry.py` y del README.
