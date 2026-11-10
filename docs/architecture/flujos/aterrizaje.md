# Aterrizaje

Qué hace el sistema con un pull request aprobado cuyo merge `auto_merge_decision` rechazó,
en vez de dejarlo al humano (ADR-0048, [el sistema aterriza sus propios pull requests](../../adr/0048-el-sistema-aterriza-sus-propios-pull-requests.md)).
Un Aterrizaje no es una Reanudación: una reanudación retoma una corrida cortada, un
aterrizaje trabaja sobre un pull request ya entregado (glosario en
`agentic/core/process/CONTEXT.md`).

```mermaid
sequenceDiagram
    participant Poller
    participant Merge as agentic merge
    participant Land as agentic land
    participant Table as landing_step
    participant Doc as Task document
    participant GitHub
    Poller->>Merge: spawn merge --issue N
    Merge->>GitHub: auto_merge_decision
    GitHub-->>Merge: refusal (CI, findings, master moved...)
    Poller->>Land: spawn land --issue N
    Land->>Land: take run lock, register under the run kind
    Land->>Table: landing_step(refusal, facts, spent)
    Table-->>Land: one step within the budgets
    Land->>Doc: record the step in the landing list
    alt wait
        Land->>Land: nothing to do, next round decides again
    else sync_master, re_review, fix, rerun
        Land->>GitHub: bring upstream, review, fix or rerun CI
        Land->>Land: spawn resume --from-document when the technical stage reopens
    else file_followups_and_land
        Land->>GitHub: open follow-up issues, merge
    else adr_number_taken
        Land->>GitHub: renumber when the rule can decide, else escalate_adr asks the human once; the owner's decision gives apply_adr_decision, otherwise none
    else ci_unknown_names
        Land->>GitHub: escalate once per (head, names) as a planning defect, no budget spent, then none
    else budget spent, ledger only
        Land->>GitHub: escalate_ledger asks the human once, then none
    else budget spent, a clean slice exists
        Land->>GitHub: land_slice lands what is approved
    else budget spent, no slice
        Land->>Land: diagnose, then sync_master, restart or escalate
    end
    Land-->>Poller: result printed, lock released
```

## Paso por paso

1. **El rechazo.** `agentic/cli.py:_merge` pregunta a
   `agentic/core/process/domain/rules.py:auto_merge_decision` si mergear. Cuando dice que no,
   el motivo es uno de los rechazos de la tabla del ADR-0048: CI sin terminar o roto,
   hallazgos abiertos, cabeza no revisada, master movido, intentos agotados.
2. **El poller lanza `land`.** `agentic/core/process/application/poll.py:gather_merges`
   reúne también los candidatos a aterrizar, y
   `agentic/core/process/application/poll.py:execute` lanza `agentic land --issue N`. El
   verbo es `agentic/cli.py:_land`; corre bajo el lock de la tarea y registrado con el tipo
   de corrida con que se lo lanzó (`land_checks` si no gasta modelo, `land_model` si sí).
3. **Un solo paso, elegido por una tabla.**
   `agentic/core/process/domain/landing.py:landing_step` es el único lugar que decide *qué
   hacer en vez de* mergear. El paso queda en la lista `landing` del documento antes de
   ejecutarse, y cada tipo de paso tiene su brazo en
   `agentic/core/process/application/landing.py:_dispatch`.
