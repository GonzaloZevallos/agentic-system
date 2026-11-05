---
status: accepted
date: 2026-11-05
---

# Un crítico adversario revisa el plan y la entrevista queda para las tareas grandes

GH-472. Desde el 27-10 la planificación es el 47,8 % del costo de los roles en agentic-system
(420 de 878 USD en 38 tareas) y el 27,4 % en apostrophe. La entrevista entre el Tech Lead
efímero y el Tech Lead (ADR-0014) cuesta hasta cuatro rondas, o una si el pedido trae
`## Decided`. El brazo `skip_ephemeral` planifica sin entrevista, pero el tamaño de la tarea sale
del propio plan, así que nada podía decidir por tamaño antes de entrevistar.

El dueño decidió el 2026-11-03: el plan pasa por un único crítico adversario; la entrevista
completa queda solo para las tareas con una estimación grande. Se prueba como A/B.

## Decisión

- **Un brazo nuevo, `plan_review`** (`interview` | `critic`, por defecto `interview`), tercer
  interruptor de `ARM_SWITCHES` bajo los experimentos por tarea (ADR-0081). Con todos los
  interruptores en su valor por defecto nada cambia: ni prompts, ni documento, ni
  `experiment_snapshot`. `critic` no se combina con `skip_ephemeral`, y un experimento donde unos
  brazos fijan `plan_review` y otros no es inválido: el brazo de control debe nombrar
  `plan_review: interview`.
- **Un estimador previo en todo brazo que nombra el interruptor.** El rol `plan_sizer`, sin
  herramientas, lee el pedido y devuelve costo y archivos estimados. Corre en cada tarea cuya
  asignación lleva `plan_review` (y, sin asignación, con `plan_review=critic`), de modo que el
  control se mide en la misma escala. Su costo va en una columna aparte.
- **Ruta y re-compuerta a 55 USD.** `plan_route.route` manda a la entrevista cuando el
  interruptor es `interview`, cuando el estimador falló o cuando la estimación previa alcanza el
  umbral; si no, va al crítico. 55 USD (`plan_interview_min_usd`) es el p75 de las 95
  estimaciones del historial, así que cerca del 72 % de las tareas toman la ruta del crítico.
  `plan_route.regate` repite la decisión con la estimación del borrador frío: si la alcanza, la
  tarea estaba mal ruteada (`misrouted`) y corre la entrevista de siempre.
- **Un crítico y una revisión, con vuelta al borrador.** El Tech Lead planifica en frío; un
  crítico (`tech_lead_critic`, con el modelo distinto del planificador) revisa el borrador en
  contexto nuevo y devuelve hallazgos con evidencia más invariantes propuestos; el Tech Lead
  revisa una sola vez. Si el crítico falla tras su reintento, se entrega el borrador y queda
  `critic: failed`.
- **Medición** (`agentic eval compare`, lectura de documentos, ADR-0037): por brazo y por ruta,
  devoluciones `plan-calibration` por tarea, costo y tiempo de planificación
  (`PLANNING_SLUGS`, unión de intervalos), costo del estimador, tareas mal ruteadas, críticos
  fallidos y los pares estimación previa / estimación del plan. Un documento sin `plan_route`
  cae en el cubo `unsized`.

Enmienda ADR-0014 (la etapa de solución puede ser un solo pase de crítico bajo un brazo) y
ADR-0030/ADR-0081 (tercer interruptor de brazo, y el control debe nombrarlo).

## Consecuencias

- El estimador no ve el repositorio, así que puede estar sesgado frente a un umbral calibrado
  sobre la estimación del plan. Los pares de `eval compare` son la señal para recalibrar.
- Un borrador que se subestima solo recibe al crítico; la red es el tope de estimación (ADR-0054).
- La comparación solo vale para tareas chicas, porque las grandes reciben la entrevista en ambos
  brazos.
- Una tarea que se reanuda tras una caída dentro del nodo del plan vuelve a empezar por el
  estimador y paga la planificación otra vez.
- La etiqueta de un brazo no por defecto gana `plan_review=<v>`: una serie de un brazo anterior a
  esta decisión se parte en el límite.

## Opciones descartadas

- **Decidir por el tamaño del borrador solamente**: obliga a pagar el borrador de las tareas que
  igual irían a entrevista.
- **Quitar la entrevista de todas las tareas** (`skip_ephemeral`): pierde las invariantes que
  destapa la entrevista en tareas grandes.
- **Un umbral fijo en el código**: es un parámetro que se recalibra con datos, vive en
  `Configuration`.
