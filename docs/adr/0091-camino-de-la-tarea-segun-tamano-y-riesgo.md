---
status: accepted
date: 2026-11-11
---

# Camino de la tarea según tamaño y riesgo

GH-591, entregado en tres partes. La parte 1 sólo REGISTRA el camino: PjM declara `size` y
`risk`, el sistema decide el camino inicial y lo guarda en el documento. Ningún nodo, predicado,
`after_*` ni `plan_check_for` lee `path` todavía, y los tests de ruteo y de grafo existentes
pasan sin editarse: esa es la prueba de que el comportamiento no cambió. Las partes 2 y 3 enrutan
y miden.

## Contexto

Hoy toda tarea recorre el camino entero de roles, cualquiera sea su tamaño. Una tarea pequeña y
sin riesgo paga el mismo plan, entrevista y revisión que una migración de datos. Se quiere un
camino corto cuando el cambio es chico y no toca nada delicado, sin que el atajo se pueda abrir
por una entrada que falta.

## Decisión

**Dos caminos.** `light` y `full`. `full` es el de hoy. `light` omite la entrevista de solución
y la crítica del plan, y las partes 2 y 3 dicen qué más (esta parte no omite nada).

**Tope de dos subtareas.** En `light` el plan tiene como máximo 2 subtareas, también en
`platform_enhancement` (un cambio de proceso). Si el plan lo supera, el sistema promueve con
`plan_over_cap`. Bajo `path:light` la promoción no aplica nada (ver abajo), así que la parte 2
debe decir qué hace un plan que supera el tope bajo esa etiqueta.

**Una sola función decide.** `task_path.decide(type, size, risk, labels, known_risks)` devuelve
el camino y su causa (`path_cause`); la primera condición que aplica gana:

1. Etiquetas `path:light` y `path:full` a la vez, o etiquetas ilegibles: `full`.
2. `size` o `risk` ausentes, un id de riesgo desconocido o un stack ilegible: `full`, sea cual
   sea la etiqueta. Con el stack ilegible el resultado es `full` incluso con `risk=[]`, porque
   PjM vio un menú incompleto.
3. `path:full`: `full`. `path:light`: `light`.
4. Un riesgo marcado o un tamaño mayor que `small`: `full`; `small` sin riesgos: `light`.

`type` está en la firma porque las partes siguientes lo necesitan, pero no cambia ninguna fila.

**La etiqueta del dueño.** `path:light` le gana a un riesgo marcado y válido y a un tamaño mayor
que `small`; nunca a una entrada faltante, desconocida o ilegible. Una etiqueta ilegible da
`full`: no leer la etiqueta nunca se toma por "no hay etiqueta". Las etiquetas del issue ya se
leían; se lee `IssueState.labels`, donde `None` es "no se pudo leer".

**Riesgos cerrados.** Seis ids del sistema, comparados exactos: `data-migration`,
`money-rounding`, `authn-authz-sessions`, `tenant-isolation`, `user-data-deletion` y
`public-api-break`. El producto suma los suyos con `risk_areas` en su stack (id en slug que no
repite uno del sistema, descripción y `paths` opcional, globs fnmatch que en esta parte sólo se
leen). Una declaración inválida falla en la Puerta de arranque. El stack se lee una vez en
`classify`, en el commit base, y el mismo valor alimenta el menú del prompt y la validación.

**Dos redes de riesgo.** La primera es PjM, que marca riesgos al clasificar; la segunda, de la
parte 3, es `risk_hits`: los `paths` de las áreas de riesgo comparados con el diff real. Un acierto
de la segunda red promueve con `risk_area`. Una sola red no basta: PjM no ve el diff.

**Promoción de una sola vía.** `task_path.promote` es el único reductor que cambia `path`, sólo de
`light` a `full`, y la segunda promoción no hace nada. Escribe `path` y `promotion`
(`from_role`, `kind`, texto de una línea de hasta 240 caracteres, `at`) y nada más: no es una
devolución, no es un escalado y no toca contadores. Los commits ya hechos se conservan, y el
nuevo plan retoma la entrevista. `kind` es uno de `plan_over_cap`, `risk_area`, `owner`, `role`.

**Campos del documento.** `initial_path`, `path`, `path_cause`, `path_label`
(`light`, `full`, `both`, `unreadable`, `none`) y `promotion`. Un documento viejo sin ellos se
lee como `path=full`, `path_label=none` y `path_cause=unknown`; no hay migración.

**Medición.** Toda mediana por camino agrupa por `initial_path`, no por `path`, junto con
`path_cause`: agrupar por `path` mezclaría las promovidas con las que nacieron `full`. Tamaño,
riesgo y camino viven sólo en el documento; esta parte no agrega atributo de span ni métrica, y
ningún texto libre de una promoción ni id de riesgo del producto llega a una señal.

## Consecuencias

- El hash del prompt de PjM cambia, como en toda edición de prompt; nada decide por él.
- Cuesta un `light` cada vez que una lectura transitoria del stack o de las etiquetas falla; queda
  visible en `path_cause`.
- `type` queda en la firma sin efecto hasta que una parte lo use.