4. **Los pasos.** `LandingStepKind` tiene 16 tipos
   (`agentic/core/process/domain/landing.py:LandingStepKind`), cada uno con su brazo en
   `_dispatch`. Con modelo o con el upstream: traer el upstream (`sync_master`), volver a
   revisar (`re_review`), corregir (`fix`), el pase final sobre los importantes propios
   (`fix_important`), repetir el CI o los chequeos (`rerun`), archivar el seguimiento de lo
   que quedó fuera de alcance y mergear (`file_followups_and_land`), renumerar el ADR cuyo
   número ya tomó master (`renumber`), aplicar la decisión del dueño sobre una colisión que la
   regla no decide (`apply_adr_decision`, `land_model`), aterrizar por tramos (`land_slice`) y diagnosticar
   (`diagnose`). Sin gastar modelo: esperar (`wait`), escribir lo que la aprobación vigente de
   PM cubre (`settle`), preguntarle una vez al humano por lo que no cubre (`escalate_ledger`) o por una colisión de
   números de ADR que la regla no puede decidir (`escalate_adr`),
   escalar (`escalate`) y no hacer nada (`none`). Los brazos están en
   `agentic/core/process/application/landing_sync.py`,
   `agentic/core/process/application/landing_fix.py`,
   `agentic/core/process/application/landing_slice.py` y
   `agentic/core/process/application/landing_adr.py` (`apply_adr_decision`).
   **`apply_adr_decision` (GH-544)** corre al Developer dentro del hijo de `land`
   (`agentic/core/process/application/run_role.py:run_role`, no una reanudación), en el checkout
   de la tarea y dentro de `merges_cleared`. Primero recalcula la colisión contra el sha del
   master de la ronda (`agentic/core/process/application/adr_collision.py:adr_decided`); si ya no
   es una `Ambiguous` con los mismos `moves` que registró `escalate_adr`, el resultado es
   `stale_moves` y no se corre ningún modelo. Después escribe y commitea la renumeración
   mecánica (`Workspace.commit_paths`) y le da al Developer la decisión del dueño, los `moves`
   aplicados, las líneas exactas donde puede escribir y las que la regla ya reescribió, que
   puede mover dentro de su archivo (para mantener ordenado un índice) pero no cambiar
   (`landing_adr.brief`): sin elegir nunca un número. Revisa los límites por contenido
   (`agentic/core/process/domain/adr_numbers.py:outside_bounds`, sobre `DecidedRenaming.spots`
   y `bound`, que es lo único que decide qué líneas puede tocar el pase), que los chequeos hayan
   quedado en verde y que no quede colisión (`adr_collision`). Si todo está bien empuja con `lease=head`
   (`Workspace.push`) y el resultado es `applied` con `head_after`; en cualquier otro resultado
   (`role_failed`, `checks_red`, `checks_timeout`, `out_of_bounds`, `still_colliding`,
   `stale_moves`, `git_failed`, `ambiguous`, `no_collision`) no empuja,
   deja el checkout en la cabeza con `Workspace.reset_hard` (en un `finally`, también ante una
   excepción) y vuelve a preguntar por `escalate_adr`, que nombra el último resultado fallido.
   Cuando el resultado es `checks_red`, la entrada `apply_adr_decision` registra además
   `red_checks` (nombre, últimas líneas saneadas y si el chequeo se recuperó) y `red_more`,
   construidos solo por `agentic/core/process/domain/presubmit.py:red_checks` (a lo sumo
   `RED_CHECKS_MAX`); la pregunta los muestra en el mismo comentario, acotado por
   `MERGE_COMMENT_MAX`, o dice que no se pudo leer el detalle.
   **GH-585:** la puerta del apply corre el nivel de entrega, `fast_tier(checks, delivery_checks)`
   (`landing_adr._gate`), como la entrega de un Developer y no la suite completa del producto; el
   nivel completo de la cabeza empujada lo juzga el merge. El veredicto sale de
   `presubmit.gate_verdict` sobre `final_results`: un rojo real gana a un timeout, y el campo
   `timed_out` de la fila decide, nunca su texto. Si un chequeo agotó su tiempo y ninguno está en
   rojo, el resultado es `checks_timeout`: la entrada registra `red_checks` con `timed_out`, no
   empuja, no gasta cupo y la pregunta dice qué chequeo y cuántos segundos. Toda entrada cuya
   corrida armó la puerta registra `gate_tier` (`delivery` o `all`, solo lo escribe
   `landing_adr._finish`); `landing.adr_decisions_spent` es el único que lo lee: un `checks_red`
   sin esa clave (juzgado contra la suite entera) no gasta cupo y uno con cualquier `gate_tier`,
   incluso desconocido, sí.
   **GH-579:** los límites se informan por línea (sin archivo entero; fin de línea o salto final
   solos están dentro de límites), las líneas (b)/(c) son `bound`, lo que la decisión deja igual se
   nombra con `keep <ruta>:<línea>` en un commit (`kept_spots`; `role_failed` si un `spot` ni
   cambió ni se nombró), y `escalate_adr` registra `upstream` (otro sha al aplicar: `stale_moves`).
   **GH-582:** `stale_moves` es solo que el master se movió desde la pregunta o que los `moves`
   de la regla cambiaron. `apply_adr_decision` comprueba en este orden: el upstream registrado
   contra el sha, un error de git al recalcular (`git_failed`), que ya no haya colisión
   (`no_collision`), que la colisión sea `Ambiguous` (`ambiguous`) y, al final, que los `moves`
   sean los registrados (`stale_moves`); cada rechazo deja una línea de log que nombra la
   condición. Si `adr_decided` devuelve una `DecidedRenaming` sin `spots` (la regla (d), un enlace Markdown
   con la etiqueta `NNNN` y el destino `NNNN-slug.md`, reescribió toda referencia, por ejemplo una fila de índice), no corre
   el Developer: el sistema hace el commit mecánico, corre una vez los chequeos de entrega con
   `before` en la cabeza anterior a ese commit, decide `checks_red` por los resultados de la puerta
   (un rechazo del testigo o de rutas, o unos resultados ausentes, también terminan `checks_red`,
   nunca `applied`), comprueba con `adr_collision` que no quede colisión (`still_colliding`) y
   empuja con lease. Las líneas reescritas no se reordenan: un índice cuyos chequeos exigen orden
   termina `checks_red`. Tabla de resultados:

   | Resultado | Gasta cupo | Vuelve a preguntar | Empuja |
   |---|---|---|---|
   | `applied` | sí | no | sí |
   | `out_of_bounds`, `role_failed`, `checks_red` (con `gate_tier`), `still_colliding` | sí | sí | no |
   | `checks_red` sin `gate_tier`, `checks_timeout` | no | sí | no |
   | `stale_moves`, `git_failed` | no | sí | no |
   | `ambiguous` | no | sí, sin ofrecer decisión | no |
   | `no_collision` | no | no | no |

   `git_failed` y `ambiguous` no gastan cupo: GH-652 se repetía porque el dueño volvía a decidir,
   no de forma automática; un fallo de git no es obra del dueño y `ambiguous` nunca ofrece una
   decisión. La pregunta de `escalate_adr` dice que la regla reescribe las líneas `bound` y que una
   `Decision:` las aplica sin pase de modelo solo con una `DecidedRenaming` sin `spots` y con
   `bound`; si el cálculo falló, es `Ambiguous` o no se calculó, conserva «Fix by hand». Tras un
   `git_failed`, dice que una nueva `Decision:` reintenta. El camino automático (`renumber`) y
   `ADR_EDIT_BOUNDS` no cambian.
   Cuando el resultado es `out_of_bounds`, la entrada registra `out_of_bounds_lines` (a lo sumo
   cinco `{path, line}`, con la ruta saneada por `landing.out_of_bounds_detail`; la línea 0 es el
   archivo entero) y `out_of_bounds_more` con las que no se muestran, nunca el texto de una línea.
   Toda entrada de apply registra `bounds` (`ADR_EDIT_BOUNDS`): el tope por colisión solo cuenta
   las de la versión vigente, y una entrada sin `bounds` vale 1.
