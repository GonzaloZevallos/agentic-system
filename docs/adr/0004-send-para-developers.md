---
status: accepted
date: 2026-09-12
---

# Fan-out de Developers con `Send`, no con `asyncio.gather` dentro de un nodo

Tech Lead parte la tarea en subtareas y despliega un Developer por cada una, en paralelo.
Lo hacemos con la API `Send` de LangGraph: un nodo por subtarea, checkpointeado y visible en
el trace. El diseño exige que cada Developer sea un nodo del grafo y no un subagente
escondido, porque PjM enruta los rechazos técnicos de QA al Developer exacto de cada subtarea.

`open_deep_research` abandonó `Send` y lanza sus researchers con `asyncio.gather` sobre
`subgraph.ainvoke` dentro de un nodo. Su motivo es mantener válida la conversación de tool
calls del supervisor: cada resultado vuelve como `ToolMessage` con su `tool_call_id`. Nosotros
no tenemos esa conversación. Tech Lead entrega un plan de subtareas como salida estructurada,
no como tool calls, así que la ventaja de `gather` no aplica y su costo (invisibilidad) sí.

## Consecuencias

- La clave del contador de devoluciones del par Tech Lead→Developer incluye el id de subtarea.
- El subgrafo técnico declara un `output` schema: hacia afuera salen la pieza integrada y el
  mapa de subtareas, no la conversación interna de cada Developer.

## Adenda (2026-09-14): el reintento continúa la sesión que dejó el intento fallido

La segunda corrida de GH-12 mostró el costo de que la conversación del Developer quede solo
en su sesión: en las cuatro subtareas el tope de 3 USD cortó el primer intento justo cuando
entregaba la salida estructurada, con el trabajo ya commiteado, y el reintento arrancó una
sesión nueva, releyó el worktree y gastó 1.4-1.8 USD más solo para reportar. En una el
reintento también tocó el tope y la tarea escaló.

**Un reintento continúa la sesión del intento que falló, si la hay.** `AttemptFailed`
lleva el `session_id` que el SDK devuelve aun en un resultado con error (tope, turnos,
salida inválida, entrega sin commit); `run_with_retry` reanuda esa sesión y le manda solo el
error, porque el prompt ya está adentro. El modelo conserva lo que leyó e hizo: un
Developer cortado al entregar, entrega. Es la misma regla que las entrevistas (ADR-0014):
una sesión por participante. Un fallo sin sesión, el harness roto antes de abrirla, sigue
arrancando de cero con el error agregado al prompt. Cada intento es un proceso nuevo, así
que el tope se cuenta por intento, no por sesión.

**El tope es red de seguridad, no presupuesto de trabajo.** Un tope que se dispara en toda
corrida sana no protege nada: solo duplica el gasto. El default sube a 10 USD por intento,
unas tres veces lo que costó una subtarea de GH-12, y sigue ajustable por
`AGENTIC_DEVELOPER_MAX_BUDGET_USD`. Descartado que Tech Lead estime un tope por subtarea en
el plan: no sabe estimar dólares y el plan no debe cargar con eso. Un tope por tarea que
frene el gasto total es una decisión aparte, fuera de esta adenda.

## Adenda (2026-09-14, segunda): el tope por intento es de todos los roles

