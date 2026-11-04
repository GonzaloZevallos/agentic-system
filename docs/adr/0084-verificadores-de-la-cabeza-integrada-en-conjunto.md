---
status: accepted
date: 2026-11-04
---

# Verificadores de la cabeza integrada en conjunto

Tras la integración, `copy_review`, `final_validation`, `qa_performance` y `full_checks` corrían
uno tras otro (`rules.after_integrate`), aunque los cuatro solo leen la misma cabeza aprobada. En
33 tareas recientes de apostrophe el conjunto casi no se usaba (`copy_review` en 2,
`final_validation` en 2, `qa_performance` en 0), pero crece con el trabajo de UI.

**Decisión del dueño (2026-11-03):** los verificadores de solo lectura que aplican a una tarea
arrancan juntos sobre la misma cabeza; `deliver` espera a todos; una devolución lleva los
hallazgos de todos; lo que cada uno juzga no cambia.

## Decisión

- **Un solo nodo `verify`, no un fan-out de LangGraph.** En un superstep, los reductores `last`
  de `status` y `escalated` y el `merge` de `subtasks` harían que una devolución pisara a otra.
  `application/verifiers.verify` despacha los verificadores pendientes (`rules.verifiers_due`) en
  un task group de `anyio` sin cancelar a los hermanos. El adaptador envuelve a cada uno como
  `_node` envuelve a un rol, así que conserva su span y su etiqueta de costo; los spans ahora se
  superponen en el tiempo.
- **`combined_return` es el único escritor.** La función pura `rules.combined_return` arma el
  parche de retorno (`status`, `reason`, `returned_by`, `returned_with`, `escalated`). Con un
  solo verificador devuelve su parche tal cual. Con varios, el primero en el orden es
  `returned_by` y los demás van en `returned_with`; todo lector pasa por `rules.returned_by_all`.
- **Orden fijo.** Los deltas se componen siempre en el orden `full_checks`, `copy_review`,
  `final_validation`, `qa_performance`, nunca en el de llegada, para que las posiciones de `runs`
  que lee `_latest_index` no dependan del tiempo. Si un hermano escala, el parche combinado
  escala y conserva los deltas de los demás; si uno lanza una excepción, se persiste primero el
  delta fusionado de los otros y después se relanza.
- **Cuándo entra `full_checks` al conjunto.** Solo cuando hay que esperar el veredicto del CI de
  GitHub (`full_checks_mode == "wait"`: producto con `ci:`, cabeza confiable y empuje especulativo
  no consumible). Si el veredicto ya se puede consumir, o la suite corre en local, corre primero y
  solo, como antes; un rojo devuelve sin gastar modelo.
- **`close_docs` diferido.** `final_validation` ya no commitea dentro del conjunto: su aprobación
  deja `pending_close_docs: {parent, definitions}`. `verify` commitea una vez, solo si `parent` es
  la cabeza revisada actual (`same_commit`) y nada devolvió; si no, limpia el campo. Cualquier
  devolución y `fix_important` también lo limpian, y mientras exista `after_integrate` vuelve a
  `verify`, de modo que ninguna ruta lo deja puesto y va a `deliver`.
- **Un checkout.** `verify` resuelve el checkout de la tarea una sola vez y comprueba una vez que
  el árbol esté limpio. Ningún verificador del conjunto resuelve el checkout ni llama a
  `restore_clean`; `full_checks` en modo local, que corre solo y primero, es la excepción.

## Riesgos aceptados

- Una muerte dura (SIGKILL, OOM) dentro de `verify` pierde lo que estaba en memoria de varios
  roles a la vez. Es rara: estos roles corrieron en 2 de 33 tareas.
- En la rama «CI por esperar», PM, Tech Writer y QA pueden gastar modelo sobre una cabeza que el
  CI después devuelve. Es el precio de la decisión del dueño, acotado a esa rama.

## Consecuencias

- `fix_important` y `owner_rules` siguen en serie antes del conjunto, porque mueven la cabeza.
- El grafo pierde los nodos `full_checks`, `copy_review`, `final_validation` y `qa_performance`;
  gana `verify`. Los casos de uno solo producen el mismo estado y el mismo documento que antes.
