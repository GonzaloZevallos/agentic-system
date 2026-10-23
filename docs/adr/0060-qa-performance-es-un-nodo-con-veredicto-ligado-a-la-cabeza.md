---
status: accepted
date: 2026-10-23
---

# QA Performance, parte 2: un nodo con veredicto ligado a la cabeza que puede detener el auto-merge

GH-318, segunda parte. La medición y el verbo `agentic perf` son del ADR-0059; este ADR
agrega el rol, su nodo y la compuerta. Extiende la frase del ADR-0059 "esta parte no cambia
la compuerta de merge ni el aterrizaje": desde aquí sí, y sólo para un plan que convoca
rendimiento. (Las citas "ADR-0058" del pedido se corrigen: ADR-0059 para la medición, éste
para el rol.)

## Decisión

- **Convocado sale sólo del plan.** `TechPlan.performance` (lista de `PerfScenario` con
  `volume`, `volume_unit` y `budgets`) se guarda en el documento; vacía significa no
  convocado y todo documento existente carga igual. Nunca se lee de la `stack.yaml` de la
  rama. Un presupuesto `proposed` fija su número al planificar, con su `basis`.
- **El nodo y su veredicto.** Después de la integración y de la validación final de PM, el
  sistema mide la cabeza integrada y el rol lee la tabla. El veredicto es `approved`
  (todos los presupuestos `within` y ningún hecho ni hallazgo bloqueante), `returned` (algún
  `over`, un escenario ausente, un `limits_mismatch`, un límite aflojado respecto de la base
  o un hallazgo bloqueante del rol) o `unverified` (algo `inconclusive` y nada de lo
  anterior). `unverified` cuenta como corrido; `deliver` lo declara en el cuerpo del PR.
- **Veredicto ligado a la cabeza.** La corrida guarda `perf_verdict` y `perf_head` (sha
  completo medido). `auto_merge_decision` rechaza con `perf_unverified`, justo después de
  `head_not_reviewed`, si se convocó rendimiento y la última corrida no es `approved` o su
  sha no es `same_commit` con la cabeza del PR; un sha ausente falla cerrado.
  `landing_step` lo mapea a `none`: el PR queda para el humano.
- **Ningún rol recibe Bash para `agentic perf`.** El verbo toma límites, volumen y código
  de la cabeza del producto, no del plan y la cabeza integrada. Una sola vía mide: el nodo,
  con `measure_at` en un worktree `perf-` desechable, bajo el `PerfLock`, con `tries=2` (hasta
  2 × `perf_lock_wait_s` esperando con un cupo ocupado, a diferencia del verbo, que trata
  `busy` como final). El revisor de integración recibe la última tabla; la evidencia de que
  un hallazgo de rendimiento está arreglado es la re-medición del nodo tras la reaprobación.
- **Filas del sistema.** Los claims de las filas de QA Performance los construye el sistema
  con plantillas fijas (`<escenario>/<métrica> over <límite><unidad>`, `<escenario> limits
  mismatch`, `<escenario> missing at head`, `<escenario> limits loosened`), ubicación `QA
  Performance`, `scope: plan`. Una corrida aprobatoria asienta toda fila abierta en esa
  ubicación: un escritor nuevo del pliegue (addendum del ADR-0015).
- **`returned_by` total.** `review`, `final_validation` y `qa_performance` mapean a su
  sección en `rules.return_section`; cualquier devolución de revisión repone el valor a
  `review`.
- **Presupuestos de sólo lectura tras planificar.** Ninguna decisión humana edita uno;
  cuando el par QA llega a su límite, la escalación lleva la última tabla y dice que un
  límite distinto exige cerrar el PR y reabrir el issue.

## Costo

Un nodo extra por tarea convocada: la medición (hasta 2 × `perf_lock_wait_s` esperando más
`perf_node_timeout_s` midiendo) y un intento del rol. Hasta 2 devoluciones de rendimiento,
cada una repite Developer, revisión, validación de PM (UI) y QA; las de PM repiten QA. Peor
caso en una tarea de UI: 4 nodos QA extra y 2 validaciones finales extra. Una tarea no
convocada no cuesta nada más; el texto del prompt del Tech Lead sí cambia para todas.

## Riesgos abiertos

- Un paso del aterrizaje que mueve la cabeza deja el PR convocado al humano: ningún paso
  re-mide (queda un paso `re_measure` como seguimiento).
- Un presupuesto `proposed` no tiene medición del código sin cambios (medir en la base queda
  como seguimiento).
- `perf_spread_max` no está calibrado (ADR-0059): `unverified` puede ser común al principio.
- Un `measure` que imprime la unidad equivocada se cree.
- La atribución `Origin` del playbook y `reflect.py` caen en `review` para estos hallazgos.