El tope cubría solo al rol que ejecuta, y en GH-12 el que gastó diez dólares en un intento
sin ejecutar nada fue el Tech Lead revisor, con los subagentes de `code-review` (issue #7).
**La red de seguridad es la misma para todo rol**: 10 USD por intento, ahora
`AGENTIC_MAX_BUDGET_USD`; el nombre con `DEVELOPER` desaparece sin alias, porque nació el
mismo día. El tope por tarea sigue siendo la decisión aparte (issue #10).

## Adenda (2026-09-14, tercera): tope de tarea, la red de seguridad del acumulado

El tope por intento no frena una corrida: la segunda de GH-12 gastó 71 USD sin que ningún
intento se pasara, porque una tarea suma definición, plan, N subtareas con sus intentos y
devoluciones, y una revisión por entrega (issue #10). Por sección: definición y plan 7 USD,
los tres Developers aprobados 18, sus tres revisiones 40, fallos 15.

**Un tope de tarea, chequeado en `run_role` antes de cada intento de cualquier rol.**
`cost_usd` del estado ya acumula todo por reducer y llega fresco a cada intento: `review`
e `interview` aplican los patches al estado antes de la siguiente llamada, así que el
chequeo cubre también las revisiones dentro de un nodo y las rondas de una entrevista, que
el ruteo entre nodos no ve. Alcanzado el tope, `run_role` devuelve el mismo patch de
escalado que un fallo de rol repetido, con total gastado y tope como motivo, y el nodo
`escalate` etiqueta y comenta sin cambios. No es fallo de rol: el rol no se rompió. El
ruteo queda puro, sin conocer la configuración.

Descartado chequear en el ruteo del grafo: pasa una vez por superstep y se pierde el loop
de revisiones (6-20 USD cada una).

**Default 100 USD, `AGENTIC_TASK_MAX_BUDGET_USD`.** El issue proponía 50, pero con revisión
incremental (ADR-0015) y el tope de 10 por intento una tarea sana de cinco subtareas ronda
75-100 USD, y hoy escalar termina la corrida sin reanudación (ADR-0003 pendiente): un tope
que corta corridas sanas tira el trabajo de la mitad recorrida. Misma regla que las adendas
anteriores: red de seguridad, no presupuesto de trabajo. Cuando exista `agentic resume`, el
escalado por tope pasa a ser un punto de decisión del humano, que podrá subirlo y reanudar;
ahí conviene revisar el número.

**En paralelo el desborde queda acotado y se acepta.** Cada rama de `Send` arranca con una
copia del estado y no ve lo que gastan sus hermanas hasta el fan-in, así que el chequeo ve
el total al momento del fan-out. Desborde máximo: N ramas × dos intentos × tope por
intento. El escalado sale uno solo: `escalated` tiene reducer `last` y el nodo `escalate`
corre una vez por superstep. Las ramas ya commiteadas quedan (ADR-0011). Descartado un
estado compartido entre ramas: rompe el modelo de `Send` por un desborde que ya está
acotado.

**El tope por intento sigue en 10 USD.** Protegen cosas distintas: el de intento frena un
rol desbocado en una sesión, el de tarea frena la acumulación de roles sanos, y el de
intento es lo que acota el desborde en paralelo.

Fuera de esta adenda: cada devolución de Tech Lead abre una sesión nueva del Developer que
relee el worktree, la otra palanca grande de costo.

## Adenda (2026-09-15, cuarta): tope de tarea base más proporcional, y alcanzarlo pausa

La cuarta corrida de GH-12 escaló por el tope fijo de 100 USD con 106.27 gastados, las siete
subtareas aprobadas y la integración devuelta, a un Developer y una revisión del pull
request (issues #15 y #21). El tope estaba calibrado para cinco subtareas y el plan tuvo
siete más la integración: un número fijo no sabe cuánto trabajo tiene la tarea.

**El tope crece con el plan.** Antes del plan es la base; después,
`min(techo, base + por_subtarea × unidades)`, donde las unidades son las subtareas más la
integración cuando hay más de una, contada desde que existe el plan
(`rules.task_cap`). `run_role` lo lee del estado en cada intento, así sube apenas entra el
plan. `Configuration`: `task_budget_base_usd` 30, `task_budget_per_subtask_usd` 15 y
`task_max_budget_usd` pasa a ser el techo de la fórmula, 250. `plan_check` fija un máximo de 12
subtareas, para que el plan no suba el tope sin límite. Los números salen de la corrida 4
y **se recalibran tras la primera corrida con el issue #18 aplicado**, que debería costar
35-45 USD.

**Alcanzarlo escala y pausa** (ADR-0003, adenda): ya no tira la corrida. El comentario del
issue trae lo gastado por fase (definición, plan, desarrollo, revisión, sumando `runs` por
sección, y `other` para los intentos fallidos que escalaron) y la línea
`agentic resume --issue N --task-budget X`, con X lo gastado más una subtarea. El humano
decide: `--task-budget` sube el tope de esa corrida por encima de la fórmula y del techo,
nunca lo baja (`task_budget_usd`; el tope en vigor es el mayor de los dos; el techo limita
la fórmula, no al humano), así un monto dado
antes del plan no vuelve a frenar la tarea cuando el plan agranda la fórmula. `resume`
rechaza reanudar si el tope en vigor no supera lo gastado.

Descartados: releer el tope en caliente durante la corrida (rompe el versionado de
parámetros, pendiente 15 del README) y cuatro topes por fase (la fórmula ya lo expresa; la
fase solo se reporta).

## Adenda (2026-09-15, quinta): el pipeline dev→review, el nodo de fan-in y el gasto del proceso

La corrida 4 tardó 99.6 minutos de pared contra 119.5 de suma de nodos de rol: speedup real
1.2×. Los Developers ya corrían en paralelo, pero `review` recorría `delivered_subtasks` en
un `for` y, por la barrera de LangGraph, arrancaba recién cuando terminaba el último
Developer del lote. Medido: `movable-clock` esperó 392 s a `arch-test`; en el fan-out de
cuatro, las revisiones tardaron 693 s en serie contra 179 s en paralelo. Recuperable:
10.7-12.5 minutos, a costo neutro (issue #23).

**La revisión entra a la rama de su Developer.** Un subgrafo `develop → review` por
subtarea, compilado y despachado con un `Send` por subtarea lista. Cada revisión arranca
apenas entrega su Developer, sin esperar al más lento. El subgrafo entra por la revisión
cuando la subtarea ya está entregada: ahí reanuda una corrida que el tope frenó entre las
dos, y una entrega no se construye dos veces.

Un subgrafo como nodo le devuelve al padre su estado final, y el padre lo vuelve a plegar
con los mismos reducers: `cost_usd` contado dos veces y cada `runs`, `failures` y
`return_history` duplicado. El envoltorio devuelve el delta (`task.delta`, inversa de
`apply`); `subtask_id` nunca vuelve, porque el padre tiene uno por rama.

**Un nodo `integrate` de fan-in, del sistema, entre cada dos olas.** El plan le entrega la
primera ola, cada rama le devuelve lo que construyó, y una devolución es una ola más. No
corre ningún rol, así que no cuesta, y es lo único de la etapa técnica que corre solo. Eso
es para lo que está:

- **Los checkouts.** Crearlos es `git branch` más `git worktree add` sobre el mismo `.git`;
  en la corrida 4 los cuatro arrancaban con 250 ms de diferencia y un lock perdido era un
  crash sin escalado. Acá se crean de a uno, antes de la ola.
- **La rama de tarea.** Cada subtarea aprobada se mergea apenas se aprueba, no al final:
  la rama es siempre la pieza integrada hasta ahí y un conflicto aparece de a uno. El
  `Workspace.merge` pasa a devolver el motivo en vez de tirar `GitError`, deshace el merge
  y deja la rama limpia; lo que no aplica queda nombrado en la subtarea de integración y
  llega al prompt del integrador, que ve rama por rama qué ya está adentro y qué le queda.
  Un conflicto es un hecho sobre el código, no una falla del sistema, y puede aparecer en
  cualquier ola, no sólo en la última. Una dependencia que no se puede mergear sí frena la
  subtarea antes de pagarla: construir sobre código que no está es trabajo tirado, y
  desenredar dos ramas es decisión del humano; el escalado dice que una respuesta sola no
  alcanza, que hay que mergear a mano en el worktree de la subtarea.
- **El documento y el humano.** Cada rama guarda el documento con su propia vista, así que
  la última pisa a sus hermanas hasta que algo vuelva a guardar: eso es este nodo, con el
  estado ya fusionado. Y el escalado de cualquier rama se le cuenta al humano una sola vez,
  desde acá, después de que volvieron todas. Adentro de una ola una rama no puede saber que
  su hermana se frenó, y eso no cambia.
- **El estado de una tarea que escaló.** `status` tiene reducer "último gana": la rama que
  escaló escribió `waiting_on_human` y una hermana que entregó bien escribió `developing` en
  el mismo superstep. Cuál quedó era una moneda al aire. Lo resuelve el fan-in. El motivo no
  corría riesgo: nada más que un escalado escribe `escalated`.

Con todo pasando por el fan-in, el ruteo de reanudación apunta ahí en cuanto hay plan. La
excepción se mantiene explícita: un plan que no puede moverse (un `depends_on` a una
subtarea que no existe, un ciclo) no tiene ola a la que reanudar, y `resume` lo dice en vez
de sacar la tarea de la cola para no correr nada.

**La vuelta de una devolución pasa por el fan-in, no por adentro de la rama.** El issue
proponía `review → develop si returned` dentro del subgrafo. Con eso el Developer devuelto
y su revisor quedan tan ciegos a las hermanas como hoy, que es la falla de canal de #17.
Con la vuelta por el fan-in, la segunda ola arranca viendo lo que vio toda la primera
(la tabla de hallazgos es #42). Cuesta una barrera por ola; las devoluciones son minoría y
la ganancia grande es que la primera revisión no espere, que se conserva entera.

**Semáforo y gasto por proceso (`application/limits.py`).** Con la revisión adentro de la
rama, una ola de cuatro subtareas pone cuatro Developers y cuatro revisores en vuelo, más
los subagentes de cada revisor; un 429 llega como fallo de rol y dos de esos escalan la
tarea. `AGENTIC_MAX_CONCURRENT_ROLES`, default 4.

Al lado va el gasto. La tercera adenda aceptó que el desborde en paralelo quedara acotado
por N ramas × dos intentos × tope por intento, porque cada rama sólo ve el `cost_usd` del
fan-out. Con la revisión adentro de la rama ese número dejó de ser aceptable: con el loop
adentro eran 6 corridas de rol × 2 intentos × 10 USD = 120 por rama, 1440 con el máximo de
12 subtareas, sobre un techo de 250. **El tope se lee contra la mayor de las dos vistas**,
la del estado y la del proceso. No es estado compartido entre ramas: `RunLimits` no entra a
`TaskState` ni al ruteo, vive una corrida y viaja en `Ports` porque es lo que un caso de uso
recibe. Y sólo se niega a **arrancar** un intento: un rol ya corriendo nunca se corta, así
que nada pago se tira. Una corrida reanudada arranca el contador en cero y el estado trae la
historia, por eso la mayor de las dos.

Uno por **corrida**, no por proceso: `composition` arma el grafo al importar y un servidor
`langgraph dev` corre todas las tareas por ese objeto, así que uno por grafo le pasaría a la
segunda tarea el gasto de la primera. El grafo guarda uno por `thread_id`, que es el id de
tarea (ADR-0003). El CLI, una corrida por proceso, no nota la diferencia.

Fuera de esta adenda: el canal entre revisiones (invariantes del plan, tabla de hallazgos,
integrador condicional), en #42, resuelto en la adenda de ADR-0015. La tabla se escribe
desde este mismo nodo de fan-in, que es el único momento en que las ramas de una ola están
mergeadas.

## Adenda (2026-09-18, sexta): una devolución continúa la sesión del Developer, con delta y arranque en frío

La adenda del 2026-09-14 dejó resuelto el intento **fallido**: el reintento continúa la
sesión que el intento dejó. La devolución no estaba: `develop.py` llamaba a `run_role` sin
`resume` en ningún caso, ni siquiera con la subtarea en `returned`, así que el Developer
devuelto nacía de nuevo y rearmaba el contexto entero (~184k tokens cacheados por llamada;
$23.59 de retrabajo de Developer en GH-12 sobre tres devoluciones). Esta adenda extiende la
misma regla del intento fallido a la entrega devuelta. El comportamiento lo gobierna
`docs/product/changes-requested.md`.

**La sesión se reanuda y el mensaje nuevo es sólo el delta.** `resume` solo no alcanza:
`run_role` arma el prompt con `context_for` salvo que el llamador pase `prompt`, así que
reanudar sin acortar habría mandado el contexto dos veces. El delta lleva, incondicionalmente
y en este orden, lo que existe en el estado: `## Human`, la tabla de hallazgos abierta como
la dejó el fan-in, la sección de la revisión y el motivo de una línea. Nada más: ni el pedido
original, ni PM, ni el plan, ni la sección anterior del propio Developer, que es justamente
lo que la sesión ya tiene adentro. Son incondicionales porque razonar sobre lo que una sesión
ya vio no es confiable —`interview.consult` tiene `consulted_once` por eso mismo— y porque
son las únicas secciones que se mueven entre olas; re-mandar tres secciones chicas es barato
al lado de un workaround redescubierto, que es la falla de la corrida 4.

**El prompt en frío es del llamador, no de `run_role`.** Cuando la sesión registrada ya no
se puede reanudar, el intento siguiente arranca en frío; pero el contexto que ese arranque
necesita sólo lo conoce quien armó el mensaje. Sintetizarlo dentro de `run_role` rompía las
otras dos llamadas que pasan `resume` y `prompt` juntos: en `interview.turn` la transcripción
se escribe como sección recién al final, así que un `context_for` sintetizado en la ronda 3
no tiene ni la ronda 1 ni la 2 y el entrevistado contesta una pregunta que nunca vio; en
`consult` el prompt *son* las preguntas numeradas, que un contexto sintetizado descarta
entero, y PM no respondería nada mientras el llamador escribe el resultado como
`Clarifications`. Por eso `cold_prompt` lo pasa el llamador: `develop` manda contexto
completo más el mismo delta (la forma de `_with_context`, que sube a `run_role.py` porque
ahora la usan dos módulos), `consult` manda `_with_context` de su propio prompt, y
`interview.turn` no manda ninguno: sin `cold_prompt` no hay fallback y su comportamiento
queda idéntico al de hoy. Un arranque en frío cuesta a lo sumo un intento y nunca escala por
sí mismo: la definición ya bendice la sesión nueva cuando no hay sesión, y que la ausencia se
descubra al intentar en vez de al mirar el estado no la convierte en otra cosa.

**La serie nombra la decisión del sistema, no una causa.** `AttemptFailed.reason` es la
llamada del sitio que la levanta y nunca se adivina parseando el error, y el runner no puede
distinguir una sesión vencida de una red caída o un CLI ausente: el proxy disponible —el
llamador pasó `resume` y el intento no dejó sesión— se dispara con cualquier rotura del
harness anterior a la sesión. Así que no se agrega ningún valor a `FailureReason`. Lo único
que el sistema puede probar es que **soltó la sesión del llamador y arrancó en frío**, que es
una decisión suya tomada en una línea conocida de `run_with_retry`, y eso es lo que cuenta
`agentic.role.cold_fallback`, con el juego de labels de rol de siempre. Se decide una vez y
tres lectores la leen: el campo de la corrida, la entrada de `Failure` y la línea del
documento. `resumed_from` se decide en el mismo lugar y guarda la sesión que el intento
ganador reanudó de verdad, vacía cuando no reanudó ninguna: derivarla del argumento de
`run_role` mentía en dos caminos —el reintento que continúa la sesión de un intento fallido
sin que el llamador pidiera nada, y el fallback que no reanudó nada con un id válido en el
argumento— y el 100% de la definición se computa sobre ese campo. El fallback en un intento
que igual falla queda en su `Failure` y lo emite `_emit_failures`; en uno que entrega, en su
`RoleRun`. Exactamente un registro por fallback.

**La regla de contexto tiene su primera excepción, y el frío es la garantía.** El docstring
de `run_role` dice que cada rol lee enteras las secciones que declara en `Role.reads`. Desde
acá, un Developer devuelto corre sobre el delta y `Role.reads` lo honra su memoria de sesión;
cuando no hay memoria, el camino en frío lo vuelve a honrar entero. El docstring se corrige
en el mismo diff: sin eso, el próximo revisor lee el delta como una violación de la regla que
el módulo enuncia, y tendría razón.

**Lo que el delta no promete.** El revisor corre los chequeos declarados en el mismo worktree
del Developer (`review.py`, `cwd=checkout.path`), `develop` re-aplica el merge de las
dependencias en la vuelta, y un worktree podado se recrea con el `setup` del producto
corriendo de nuevo. La rama y los archivos son los que el Developer dejó; el alrededor no. El
delta abre con el hecho —rama, head actual, y que el checkout pudo cambiar— y el prompt del
rol devuelto dice además que su estado de shell no sobrevive: directorio actual, variables
exportadas, un venv activado, nada de eso sigue vivo, y un comando que asume un `cd` anterior
es el modo de fallo esperable de este arreglo. Como el prompt ya le dice que no repita el
camino que la revisión objetó: la memoria sirve para no releer, no para insistir.

Fuera de esta adenda: `ROLE_RETRIES`, el tope por intento, el límite del par y el ruteo de la
devolución por el fan-in, que no se tocan.
