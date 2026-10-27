---
status: accepted
date: 2026-10-27
---

# Las subtareas arrancan cuando están listas, en un loop propio, no en olas con barrera

GH-364. `rules.dispatched_subtasks` abría un `Send` por subtarea lista y la ola siguiente
arrancaba cuando la ola entera había vuelto a unirse. Una subtarea con sus dependencias
aprobadas esperaba a hermanas que no tenían nada que ver con ella, y una devuelta frenaba
la ola siguiente. Medido el 2026-10-26 sobre 50 planes multi-subtarea de apostrophe desde el
2026-10-12: 243 subtareas, los planes permitían un factor paralelo de ~1,62 y el observado
fue 1,26 (121 h de sesiones de Developer en 96 h de pared).

Este ADR reemplaza dos frases de ADR-0004: "un nodo por subtarea, checkpointeado y visible en
el trace" (decisión original) y "Cuesta una barrera por ola" (adenda quinta). El resto de esa
adenda sigue: la revisión entra a la rama de su Developer, el fan-in es del sistema, y la
vuelta de una devolución pasa por `integrate`.

## Decisión

**El nodo `pipeline` corre un loop `asyncio`.** El nodo, la constante `PIPELINE` y los strings
de `rules.after_integrate` conservan nombre y valor. Adentro, el loop (`application/schedule`)
pregunta a `rules.next_to_start(state, en_vuelo, límite)` qué arranca y lo arranca; cada
subtarea corre su `develop → review` como una tarea propia. Una entrega se revisa apenas
llega, y cuando una subtarea se aprueba arranca toda la que quedó lista, sin esperar a las
demás. `next_to_start` es la única función que decide qué arranca (legibilidad, dependencias
aprobadas, orden y cota): ordena por camino crítico restante y después por orden del plan, y
no devuelve nada mientras haya `escalated`. El tope de tarea sigue comprobándose sólo en
`run_role`.

**Un turno serializado.** Después de cada terminación, y antes de arrancar nada, corre
`integrate_turn` (síncrono) en un hilo: el merge a la rama de tarea, la creación de checkouts
de subtareas y la escritura de la tabla de hallazgos. Se espera a que termine antes de volver
a preguntar a la regla, así que hay a lo sumo un turno a la vez sin lock, y nunca se crea un
checkout junto a un merge. El adaptador de git espera y reintenta, acotado, ante un error de
lock de ref, y pasa `-c core.filesRefLockTimeout` y `-c core.packedRefsTimeout` sólo en sus
propias llamadas a `merge` y `worktree add`; nada se escribe en el `.git/config` del producto.

**Un decorador de documentos.** Las ramas corren a la vez sobre copias del estado, y cada una
guardaba el documento con su vista: la última en escribir borraba el trabajo de las otras.
Mientras hay subtareas en vuelo, el único que guarda el documento es `FoldingDocuments`, y lo
que escribe es `apply(estado plegado, último delta de cada rama en vuelo)`. El delta de una
rama se reemplaza con su guardado siguiente, no se suma, así que `runs` y `cost_usd` de una
rama que guarda cinco veces cuentan una vez.

**Se pierde el checkpoint por rama; lo reemplaza el documento más `resume_source`.** Antes cada
subtarea era un nodo y LangGraph dejaba un checkpoint por rama. Ahora el loop es un solo nodo:
si el proceso muere con tres subtareas en vuelo, el checkpoint sólo sabe que `pipeline` estaba
pendiente. `rules.resume_source(snapshot.next, crashed)` decide, sólo desde el nodo pendiente
y nunca comparando conteos, de dónde se reconstruye: del documento para una caída con
`pipeline` pendiente, del checkpoint para todo lo demás. `agentic resume` reconstruye el
estado con `from_document` (cruzado con git por `disagreements`, sin cambios: una aprobación
cuyo `reviewed_head` difiere del head de la rama se sigue rechazando), borra el hilo, siembra
con `aupdate_state(..., as_node=PIPELINE)` y reinvoca con `None`. Es una caída, no una pausa:
`crashed = r.interrupted`, nunca `pause` ni `Command(resume=...)`, de modo que el comentario
`Resumed:` ("interrupted at ...") y las cuentas de retoma del poller siguen viendo una
corrida interrumpida. Una tarea pausada por una escalada reanuda igual que antes. Lo que ya
estaba aprobado y guardado no se reconstruye; lo entregado entra por la revisión; una
subtarea `pending` con un commit en su rama reanuda. Lo que se pierde: lo que una rama había
hecho y no alcanzó a guardar en el documento, que se rehace.

**La cota es `max_parallel_subtasks`.** Sin fijar vale `min(4, max_concurrent_roles)`; sólo un
valor explícito mayor que `max_concurrent_roles` es un error, de modo que ningún `.env` válido
antes de este cambio se rechaza. Con la cota menor o igual al semáforo de roles y un solo rol
en vuelo por subtarea, una revisión nunca espera cupo detrás de un Developer nuevo. El 4 es
provisorio hasta #361 (el planificador de la máquina): sin él, más subtareas en paralelo son
más contención por el mismo recurso.

**El drenaje tras una escalada.** Con `escalated` en el estado plegado no arranca nada
nuevo, pero un rol en vuelo nunca se mata: el loop espera a que terminen. Entre la escalada y
el aviso al humano puede pasar, como mucho, una puerta de entrega más (hasta unos 16 min en
apostrophe), y esas ramas se pliegan al documento antes de que la tarea se pause.

**Qué mide el factor paralelo.** Una función pura en `core/measurement` (sin span, métrica ni
etiqueta nuevos): la suma de `ended_at - started_at` de las corridas del Developer, sobre la
unión de los intervalos de las corridas de Developer y de revisión de subtarea. No cuenta
pausas ni esperas de cupo del semáforo. No es comparable 1 a 1 con el 1,26 del issue, que salió
de transcripciones de sesión (incluye tiempo entre turnos y esperas que acá no están); sirve
para comparar tareas medidas igual antes y después. `agentic eval compare` lo muestra.

## Consecuencias

- Riesgo residual de lock del lado del Developer: su commit o su puerta de entrega pueden
  chocar con un lock de ref mientras el turno hace un merge. El turno ya espera y reintenta;
  el Developer no. Se vigila como una entrega fallida o un hallazgo que mencione `.lock`; si
  aparece, es otro issue.
- Una rama que falla ya no deja un checkpoint propio donde reintentar: el loop deja drenar
  a las demás, pliega lo que terminaron y re-lanza el error; la reanudación es desde el
  documento.
- El costo escala con las subtareas en paralelo (puertas de entrega concurrentes por tarea),
  acotado por `max_parallel_subtasks`.
- El criterio de medir en las próximas tareas de apostrophe es operativo, después del merge.
