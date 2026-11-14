---
status: accepted
date: 2026-10-24
---

# El trabajo de tracker es una lista para el humano, no una escalada

GH-336. En apostrophe GH-307, tras aterrizar las rebanadas, quedaba solo trabajo del
tracker (postear una tabla, cerrar o retitular seguimientos). La corrida escaló dos veces:
PjM pidió `gh` porque ningún rol lo tiene, y el Tech Lead falló dos veces porque un plan sin
archivos se rechazaba (`estimated_files must be at least one file`). Costó 6,50 USD y el
humano cerró el issue a mano.

## Qué se decide

1. **Ningún rol recibe `gh`.** Es la regla de ADR-0001; este caso no la cambia.
2. **El código del sistema toca el tracker solo por el puerto `Tracker`.** Esta tarea suma
   un comentario por tarea, hecho por una sola función (`post_tracker_checklist`).
3. **El trabajo del tracker que pide criterio de una persona va al humano como un
   comentario con una lista de casillas**, postea una vez, con un marcador que lleva el hash
   del contenido (`<!-- agentic-tracker: <tarea> <12 hex> -->`). La idempotencia es el patrón
   receptor de `split`: primero el comentario, después el registro
   `tracker_checklist_posted: {at, hash}` en el documento; si falta el registro, el marcador
   evita el segundo post. Una lista que cambia es un comentario nuevo que dice que reemplaza
   a la anterior: el sistema no edita ni borra comentarios.
4. **Una tarea con trabajo de tracker nunca cierra su propio issue.** Un plan con
   `tracker_checklist` y subtareas abre su pull request con `closes_issue=False`.
5. **El trabajo de tracker nunca es una subtarea**: los Developers tampoco tienen `gh`.

## La tercera forma de plan

Sin subtareas, sin partes, con `tracker_checklist` no vacío y `estimated_files == 0`. Va al
nodo terminal `tracker_only`, con el estado `tracker_only`: postea la lista y termina sin
llamar a un rol, sin pull request y sin `delivery_outcome`. Un fallo del comentario escala
con la clase `failure` y la reanudación vuelve a `tracker_only`, nunca a `plan`. Todo plan sin
lista se comporta igual que antes. Desde GH-705 la misma forma admite ítems tipados
(`comment` y `human`) en lugar de la lista; ver el addendum de GH-689.

## Consecuencias

- Los comentarios que el código postea empiezan con `Tracker work for you: `, registrado en
  `resume.SYSTEM_COMMENTS`: una reanudación no los lee como decisión del humano.
- El cuadro de puntaje excluye `tracker_only`, como excluye `split` (`SCOREABLE_STATUSES` y
  `TERMINATED_STATUSES` no cambian). Es un hueco conocido: el gasto de esas tareas no se
  cuenta, y contarlo es otro issue.
- Vale para las tareas planeadas después de este cambio: un documento anterior, como el de
  GH-307, se comporta como hasta ahora.
- Que PjM y el Tech Lead dejen de pedir `gh` no lo prueba ningún test, solo que la regla esté
  en el prompt; la primera tarea real de solo tracker es la evidencia.

## Addendum (GH-342): el registro vacío y la entrega que no se cae

**Causa.** `tracker_checklist_posted` es un canal de reducción del grafo: arranca en `{}` para
toda tarea, no en "ausente". `post_tracker_checklist` trataba todo valor distinto de `None`
como un registro completo y fallaba con `KeyError: 'hash'` en `deliver` (GH-346). Ahora un
solo lector, `rules.tracker_posted`, devuelve el registro solo si `at` y `hash` son cadenas;
`{}`, un registro sin `hash` o cualquier otra cosa significa "nada publicado".

**Los dos caminos, lado a lado.**

- `tracker_only` no tiene pull request: un comentario fallido escala con `failure` y la
  reanudación vuelve al nodo.
