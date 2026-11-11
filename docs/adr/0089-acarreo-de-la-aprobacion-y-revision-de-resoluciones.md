---
status: accepted
date: 2026-11-11
---

# Acarreo de la aprobación y revisión de resoluciones

GH-589. Cuando master entra en la rama de una tarea aprobada, hoy el revisor vuelve a leer
`reviewed_head..head` entero (`head_not_reviewed` bloquea el merge hasta entonces), lo que trajo
master incluido. En apostrophe, el 2026-11-10/11, pasó en cada una de las ~40 pasadas de
integración contadas para GH-403, GH-406, GH-407 y GH-409. Este ADR enmienda la regla de acarreo
de ADR-0048: ahora la decide una sola función pura, `landing.review_scope`, y distingue tres
alcances.

El problema ocurre en el aterrizaje (`landing_sync.sync_master` y el `re_review` que sigue), no
en el nodo `integrate` previo al pull request: ahí la integración se revisa por primera vez y no
hay aprobación que acarrear.

## Decisión

- **Tres alcances.** `review_scope(facts)` devuelve `ReviewScope` (`carried`, `resolution`,
  `full`) y la causa `ScopeCause` que lo dio.
  - `carried`: el merge de master fue limpio y no hay commits propios después. La aprobación
    avanza a la cabeza nueva sin revisión.
  - `resolution`: la cadena de primer padre desde la punta aceptada tiene un merge del upstream
    con resolución, o commits propios después del primer merge. El revisor recibe solo el
    remerge-diff (`git show --remerge-diff`) de cada merge más el diff de los commits posteriores;
    su veredicto cubre solo eso, y la aprobación del resto se mantiene.
  - `full`: todo lo demás. La primera revisión y la incremental sin merge quedan iguales que hoy,
    byte a byte (mismo prompt, sin `RESOLUTION_SECTION`).
- **Causas, una condición cada una.** `first`, `no_merge`, `clean`, `resolved`, `rewritten` (la
  punta aceptada no es ancestro de la cabeza), `own_before_merge`, `foreign_merge`, `unreadable`,
  `no_remerge_diff`, `over_cap` y `renumber`. Cualquier `GitError` al reunir los hechos da
  `full` con `unreadable` o `no_remerge_diff`; nunca `carried` ni `resolution`.
- **Sin `git range-diff`.** La rama se sincroniza por merge, no por rebase, así que "los commits
  propios no cambiaron" no necesita range-diff: se prueba con hechos estructurales
  (`is_ancestor` y `first_parent_commits`). Un commit propio antes del primer merge, un merge que
  no es del upstream o una punta reescrita dan `full`.
- **`review_carries` queda** solo para los tramos aterrizados (GH-297), donde conviven el código
  viejo y el nuevo; ningún otro módulo prueba limpieza o ancestría para acarrear una aprobación.
- **Tope del texto.** `RESOLUTION_LINES_MAX = 600`, declarado, no medido. El texto de resolución
  nunca se trunca: por encima del tope el alcance es `full` con causa `over_cap`. Con 600 líneas
  es `resolution`; con 601, `full`.
- **Registro.** `review_scopes` es una lista de solo anexar en el documento, con filas
  `{unit, head, base, scope, cause, at}`. La escribe solo el reductor `landing.scope_patch`, en el
  mismo patch que mueve `reviewed_head` (y `reviewed_head_carried_from`, solo en `carried`).
  Ningún veredicto, puerta ni paso de aterrizaje la lee, así que un documento viejo, sin la clave,
  no cambia ninguna decisión.
- **Git 2.36 o más.** `--remerge-diff` nació en 2.36. El puerto `Workspace.merge_resolution`
  lanza `RemergeDiffUnsupported` (un `GitError`), sin correr el comando, con una versión menor o
  desconocida (`Workspace.git_version`); el modo resolución cae a `full` con causa
  `no_remerge_diff`. El `carried` del aterrizaje no lee el remerge-diff, así que no depende de la
  versión.
- **Exclusiones.** Las ramas que aprueba el sistema en el brazo `single` y las revisiones sombra
  (ADR-0088) no invocan `review_scope` ni escriben `review_scopes`.

## Riesgos que quedan

- **Choque semántico en un merge limpio.** Un merge limpio ya no lo lee un revisor: dos cambios
  que no se tocan en el texto pero se rompen entre sí pasan sin ojos humanos ni de modelo. Lo
  cubren los chequeos sobre la cabeza nueva en la puerta de merge (`merge_ci` con `ci:`, la suite
  local sin él). Un resultado reutilizable se firma por árbol, así que la cabeza nueva nunca hereda
  el pase de la anterior.
- **Resoluciones por otra vía.** Una resolución hecha con `squash`, `cherry-pick` o `rebase` no
  deja un merge del upstream en la cadena de primer padre y cae a `full`, con su causa registrada.
- **Ahorro sin medir.** En GH-403/406/407/409 predominan los conflictos, así que el ahorro
  principal es el alcance `resolution`, no `carried`. El tope de 600 líneas es una elección, no
  una medida.

## Consecuencias

- Una pasada de revisión cuesta lo que cambió en las resoluciones, no lo que trajo master.
- Seguimiento: cuando haya corridas, contar los alcances y las causas de `review_scopes` para
  medir el ahorro y ajustar el tope.
- Los documentos de tareas anteriores no tienen `review_scopes` y se leen igual que antes.
