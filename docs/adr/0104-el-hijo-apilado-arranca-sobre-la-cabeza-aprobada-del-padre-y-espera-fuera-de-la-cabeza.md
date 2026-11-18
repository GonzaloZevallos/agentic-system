---
status: accepted
date: 2026-11-18
---

# El hijo apilado arranca sobre la cabeza aprobada del padre y espera fuera de la Cabeza

GH-838, parte 2 (GH-856). Decisión del dueño del 2026-11-15: la parte N+1 de una tarea dividida
no espera al merge de la parte N. Este documento registra el diseño del arranque apilado y la
pieza que esta parte agrega: la guarda que mantiene a un hijo apilado fuera de la Cabeza y del
Carril rápido mientras su padre no llegó al upstream. Hasta que el lanzamiento apilado (parte
posterior) escriba `stack_parent`, la guarda es inerte: `stack_held` solo es verdadero para un
documento que lo trae.

## Decisión

**Cuándo arranca.** La parte N+1 arranca cuando la parte N está Lista para revisión, sobre la
cabeza aprobada de la parte N (`stack_parent.head`).

**Contra qué rama va el pull request.** El pull request apunta a master, aunque la base del
diff es la cabeza del padre. Todo camino de merge y de aterrizaje supone master y las ramas
nunca se borran, así que apuntarlo a la rama del padre rompería ambas suposiciones.

**El restack.** Cuando el padre llega al upstream, el hijo toma el upstream con un solo merge
por `sync_master`, nunca un rebase: el aterrizaje solo agrega commits sobre la cabeza del padre,
así que un arreglo de aterrizaje del padre no lo desplaza. No se agrega ningún `ScopeCause`
nuevo, porque `merge_ci` juzga un restack limpio. Lo que la revisión paga por él lo fija
`landing.review_scope` y lo muestra `tests/core/process/domain/test_review_scope.py`
(`test_a_restack_after_the_approved_head_is_carried_with_the_cause_clean`): un restack sobre una
cabeza ya aceptada es `carried` con causa `clean`, sin revisión. Si el hijo tuviera commits
propios que la revisión nunca aceptó antes del merge, el alcance sería `full` con causa
`own_before_merge`
(`test_a_restack_over_own_commits_the_review_never_accepted_is_a_full_review`): cada restack
costaría entonces una revisión completa, y eso queda para el lanzamiento.

**La pausa.** Esperar al padre es una retención del poller, no una clase de escalado: nadie debe
decidir nada mientras el padre avanza.

**Merge commits.** El apilado supone que el repositorio mergea con commit de merge. Un
repositorio que solo permite squash deja huérfano a cada hijo (`stack_base_orphaned`), porque la
cabeza registrada del padre nunca queda en el upstream.

**Las guardas llegan antes que el lanzamiento.** La tabla de merge (`stacking.refusal`) y esta
guarda se publican antes de que nada escriba `stack_parent`, de modo que el primer hijo apilado
ya encuentre todo cerrado.

**El interruptor es por producto:** encendido en agentic-system, apagado en apostrophe. Con el
interruptor apagado las decisiones de lanzamiento, merge y aterrizaje son las de siempre.

**Quién queda retenido (`stacking.held`).** Un único predicado, sobre los `StackFacts` del
propio miembro, que `poll._member` aplica a la evaluación ya hecha (sin lecturas nuevas) y
`landing_order` recibe como el campo `stack_held`; `landing_order` no importa `stacking`. Un
miembro retenido sale de la elección de la Cabeza igual que el de una persona, y no recibe el
Carril rápido (`lane_reason` dice `stack_parent_unmerged`, antes que los obstáculos de la
Cabeza). Es verdadero solo cuando `refusal` dice `stack_parent_unmerged`:

- La duda retiene: ascendencia desconocida, estado del tracker desconocido y una cabeza que no
  se puede traer dentro de sus 12 h de espera.
- Una cabeza no traíble cuya espera venció se libera: llega a la Cabeza y `landing_step` la
  escala (clase `questions`).
- `stack_base_orphaned` no retiene: es de una persona (`HUMAN_OWNED_REFUSALS`) y el orden ya la
  descarta por `waits_on_human`.
- `stack_restack_due` no retiene, para que corra la `sync_master` del restack.
- No lleva `conflicts`: `refusal` solo lo lee donde responde `stack_restack_due` o nada, así que
  no podría cambiar la respuesta.

Un miembro sin evaluación (ocupado o con lock) o con `StackUnreadable` conserva `stack_held=False`.

## Consecuencias

`LaneReason` gana un valor, `stack_parent_unmerged`, que el poller dice como `stacked parent
unmerged`; ninguna métrica, atributo de span ni etiqueta de medición cambia. Un hijo retenido que
no es Cabeza sale en la línea del poller como `waiting (…, stacked parent unmerged)`. Un fallo
persistente de lectura del tracker mantiene retenido al hijo: solo la cabeza no traíble tiene
vencimiento, y se acepta aquí porque la guarda es inerte hasta el lanzamiento.
