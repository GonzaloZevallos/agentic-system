---
status: accepted
date: 2026-11-04
---

# Brazo `plan_shape: single`: un Developer escribe toda la tarea, sin subtareas ni integración

GH-471. Desde #364 el Tech Lead planifica subtareas como un grafo de dependencias y se agrega una
integración cuando hay más de una. Con ese modelo, 29 de 38 tareas de agentic-system y 17 de 33 de
apostrophe tuvieron integración (2,47 subtareas en promedio en agentic-system); integrar cuesta
24-30 minutos por tarea para un paralelismo de 1,26x. Partir una tarea grande en issues ya existe
(ADR-0054, 150 USD o 40 ficheros; el nodo `split`).

El dueño decidió el 2026-11-03: por defecto un solo Developer escribe toda la tarea y el paso de
integración desaparece; una tarea demasiado grande se parte en issues, no en subtareas internas.
Corre primero como experimento y se compara antes de adoptarse. Este ADR fija el diseño antes de
que corra la primera tarea `single`.

## Decisión

- **El brazo.** `Configuration.plan_shape: Literal["free", "single"] = "free"`, el último de
  `ARM_SWITCHES`. Bajo `single` el plan de subtareas (forma A) tiene exactamente una subtarea y
  `integration` vacía; `schemas.plan_check_for(..., plan_shape=...)` lo rechaza con un mensaje que
  empieza por `schemas.PLAN_SHAPE_REFUSAL`, y el prompt del Tech Lead lo dice
  (`rules.plan_shape_text`). Un plan con `parts` o solo de tracker no se rechaza nunca por esto.
- **Sin cambio por defecto.** Con `free` (o sin experimento) el snapshot, la etiqueta del brazo,
  `baseline_id`, el documento y los prompts renderizados son idénticos byte a byte; lo fijan tests
  con literales. `plan_shape` solo aparece en `experiment` y en la etiqueta cuando no es `free`.
- **Un solo origen del valor `free`.** `shared.configuration.DEFAULT_PLAN_SHAPE` se lee del campo
  de `Configuration`; `cost_history`, `schemas` y `rules` comparan contra él y ninguno repite el
  literal, de modo que renombrar el valor no deja un lector atrás.
- **Los umbrales de partición no cambian** (150 USD, 40 ficheros) y los topes tampoco: 10 USD por
  intento, el tope de turnos y 250 USD por tarea. Una tarea `single` que no cabe se parte en
  `parts` igual que antes.
- **La referencia del historial es más débil bajo `single`.** `cost_history.build` descarta los
  documentos cuyo `experiment.plan_shape_of` no es `free`: el plan `single` se calibra contra los
  baldes de tareas `free`, que no incluyen documentos `single`, y el historial `free` no se mueve.
  La protección contra una estimación baja es por tanto más floja en ese brazo.
- **El hash del prompt `tech_lead` se mueve una vez**, en este merge (la plantilla gana
  `{{plan_shape_rule}}`, vacío en `free`). Es igual para los dos brazos; ninguna ventana de
  comparación puede cruzarlo.

## Diseño del experimento (fijado de antemano)

- **Unidad:** intención de tratar. Cada tarea que el poller asignó a un brazo cuenta para ese
  brazo. Las particiones cuentan como particiones y quedan fuera de costo y tiempo.
- **Mínimo:** 10 tareas construidas por brazo bajo una misma línea base; con menos, el resultado es
  "indeciso".
- **Productos:** agentic-system y apostrophe se juntan y cada uno se reporta aparte. Si van en
  direcciones opuestas en costo o tiempo de ciclo, el resultado es "indeciso".
- **Lectura secundaria** por bandas de `estimate.files` (hasta 10, 11-25, más de 25); no decide.
- **Medidas:**
  - Retrabajo y costo por entrega: del scorecard por brazo.
  - Tiempo de ciclo: de las cifras de tiempo de `eval compare` por brazo.
  - Hallazgos escapados: entradas de la lista `findings` del frontmatter con `location` que empieza
    por `full_checks:` (`rules.FULL_CHECKS_PREFIX`) o igual a `PM_FINAL.name` (el hallazgo que
    escribe la validación final), en cualquier estado salvo `dismissed`, sin las filas `copy:` ni
    el aterrizaje, divididas por las tareas construidas. Se clavan por `location` y no por
    `subtask`: bajo `single` no hay integración y la validación final apunta a la única subtarea
    (`final_validation._verdict_patch`), de modo que `subtask` mezclaría con los hallazgos de la
    revisión de esa subtarea y sesgaría la medida contra `single`. Se cuentan a mano hasta que una
    tarea de seguimiento lo agregue a `eval compare`.
  - Fallas de `limit` o tope de turnos del Developer, y fallas de plan del Tech Lead que llevan
    `PLAN_SHAPE_REFUSAL`: se reportan aparte y no cuentan como fallas propias (el costo del
    reintento queda en el costo).
- **Regla de decisión:** se adopta si el costo por entrega agrupado y la mediana del tiempo de
  ciclo no suben frente a `free`, y los hallazgos escapados por tarea y las fallas de
  `limit`/tope de turnos no superan los de `free`. Se rechaza si cualquiera empeora. Cualquier otro
  caso es "indeciso". Adoptarlo por defecto es otra tarea.

## Consecuencias

- Una tarea `single` carga con todo el código y los tests en un solo Developer (en torno a 7
  módulos de producción); si llega a `limit`, es el primer dato del brazo, no una razón para subir
  un tope.
- Con 10 a 20 tareas la potencia es baja; "indeciso" es un resultado probable.
- Declarar `.agentic/experiment.yaml` es un paso del dueño y esta tarea no lo hace.
- Fuera de alcance: que `required_paths` acepte un directorio como `docs/adr/` (compuerta de
  revisión, #510); esta tarea no toca esa compuerta.
- Fuera de alcance: un plan `free` con una sola subtarea sigue descartando en silencio el texto de
  `integration`.