5. **Cuando el paso reabre el stage técnico.** Un `fix` o un `re_review` que necesita a un
   Developer lanza una reanudación desde el documento
   (`agentic/cli.py:_resume_spawner`: `agentic resume --issue N --from-document --yes --detach`)
   y espera a que se registre.
6. **Presupuestos.** Los pases del modelo y las sincronizaciones tienen topes por tarea
   (`agentic/core/process/domain/landing.py:LANDING_PASSES_MAX`,
   `agentic/core/process/domain/landing.py:LANDING_SYNCS_MAX`). Cuando uno se agota, todos
   terminan en el mismo lugar, `exhausted()` dentro de `landing_step`: si el rechazo es solo
   del libro mayor (`ledger_only`), el paso es `escalate_ledger`, o `none` si ya se preguntó;
   si no, es `land_slice` cuando existe un tramo limpio ya aprobado, y `diagnose` solo cuando
   no. El diagnóstico tiene un veredicto cerrado, `sync` o `unlandable`, y
   `agentic/core/process/domain/landing.py:after_diagnosis` lo convierte en una sincronización
   más (`sync_master`), un reinicio (`restart`) o un escalado (`escalate`). El reinicio es el
   último recurso: avisa en el issue con el motivo y el costo hasta ese momento.
   El rechazo `adr_number_taken` no pasa por `exhausted()`: es, en este orden:
   `renumber` cuando la regla decide la colisión y quedan renumeraciones
   (`LANDING_RENUMBERS_MAX`); `apply_adr_decision` cuando el dueño liberó la pregunta
   `escalate_adr` y queda cupo para esa colisión (`adr_decision_due`, `LANDING_ADR_DECISIONS_MAX`, contado por
   (cabeza, `moves`) con `agentic/core/process/domain/landing.py:adr_decisions_spent`; agotado el
   cupo, la pregunta deja de ofrecer una decisión); `none` cuando
   la pregunta sigue pendiente (`adr_asked`); si no, una sola `escalate_adr` por (cabeza,
   rechazo). Nunca `diagnose` ni `restart` (ADR-0049, adendas GH-412 y GH-544). La decisión no
   gasta `LANDING_PASSES_MAX`, pero la primera `re_review` tras una decisión aplicada se exime
   del tope una vez (`LandingSpent.adr_review_owed`), aunque cuenta como pase.
   El rechazo `ci_unknown_names` (GH-485) tampoco pasa por `exhausted()`: un nombre exigido que
   ninguna corrida de chequeo de la cabeza lleva, con todas las corridas de workflow completadas,
   es un defecto de planificación y no un CI lento. `landing_step` lo manda a un solo `escalate`
   por (cabeza, nombres), con los nombres ordenados en la entrada `landing`
   (`agentic/core/process/domain/landing.py:landing_step`), y después a `none`; otros nombres
   sobre la misma cabeza se publican de nuevo (`MergeFacts.ci_unreported` y `ci_reported`,
   `LandingStep.names`, `already_dispatched(names=)`). No gasta presupuesto, ni `diagnose`, ni
   reinicio (ADR-0045, adenda GH-485).
