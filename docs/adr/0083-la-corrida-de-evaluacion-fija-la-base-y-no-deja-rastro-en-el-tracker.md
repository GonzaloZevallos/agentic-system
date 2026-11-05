---
status: accepted
date: 2026-11-04
---

# La corrida de evaluación fija la base y no deja rastro en el tracker

Enmienda el ADR-0081 y la sección 9 del README (GH-470, parte 2). Hasta ahora `agentic eval run`
corría `run --fresh --issue N` sobre el master de turno: reusaba el id, el documento y las
ramas de la tarea real, así que una tarea terminada no se podía re-correr sin tocar su
registro, y el texto de la tarea era el del issue en ese momento. Este ADR define la
**corrida de evaluación** y sustituye a esa forma, sin editar ninguna frase de los ADR
anteriores (el historial queda donde se escribió).

## Qué se compara: brazos sobre una entrada y una base congeladas

El set de evaluación (`.agentic/eval-set.yaml`) pasa a ser `entries: [{issue, base, title,
body}]`: `base` es el sha completo del commit del producto sobre el que se corre, y `title` y
`body` son el texto del issue **congelado** una vez. Cada entrada se corre una vez por brazo
declarado (`--experiment`, leído del `.agentic/experiment.yaml` del master, nunca de una raíz)
o una vez como `default`. La comparación es **entre brazos, sobre una misma entrada y una misma
base**. El desenlace histórico de la tarea real (lo que `eval score` y la línea de base de
`eval compare` leen de `.agentic/tasks/`) es solo una referencia: corrió con otra versión del
sistema y no es comparable en sentido estricto.

La forma `issues:` desaparece sin migración, porque ningún producto tiene el archivo.
`EvalSet.issues` queda como propiedad (los números distintos de las entradas, ordenados) y es la
única fuente del guardián de merge, de `eval score` y del conteo de faltantes de `eval compare`;
un archivo ilegible le da `None` al guardián, nunca un conjunto vacío.

## Una raíz por corrida

Cada corrida vive en un worktree desprendido del producto en `base`, en
`<producto>/.agentic/eval/<id>/`, con `id = EVAL-<issue>-<sha8>-<brazo>-<k>` (`k` es el
siguiente ordinal, calculado desde el disco). Id, raíz, documento y estado son únicos por
`(issue, base, brazo, ordinal)`: dos brazos de una entrada dan dos raíces, y repetir da `k=2`. El
conocimiento, los ADR, los estándares y las reglas del dueño se leen de la raíz, es decir, como
estaban en `base`. El driver escribe el texto congelado y el experimento en
`<raíz>/.agentic/eval-input/` y lanza `agentic run --issue-input <archivo> --arm <nombre>
--experiment-file <archivo>` con `AGENTIC_PRODUCT_DIR=<raíz>`. La entrada de la tarea sale solo
de ese archivo (`FileInbox`); los comentarios del issue no entran. Una corrida de evaluación
nunca se reanuda.

Un `base` que no resuelve o que no declara `.agentic/stack.yaml` se rechaza antes de gastar. Al
terminar el hijo, el driver copia su documento a `<producto>/.agentic/eval/docs/<id>.md`, quita
la raíz y borra las ramas locales `agentic/EVAL-*`; al arrancar, barre las raíces huérfanas de un
driver que murió. `exclude_system_files` ignora `.agentic/eval/`. `eval/docs/` es evidencia
local sin versionar: un `git clean -fdx` borra la historia de evaluaciones, y es un riesgo
aceptado.

## El cableado se decide por la raíz, y el id es la segunda condición

Si una corrida es de evaluación lo decide **solo** `evaluation.is_evaluation_root(product_dir)`,
sobre la ruta resuelta con `realpath` (una ruta que termina en `.agentic/eval/EVAL-…`). La clave
`evaluation` del documento nunca decide el cableado: la escribe la propia corrida y un
documento editado no puede encenderla ni apagarla. `evaluation.wiring_refusal(product_dir,
task_id)` rechaza, antes de gastar, la raíz y el id que no coinciden: un id `EVAL-` en una raíz
que no es de evaluación escribiría en el tracker real bajo un nombre que el tracker no conoce;
un id real en una raíz de evaluación correría sin registro.

