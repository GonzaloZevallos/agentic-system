---
status: accepted
date: 2026-11-20
---

# El dimensionador temprano parte una función de tamaño épico antes del diseño

Enmienda: ADR-0054. GH-999, parte B (GH-1027). La parte A dejó las piezas puras
(`early_split.verdict`, `DEFINITION_SIZER`, `schemas.early_parts_check`, `rules.early_size_due`);
esta parte las conecta al grafo con el nodo `early_size`.

## El corte temprano y cuándo corre

Justo después de que PM aprueba el PRD (`definition_ready`), `rules.after_definition` manda a la
tarea a `early_size` mientras `rules.early_size_due` sea verdadero: antes de `ux_flow`, `ux_walk`,
`ui_screens`, `design_review`, `consolidate`, `write_copy` y el plan. Solo corre para un `feature`
en la ruta `full` sin spec, que no sea una parte de una división, ni esté promovido, ni sea una
evaluación, y antes de que haya corrido cualquier rol del plan o de la escalera de diseño. Si la
decisión es `split`, el nodo escribe `parts` y el grafo sigue por `split` sin cambios; si es `go`,
`rules.after_early_size` sigue por la escalera de diseño o el plan.

## El factor 2 de "claramente por encima"

Solo se parte con un tamaño declarado por encima de `EARLY_SPLIT_FACTOR` (2) veces un tope, tal
como se declaró, sin multiplicar por el piso. Se revisa después de 10 filas de `early_split` cuyo
plan quedó medido: el factor se compara con lo que el plan terminó midiendo.

## Falla abierta

Un rol que no entrega, o unas partes que `early_parts_check` rechaza, dan `go`: la medición queda
en una fila `sizer_failed` o `parts_refused` y la tarea sigue. Una segunda falla cierra la medición
(`rules.early_measured`): se reintenta una sola vez, en otra invocación. Solo el tope de la tarea
escala, y el nodo nunca aplica el tope de estimación ni escribe `estimate`, `planned_size`,
`subtasks` ni una sección.

## Las partes citan el PRD

Cada parte cita literalmente un bloque `>` del PRD de PM y lleva `#<issue>`; `early_parts_check`
lo verifica contra la sección de PM. La línea de banderas (Flags) es un traspaso al siguiente rol,
no un dato.

## Decide el primer PRD aprobado

El primer PRD aprobado decide. Un PRD que crece después lo detecta la compuerta de tamaño del
plan (ADR-0054), que sigue intacta.

## El conocimiento de la épica no llega a master

La aprobación de PM destila en la rama de la tarea, y una división no abre ningún pull request:
el conocimiento de la épica nunca llega a master. No hace falta ningún cambio.

## El costo

El nodo lee los documentos de las tareas cerradas para armar el historial de costos
(`closed_documents`), y el plan lo vuelve a hacer: dos lecturas completas por función, lineales en
la cantidad de tareas cerradas y acotadas a una por tarea (más el reintento de una falla).