- `deliver` abre el pull request igual. La lista, acotada (20 ítems de 200 caracteres y un
  puntero al documento, `rules.tracker_failure_block`), va en el prefacio del cuerpo, si el
  pull request se abre en esa entrega, y en el comentario "Ready for review". No se escribe
  registro, así que el marcador y el hash siguen siendo la idempotencia. Un fallo del
  guardado intermedio, del comentario de listo o de `release_task` no termina el nodo: queda
  una línea `paso: Clase` en el `pause_log`.

Límite: si una re-entrega ocurre mientras `gh` no deja comentar, la lista no llega ni al cuerpo
ni al issue; queda en el documento de la tarea.

## Addendum (GH-344): el trabajo de tracker no retiene el issue

**Causa.** El punto 4 hacía que una tarea fusionada con lista dejara su issue abierto hasta que
el humano lo cerrara. Todo issue con `Blocked by:` apuntando a él quedaba detenido, y la cola
se paró una hora en apostrophe (#339 y #346 bloqueaban a #340, #347, #349 y, por ellos, #350 a
#354). El humano decidió que el trabajo de tracker pendiente nunca debe bloquear la cola.

**Decisión del humano. Reemplaza el punto 4 y solo el punto 4.**

- **Una tarea con `tracker_checklist` cuyo pull request se fusiona cierra su issue como
  completado.** La lista queda como comentario en el issue cerrado y los issues que dependen
  de él lanzan. Hacer la lista sigue siendo del humano; ya no es condición de nada.
- **El pull request de entrega lleva `Closes #N` también con lista.** `deliver` deja de pasar
  `closes_issue=not checklist`. El pull request de una rebanada sigue sin cerrar el issue
  (`closes_issue=False`, GH-297).
- **Si el pull request no lleva la línea o el issue sigue abierto tras el merge, lo cierra el
  sistema** en el barrido de `agentic close --all`, como `completed`, un paso aparte
  independiente de `closed_at` que se repite hasta que el issue figure `CLOSED`
  (`rules.close_after_merge`, sin cláusula de lista pendiente). Solo para los desenlaces
  `merged`, `merged_with_changes` y `auto_merged` con `merge_commit`, o un `closed_by_slices`.
  Es la primera vez que el proceso hijo del poller escribe en el issue de un producto.
- **Sin cambios:** los puntos 1, 2, 3 y 5. El comentario de la lista se postea antes de abrir
  el pull request, así que está en el issue cuando se cierra. `tracker_only` no tiene pull
  request ni merge: el sistema no cierra su issue, lo cierra el humano al terminar la lista.
  Un documento de `tracker_only` con el issue cerrado como `COMPLETED` resuelve a quien lo
  bloquea sin exigir un commit de merge.

**Consecuencias.** Un issue cerrado ya no prueba que el trabajo de tracker se hizo: la
lista del comentario es el único registro y el humano la lee cuando le toca. Los issues ya
detenidos (#339, #346) se cierran en el primer barrido posterior a este cambio.

## Addendum (GH-348): el poller corre el barrido en cada vuelta

El barrido de GH-344 (`close_issues.sweep`) solo corría desde `agentic close --all`, así que
un merge cuyo PR no traía `Closes #N` dejaba el issue abierto hasta que un humano lo cerraba
(apostrophe #340, PR #377: tres horas, con todo lo que dependía de él bloqueado).

- **Qué cambia:** `poll.run_once` llama al barrido en cada vuelta, justo después de
  `gather_closes` y antes de `gather_launches`, fuera de `registry.exclusive()`. Va antes de
  los lanzamientos para que un dependiente vea a su bloqueador `CLOSED` en la misma vuelta.
  Solo decide `rules.close_after_merge`.
- **Qué imprime:** solo `agentic: close: GH-N: issue closed`. `sweep` suma
  `report_failures` (por defecto `True`: `agentic close --all` no cambia); el poller lo pasa
  en `False`. Un cierre que falla o una cola ilegible reintenta en silencio en la vuelta
  siguiente; una falla persistente se ve en el issue abierto y en `agentic close --all`.
  Eso incluye una falla de transporte de la lectura de la cola: el barrido no corta la vuelta,
  y la lectura de `gather_launches` que le sigue falla igual y la termina como siempre (el
  adaptador `gh` imprime su propia línea en cada lectura caída, así que aparece dos veces).
  Una vuelta sin nada nuevo sigue imprimiendo `nothing to do this round`.
- **Costo:** un `gh issue list` más por vuelta (además del de `gather_launches`), lineal en
  los issues `agentic` abiertos, más un `gh issue close` por cada cierre.
- **Sin cambios:** un documento con `closed_at` pero sin `merge_commit` (anterior a este
  ADR 0062) sigue necesitando `agentic close --all` para el backfill.
- **Causa raíz de #377:** lo abrió un proceso que aún corría código anterior a b5c6653
  (`closes_issue=not checklist`, y GH-340 tiene `tracker_checklist`). La re-entrega conservó
  el PR guardado (`state.get("pull_request") or open_pull_request(...)`), así que nunca
  volvió a pasar por `open_pull_request`. No se reescriben cuerpos de PR: lo cubre el barrido.
  Un test fija que la entrega de un split part con checklist abre con `closes_issue=True`.

## Addendum (GH-350): el split también publica la lista

Un plan con `parts` y `tracker_checklist` (apostrophe #378: reapuntar el `Blocked by:` de
#379) se rechazaba con una instrucción que ningún campo podía cumplir, porque una parte no
tiene checklist: el reintento repetía el plan y la tarea escalaba.

- **Decisión:** el validador acepta `parts` junto a `tracker_checklist` (los ítems pasan los
  mismos chequeos de ítem). `split` guarda, junto con la URL de la última parte, la lista del
  plan seguida de los ítems que `rules.split_tracker_items` deriva por cada issue abierto, que
  no sea una parte, cuya primera línea diga `Blocked by: #<esta tarea>` (tope de 10 y una
  línea "and N more"). Después la publica con `post_tracker_checklist` en el issue original,
  antes del comentario `Split:`. Una reanudación reutiliza la lista guardada y no la recompone.
- **El humano reaparta:** el sistema no edita ningún issue ni bloqueador nativo; el ítem dice
  "cerrá #T o reapuntá #N a la última parte". `rules.TRACKER_WITH_PARTS` es el único texto que
  dice adónde va el trabajo de tracker de un plan con partes, y lo renderizan ambos prompts
  del Tech Lead y el mensaje del validador.
- **Límites conocidos:** un issue bloqueado solo por la relación nativa de GitHub no se
  detecta (lo cubren los ítems del Tech Lead); `open_issues` es un `gh issue list` acotado, y
  un repo con más issues abiertos que ese tope puede ocultar uno bloqueado.

## Addendum (GH-375): el tablero del Proyecto

El §2 ("el código del sistema toca el tracker solo por el puerto `Tracker`") se refiere a
issues, PRs, comentarios y etiquetas. El GitHub Project que espeja el estado no pasa por
`Tracker`: tiene su propio puerto, `Board`, y es una salida que nunca se lee como entrada de
una decisión. Lo registra ADR-0073.

Addendum (GH-415, 2026-10-28): el alcance de "cuya primera línea diga `Blocked by:`" del
addendum de GH-350 pasó a ser cualquier línea completa fuera de un bloque de código; ver el
addendum de GH-415 en ADR-0039.

## Addendum (GH-619, regla del dueño 2026-11-12): el sistema reapunta a los dependientes

Reemplaza el punto "El humano reaparta" del addendum de GH-350: el sistema mantiene el tracker
coherente con sus propios splits.

- **Decisión:** cuando `split` termina, `split_sweep.repoint_dependents` reescribe, en cada
  issue abierto que no sea una parte ni el issue de la tarea, las líneas completas
  `Blocked by:` (fuera de un bloque de código) que nombran a la tarea, para que apunten a
  `rules.last_part_numbers`. La lista de candidatos sale solo de `Inbox.open_issue_refs` (que
  falla ante una página llena), nunca de `open_issues`. Cada dependiente se vuelve a leer con
  `Inbox.task` justo antes de escribir, la reescritura la hace `rules.repoint_blockers` (el
  mismo recorrido que `blockers_from_body`, consciente de los bloques de código) y solo se
  escribe si se cumple la poscondición. Una edición (`Tracker.update_issue_body`) y un
  comentario por issue; la lista del split vuelve a ser solo la del plan, y ningún ítem pide
  al humano cerrar o reapuntar. `rules.TRACKER_WITH_PARTS` sigue siendo el único texto que dice
  quién reapunta.
- **Si falla:** una lectura o una edición que falla nunca detiene el split; el comentario
  `Split:` lo dice en una línea y el barrido de la parte 2 lo termina. El issue de la tarea
  partida no se cierra solo hasta la parte 3.
- **Límites conocidos:** no hay compare-and-swap sobre el cuerpo de un issue: una edición
  humana entre la relectura y la escritura se pierde; los espacios en los bordes del cuerpo se
  pierden porque la relectura va recortada; los enlaces nativos y las referencias externas
  `owner/repo#N` no se editan; un corte entre la edición y el comentario pierde el comentario;
  un split en vuelo antes de GH-619 puede publicar ítems derivados que ya tenía guardados; un
  barrido que falló espera a la parte 2.

- **Costo del barrido de partidas (parte 2):** en régimen estable no hace ninguna llamada: sin
  padre `split` abierto y sin `split_repointed_at`, la vuelta no toca el tracker. Mientras algún
  padre carezca del registro hay una lectura de lista (`open_issue_refs`) por vuelta, más una
  relectura, una edición y un comentario por dependiente. Una página llena de 1000 issues
  abiertos hace que la lectura falle: no se escribe el registro y el barrido repite esa lectura,
  en silencio, cada vuelta. Un dependiente que empieza a nombrar al padre después de escrito el
  registro no se reapunta nunca; la parte 3 (cerrar el padre) lo acota. Las líneas nuevas son
  salida del poller, no una métrica (estándar 7).

- **Cierre y escalación (parte 3):** el barrido de partidas también cierra al padre. Cuando
  todas las partes están `CLOSED`/`COMPLETED` y su veredicto de bloqueo es `resolved`
  (`rules.split_parent_state` lee primero `state_reason`: una parte `NOT_PLANNED` nunca cuenta
  como hecha aunque su veredicto lea `resolved`), reapunta una vez más, registra
  `split_closed_at` con `Documents.record_closure` y solo entonces llama a
  `Tracker.close_issue(task_id, comment=…)`, que cierra con el comentario en una sola llamada
  de `gh`. Un registro que falla significa que no hay cierre; con el registro escrito y el
  issue abierto (un corte), la vuelta siguiente solo reapunta y cierra. Si una parte está
  `NOT_PLANNED`, el barrido publica un comentario en el padre, terminado en
  `<!-- agentic-split-abandoned: <id> part <k> -->`, y registra `split_abandoned_at`: una
  escalación por padre, sin etiqueta, sin ítem de lista para el humano. A diferencia de lo
  pedido, el padre escalado no pasa a leer solo sus partes abandonadas: sigue leyendo todas
  (2 a 5 lecturas por vuelta), porque registrar cuáles quedan exigiría un valor estructurado
  que `record_closure` no escribe, y así una parte reabierta y fusionada todavía lo lleva a
  cerrarse. Ambas claves son cadenas ISO UTC; no se añade ninguna clave nueva.
- **El reapunte previo al cierre es por prolijidad, no por corrección:** `poll._blocker_verdict`
  resuelve a un bloqueante cuyo documento es `status: split` y lleva `split_closed_at`, y solo
  con el issue ya `CLOSED`/`COMPLETED` (un registro sobre un issue abierto no afirma nada), de
  modo que un dependiente que aún nombra al padre cerrado también se desbloquea.
- **Estándar 8 (costo):** el sistema cierra solo issues que él mismo partió (el documento es
  `status: split` con todas las partes registradas); un padre anidado sigue cerrándolo el
  `Closes` de su hija terminal (GH-592), sin cambio. La primera vuelta en producción hace unas
  35 escrituras (reapuntes, registros y cierres de los padres abiertos cuyas partes ya se
  fusionaron), sin tope, y se detiene ante un error de límite de tasa, de tracker inalcanzable o
  de tiempo agotado; la vuelta siguiente termina lo que quedó. En régimen estable: cero llamadas
  por padre con alguna parte en la cola; una lectura de `issue_state` por parte y vuelta para los
  padres juzgados, memoizada por número dentro de la vuelta; como mucho un `fetch` por vuelta
  (el `upstream` propio del barrido, solo si hay un commit de fusión que juzgar). Crece
  linealmente con la cantidad de padres `split` abiertos. Un padre que sigue `waiting` aparece
  como "Necesita humano" en el tablero: hallazgo externo, no se cambia acá.

## Addendum (GH-689): el sistema hace los ítems `comment`

Regla del dueño (GH-620): el trabajo de tracker que el sistema puede hacer con su propio acceso se
hace en la misma corrida y se informa.

- **Qué hace:** `deliver` publica los ítems `comment` del plan, antes de abrir el pull request y
  solo en el issue de la propia tarea (`Tracker.comment(state["id"], …)`). El plan los muestra
  primero, bajo `### Comments the system posts`, porque es el único lugar donde el dueño ve el
  texto que se publicará.
- **La guarda:** primero el registro, luego el marcador. Un ítem con registro en
  `tracker_actions_done` se salta; si no, se leen los comentarios del issue (una vez) y, si ya
  tienen su marcador `<!-- agentic-item: <tarea> <hash> -->`, solo se registra; si no, se publica.
  El registro `{at, kind}` se escribe solo tras el éxito y se guarda de inmediato. Se detiene en
  el primer fallo.
- **Prefijos:** `Plan note: ` para cada comentario y `Tracker work done by the system: `
  (el informe lo postea `tracker_only` desde GH-705); ambos están en `SYSTEM_COMMENTS`, así que una reanudación no los lee
  como decisión del humano, y un comentario humano con ese texto a mitad de línea sí cuenta.
- **Texto que no enlaza fuera:** un campo del sistema rechaza esquemas de URL, `www.`, `![` y
  `](`, etiquetas HTML, `#N` suelto, `owner/repo#N` y `GH-N`; menciones y marcadores ya se
  rechazaban. El tipo `human` no cambia.
- **La lista de casillas** (`tracker_checklist`) queda solo para documentos antiguos; el comentario
  «Tracker work for you» no se publica para ítems tipados.
- **Si falla en `deliver`:** queda un lapso `tracker item comment: <Clase>` y el bloque de lo
  pendiente va en el cuerpo del pull request y en el comentario de listo; el pull request se abre.
  Un ítem así solo se reintenta si `deliver` corre de nuevo; si no, sigue listado en el cuerpo y en
  el documento de la tarea. Una nueva entrega repite el informe.
- **Camino de `tracker_only` (GH-705):** el nodo hace, en este orden, `run_system_items` (la
  misma guarda y los mismos registros que en `deliver`), el informe `DONE`, el escalado de los
  ítems `human` y, solo para documentos antiguos, la lista de casillas. El informe se arma solo
  con `tracker_items.report` y termina en `<!-- agentic-done: <tarea> <digest> -->`, calculado
  con `done_marker` sobre los `item_hash` de los ítems hechos (nunca sobre el texto del informe);
  antes de postearlo se buscan los comentarios del issue y, si el marcador ya está, no se postea.
  Sin ítems hechos no se lee ni se postea nada. Un plan de solo ítems `comment` termina sin
  escalar; con ítems `human` escala `questions` después del informe, y el parche une los
  registros de ambas fuentes con `task.add_tracker_actions`. Un posteo fallido escala con
  `failure`: el paso que falló no deja registro, los anteriores viajan en el parche, el motivo
  nombra el paso y solo la clase de la excepción, y la reanudación vuelve al nodo. El validador
  del plan ya no rechaza ítems del sistema en un plan solo de tracker; la regla de
  `estimated_files == 0` se mantiene.