`composition.default_ports` envuelve los puertos con decoradores
(`agentic/core/process/adapters/evaluation.py`), de modo que ningún caso de uso gana un `if`
sobre evaluación:

- `EvaluationTracker`: las lecturas permitidas pasan; `issue_comments` y `timestamped_comments`
  devuelven `[]`; `comment`, `label`, `unlabel`, `note` y `drop_label` no hacen nada; cualquier
  otra escritura lanza `EvaluationWrite`.
- `EvaluationWorkspace`: `push`, `publish_ci_ref`, `delete_ci_ref` y `delete_remote_ref`
  lanzan `EvaluationWrite`, de modo que no sale ninguna rama ni `agentic-ci/<id>`.
- `EvaluationStack.ci()` devuelve `()`, así que `full_checks` toma su camino local ya existente.
- `FileInbox` reemplaza al inbox, y `board` y `board_sent` quedan en `None`.

Cada decorador **falla cerrado**: lo que no está en su lista de lecturas permitidas ni
sobrescrito lanza `EvaluationWrite`, de modo que un miembro nuevo del puerto no llega al tracker
real por olvido. Una prueba por introspección clasifica cada miembro de `Tracker`, `Workspace` e
`Inbox` y falla ante uno sin clasificar.

## El estado `evaluated`

El grafo lleva una tarea con `evaluation` a un estado terminal nuevo, `evaluated`, en lugar de
`deliver`: no hay pull request ni veredicto del humano que esperar. Entra a los terminales de
`rules`, a `CLOSENESS` y al mapa cerrado de `board.py` (etapa «Hecha»); una prueba recorre el
`Literal` de estados contra cada mapa cerrado. No se agrega ninguna señal nueva: se mueve un
valor dentro de un conjunto ya cerrado, así que la cardinalidad de las etiquetas no cambia.

## Los lectores, y el cupo

Los documentos de evaluación viven fuera de `.agentic/tasks/`, así que ningún barrido existente
(cubetas de línea de base, ids de documentos, ventana del playbook, puntajes de tarea, puertas
del Reflector) los ve. Solo los lectores que pasan por `evaluation.evaluation_documents`
(filtro `is_evaluation`) los leen: `eval compare --experiment` suma `eval/docs/` a `tasks/`.

El driver cuenta el cupo con las mismas funciones de `rules` que el poller. Dentro de **un solo**
`registry.exclusive()` cuenta los hijos vivos (`counts_against_cap` contra
`max_concurrent_runs`), mira si otro producto tiene una espera de aterrizaje vigente y si la
cuenta está en pausa, y, si nada lo retiene, crea la raíz, lanza al hijo y espera su registro.
Un hijo de evaluación nunca usa `LAND_HEADROOM`: toma un cupo normal y cuenta contra el tope,
como una tarea del poller. El driver honra las esperas de aterrizaje y las pausas de cuenta
porque lanza como lo haría un poller. Las lecturas del tracker (`gather_resumes`) y los
volúmenes (`space_holds`) se miran fuera del cerrojo, en el mismo orden que el poller.

**Carrera aceptada.** El conteo y el lanzamiento son atómicos entre procesos que usan el
registro, pero una corrida lanzada a mano (`agentic run`) registra después de arrancar, así que
puede haber a lo sumo **una corrida por encima del tope**, como hoy. La cota de la máquina es
`max_concurrent_runs` más el único margen de aterrizaje que ya existe.

## Lo que queda fuera

Congelar `title` y `body` es hoy una edición a mano; `eval freeze --issue N --base <sha>` es el
seguimiento. La estimación del gasto pasa a ser hijos × `PER_TASK_ESTIMATE_USD` (45 USD, por
encima de las referencias observadas de 33 a 40 USD) y el presupuesto `--budget` sigue siendo
el tope total del comando.
