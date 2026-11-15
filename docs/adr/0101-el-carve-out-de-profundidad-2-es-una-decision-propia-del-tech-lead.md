---
status: accepted
date: 2026-11-15
---

# El carve-out de profundidad 2 es una decisión propia del Tech Lead

Enmienda: ADR-0093

GH-722, parte 3 de GH-722, sobre las reglas de GH-719. El addendum de ADR-0093 «Addendum
(GH-719, 2026-11-13): las reglas del carve-out» dejó las reglas puras y dijo que nada en `plan`
las llamaba todavía, y cerró con una lista «La parte 3 debe…». Ninguna de las dos frases
describe ya el código: el nodo del plan llama al paso y la lista está cumplida. Este documento
dice cuándo corre el paso, cómo termina y qué riesgos deja. El marcador, la línea `Blocked by:`,
la sección `## Carved-out steps`, las etiquetas excluidas y los dos niveles de rechazo siguen
como los fija ese addendum, y no se repiten aquí. ADR-0093 sigue vigente.

## Decisión

**Cuándo corre.** Solo en una parte de profundidad 2 cuyo plan no cabe (`fit_question`), cuyo
documento no tiene todavía un registro `carve_out` y cuya corrida escribe en el tracker
(`carve_out_due`, `writes_tracker`: nunca en una corrida de evaluación). Hay un solo intento
por tarea: una vez que el registro existe, el paso no vuelve a estar debido.

**Quién decide.** Es una llamada propia del Tech Lead (`TECH_LEAD_CARVE_OUT`), no uno de los 19
roles de la lista. Elige subtareas enteras. El resto se juzga con piso, con la comprobación de
ajuste que el plan ya tiene (`judge_remainder`), y pasa además el plan check de la rama; nada
más lo juzga.

**El registro.** `carve_out` lleva un `outcome`:

- `applied`: lo escribe `finish_carve` al cerrar el paso.
- `failed`, sin `cause`: lo escribe `_failed` ante un fallo del rol o de su esquema, un
  rechazo, `CarveRefused`, el plan check o un `finish_carve` que no puede cerrar.
- `refused` con `cause: no_issue`: la parte no tiene número de issue.

`refused` con `cause: evaluation` existe solo como defensa dentro de `apply_carve`: el nodo del
plan nunca llega ahí, porque en evaluación el paso no corre.

**Dos finales de pausa, y el poller no retoma ninguno.**

1. Estimación. `_over_line` llama a `rules.part_over_line` y pausa con la clase `estimate` y
   `fit_question`. Llegan a ella `failed`, `refused`/`no_issue` y un `applied` cuyo resto, con
   piso, no cabe (el registro queda `applied`, no se encola ninguna subtarea y la pregunta
   lleva las cifras del resto). Sigue solo con una decisión del humano y luego
   `agentic resume`; el carve-out no se repite.
2. Tracker. `_stopped` pausa con la clase `failure`, sin `outcome`: el registro queda pendiente
   (`carve_out_pending`). `agentic resume` vuelve al nodo por `_resume`: no corre ningún rol, no
   hace el plan check y salta los pasos que el registro muestra hechos.

**Seguridad y costo.** El texto del modelo llega a un issue público solo después de
`text_refused`; las etiquetas pasan por `copied_labels`. Los issues abiertos se leen
(`open_issue_refs`) una vez por paso que los necesita (buscar un seguimiento existente y
re-apuntar): a lo sumo dos lecturas por intento de aplicar. Una lista cortada (`ListMayBeCut`)
detiene el paso en vez de abrir un duplicado.

## Riesgos conocidos

- Ningún adversario revisa el resto: lo juzgan solo el ajuste con piso y el plan check.
- Una ruta movida que se queda en el resto pasa la regla del subconjunto.
- `required_paths` es any-of: tocar una de las rutas basta.
- El seguimiento se abre antes de saber si el resto cabe. Si `_land` pausa (falla o por encima
  de la línea), el seguimiento ya existe con `Blocked by: #<parte>`; si el humano detiene la
  parte, tiene que cerrar el seguimiento a mano.
