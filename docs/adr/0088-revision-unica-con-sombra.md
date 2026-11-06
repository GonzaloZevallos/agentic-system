---
status: accepted
date: 2026-11-06
---

# Revisión única con sombra

GH-473. Hoy cada subtarea tiene su propia revisión del Tech Lead y después la integración tiene
otra (ADR-0004, adenda quinta). El dueño decidió el 2026-11-03: el Tech Lead hace una sola
revisión fresca del código integrado, con a lo sumo 2 rondas de arreglo; si todavía no pasa, la
tarea va al dueño. La puerta automática (`full_checks`) corta antes una suite roja. Durante un
período de prueba la revisión de hoy corre en paralelo como sombra que no bloquea, y el sistema
registra lo que la revisión nueva no ve.

Este ADR reemplaza la adenda quinta de ADR-0004 **solo para el brazo `single`**: con `full` y
`light` el flujo, los documentos y los hashes de los prompts son los de siempre, byte a byte.

## Decisión

- **El interruptor.** `Configuration.review_mode` gana el valor `single` (`full | light | single`)
  y `shadow_grace_s: int = 180`. `single` entra en `ARM_SWITCHES` y en `EXPERIMENT_KEYS` como
  los otros brazos; `shadow_parallel` y `shadow_grace_s` no son brazos y nunca entran en
  `experiment_snapshot` ni en un hash de prompt.
- **El sistema aprueba las ramas.** `rules.system_approves(state, subtask_id, mode)` es verdadera
  con `mode == "single"`, una subtarea que no es la integración y una tarea con más de una
  subtarea. El nodo `review` vuelve al principio sin correr un rol: escribe `approval_patch` con
  `findings=[]` sobre la cabeza entregada y la sección `approved by the system: review_mode
  single`. No escribe hallazgos ni `ci_speculation`. La topología del grafo no cambia.
- **Una sola puerta de aprobación.** La aprobación del sistema y la del revisor pasan por
  `rules.approval_patch`; ninguna escribe `approved` de otro modo. Una subtarea que traía una
  devolución abierta de otro verificador (`returned_by`) la cierra con el mismo patch.
- **Las dos rondas son el contador del par.** El tope es el contador de `pair_for(INTEGRATION)`
  leído por `rules.remaining_changes` (`max_changes_requested_per_pair = 2`); ningún nodo ni
  adaptador cuenta rondas por su cuenta. Una tercera devolución bloqueante escala con la clase
  `returns`. Las devoluciones de los verificadores (`final_validation`, `qa_performance`) ya
  cuentan en claves propias, y `fix_important` y el aterrizaje no tocan `changes_requested`:
  quedan fuera de las dos rondas. `full_checks` compartía la clave de la integración; bajo
  `single` cuenta en `Tech Lead→Developer:<destino>-full_checks`, que solo construye
  `rules.full_checks_pair`, la misma en `full_checks_return`, `limit_reached`, `pair_at_limit` y
  `resumed`. Con `full` y `light` es `pair_for(destino)`, como hoy.
- **`run_reviewer`.** La corrida del revisor (rol, secciones extra, chequeos y formas afectadas,
  invariantes, `docs_review`, `landing_gate` y tabla de rendimiento) se extrajo de `review()` a
  `review.run_reviewer(..., with_developer, ci_names)`, que llama a `run_role` y devuelve
  `(output, patch)`. `review()` conserva el push, `read_ci`, el rebajado, `required_paths`, los
  registros y el estado. La revisión de integración bajo `single` la llama con
  `with_developer=False`, sin las secciones del Developer. La sombra usa la misma función: ningún
  módulo guarda una segunda lista de secciones o argumentos del revisor.

## La sombra

- **Qué hace.** En el primer pase del nodo de integración bajo `single` (todavía sin
  `reviewed_head`), por cada subtarea que no es la integración y aún no tiene registro, abre un
  checkout separado en su `reviewed_head`, preparado con el `setup` del producto, corre
  `run_reviewer(mode="full", with_developer=True, ci_names=())` con sus propios `RunLimits` y lo
  borra en un `finally`. Escribe solo un `ShadowRecord` (`ran | late | unavailable | skipped`, con
  su causa) y su sección `Tech Lead: shadow review of <id>`, que ningún rol lee. Nunca llama a
  `finish`.