7. **Un escalado se libera por una sola decisión (GH-442).** Un documento escalado cuyo paso
   de la tabla es `diagnose` no se despacha: `agentic/core/process/application/landing.py:_land`
   (y el poller, en `gather_merges`) preguntan a
   `agentic/core/process/application/landing_plan.py:release` si el escalado se libera, bajo la
   condición `landing_plan.release_asked`. La respuesta (`Release.released`) es verdadera por
   una decisión humana (`landing.released`) o, si el escalado lo causó solo el CI, porque el CI
   de la rama base volvió a verde (`landing.released_by_ci`, que no escribe `### Decision` ni
   texto en `Human`). Liberado, el paso se recalcula sobre la vista liberada
   (`landing_plan.released_step`): `sync_master` si la cabeza no contiene el upstream, `rerun`
   si lo contiene, `wait` si la ascendencia no se puede leer. `land` juzga la clase de corrida
   (`run_kind_admits`) y `takes_action` una sola vez, sobre ese paso liberado y no sobre el de
   antes de la liberación, y antes de escribir la entrada `landing`; la
   liberación (el desescalado y el `ci_released` de la entrada `escalate`) y la entrada del
   paso siguiente se guardan en una sola escritura de `documents.save`, así que ningún fallo
   deja una liberación sin su paso siguiente. Un escalado que no es de CI (libro
   mayor, diagnóstico de profundidad 1, GH-328) conserva su comportamiento. El de una colisión
   de ADR (`escalate_adr`) también se libera por un comentario del dueño
   (`landing_plan.release_asked`), y el paso que la tabla da sobre la vista liberada
   (`landing_plan.released_step`) es `apply_adr_decision`, no `sync_master` ni `rerun`.
   **El escalado `ci_no_verdict` (GH-463).** Antes de escalar, un par `merge_ci` de la cabeza que
   terminó `no_verdict` sobre el master actual se relee (GH-552, GH-564,
   `agentic/core/process/domain/merge_ci.py:rereadable`): la corrida pudo terminar después de la
   gracia. Hasta esa relectura la decisión de la puerta es `ci_run`, así que `land` ve
   `skipped decision_ci_run` y el poller lanza un hijo de merge, también para una tarea escalada. El
   hijo lee GitHub y escribe después, solo si la lectura es terminal
   (`agentic/core/process/application/merge.py:_reread_pair`): un veredicto verde o rojo decide
   (un verde deja el par en `green` y se mergea, sin `released_at`); un `timed_out` o `unreachable`
   estampa `reread_at` y `reread_state` y el rechazo `ci_no_verdict` sigue; una corrida todavía en
   curso no escribe nada y la puerta espera (`ci_wait`), sin republicar ni despachar. Con
   `reread_state` el par ya no es releíble y sigue lo de abajo. Cuando GitHub no dio veredicto para una corrida
   fresca, la tabla da `escalate` y `landing_plan.release_asked` también se cumple para ese
   paso `escalate`, no solo para `diagnose`, si el documento tiene la forma de
   `agentic/core/process/domain/landing.py:ci_no_verdict_entry`. Ese escalado se libera una vez
   por par (cabeza, upstream) (`landing_plan._no_verdict_release`): por un upstream nuevo o por un
   comentario del dueño, y nunca porque la base esté en verde. La liberación la escribe
   `landing.released_no_verdict`: desescala el documento, marca `released_at` en la entrada
   `escalate` y en el par `merge_ci` de la cabeza, que le concede un par más al contador
   `no_verdict` (`agentic/core/process/domain/merge_ci.py:pair_counts`). El paso liberado es `wait`
   (`landing_plan.released_step`), que `_land` guarda directamente, sin ejecutor, y el poller
   (`poll._landing_step`) lo devuelve para que un hijo lo persista.
   `agentic resume` consulta la misma decisión
   (`agentic/core/process/application/resume.py:_landing_owns_line`) y solo nombra
   `agentic land` si `land` puede mover la tarea; si espera la CI de la base o un comentario
   humano, lo dice sin mencionar `--fresh`, y si la forma del escalado solo la mueve un reinicio
   (libro mayor, diagnóstico de profundidad 1) lo dice en vez de enviar a `land`, que responde
   `not_live` o `escalated_needs_restart` sin escribir nada.