- **Nunca decide nada.** Ningún camino escribe su salida en `findings`, `settled_findings`,
  `changes_requested`, `runs`, `failures`, el `status` de una subtarea ni `escalated`; solo
  `shadow_reviews`, `shadow_wait_s`, su sección y su costo en `cost_usd`. Una falla, un error de
  `setup`, un tope o un desborde queda `unavailable`, `late` o `skipped`; nunca es una falla de
  rol, un reintento ni una escalación. Toda la exclusión vive en `shadow_review.run_one`, que
  llama con `cfg.model_copy(update={"role_retries": 0})`; `run_role` no recibe ninguna marca.
- **Concurrencia.** Corren a la vez `rules.shadow_slots(shadow_parallel, max_concurrent_roles) =
  min(shadow_parallel, max_concurrent_roles - 1)` sombras, siempre un cupo de rol menos que la
  corrida; con 0, cada sombra queda `skipped`/`cap`. La sombra no toma el semáforo de roles y no
  hay validador de configuración que rechace una combinación de los dos valores. Se lanzan por
  `changed_lines` ascendente (de `Workspace.changed_lines`, sin subprocesos en `application/`),
  con el id de la subtarea como desempate.
- **El plazo.** Después de que vuelve la revisión única, el nodo espera a lo sumo
  `shadow_grace_s`; lo que sigue corriendo se cancela y queda `late` con el costo observado hasta
  entonces. Un `late` cuesta lo observado y no deja hallazgos. El nodo escribe `shadow_wait_s`.
  Una tarea con una sola subtarea registra `skipped`/`single_unit`.
- **Comparar con la revisión única** se decide con una sola función pura sobre campos
  estructurados (`finding_file(location)` y la familia de la categoría), nunca sobre prosa
  (`rules.shadow_match`). Un hallazgo de la sombra cuyo `location` no da un archivo relativo al
  repo es `unlocated`, nunca `missed`.

## Costo

- El costo de la sombra entra en el `cost_usd` de la tarea por el mismo patch que los registros.
- Toda decisión de tope (el tope de tarea en `run_role` y en `landing_plan`, y el de estimación)
  lee el costo por `rules.cost_without_shadow(spent_usd, state)`, que resta la suma de
  `ShadowRecord.cost_usd`: una sombra, que no bloquea, no hace escalar a la tarea. El tablero y el
  scorecard conservan el costo bruto (`rules.task_spent`), de modo que el brazo `single` se paga
  entero en la comparación.
- La sombra no se lanza si el costo bruto más `cupos × max_budget_usd` llega al tope de tarea: queda
  `skipped`/`cap`.
- Los registros viajan en el patch del nodo. Si el proceso muere mientras corren las sombras, se
  relanzan y se pagan otra vez; el gasto lo acotan un intento, 10 USD por intento y los 3
  reintentos de la tarea. Una subtarea que ya tiene registro no se relanza.

## Dos diferencias deliberadas con el control

- La tabla de hallazgos hermanos llega vacía a la revisión única: las ramas no tuvieron revisión
  y por tanto ninguna dejó hallazgos que mostrar.
- Ninguna revisión de rama lleva la sección de CI: el sistema aprueba las ramas sin empujar ni
  dejar `ci_speculation`, y la sombra corre con `ci_names=()`. La puerta automática y
  `full_checks` cubren esa señal antes de la revisión de la integración.

Ambas diferencias son parte del brazo; no se corrigen para parecerse al control.

## Período de prueba y validez

- **Ventana:** 10 tareas `single` cerradas. Concluye solo si al menos la mitad de los
  `ShadowRecord` quedaron en `ran`; si no, se prolonga (por ejemplo subiendo `shadow_grace_s`).
- **Límite de ruido.** Cada tarea da una sola muestra de cada revisión; el desacuerdo entre una
  revisión única y una sombra mezcla lo que la primera no ve con la variación de correr un modelo
  dos veces. Con 10 tareas la señal es débil y "indeciso" es un resultado probable.
- **Sobrevivencia.** Las subtareas largas tienden a quedar `late` o `skipped` (se lanzan las
  cortas primero y el plazo es fijo), así que lo medido sesga hacia las subtareas cortas.
- **Contención de CPU** de los checkouts extra y de los revisores en paralelo: no se midió. El
  tiempo de ciclo del brazo incluye esa contención y no se separa.

## Consecuencias

- Bajo `single` el brazo emite spans de rol del Tech Lead adicionales, uno por sombra; no hay
  etiqueta ni métrica nueva y `changes_requested_total` sigue siendo una suma.
- En el primer pase de la integración hay hasta `cupos` checkouts extra, cada uno con su `setup` y
  un revisor completo; el costo escala con la cantidad de subtareas y termina al vencer
  `shadow_grace_s`.
- Adoptar `single` por defecto es otra tarea, que se decide con la ventana de arriba.
