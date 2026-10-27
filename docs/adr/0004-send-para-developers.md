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

## Adenda (2026-09-18, séptima): el reintento tras `max_turns` dice qué pasó, no que arregle algo

Un Developer que terminó su trabajo y agota `max_turns` (80, el tope más alto del sistema)
pierde el intento entero (issue #85: $16.38 en tres corridas, GH-69 dos veces en la misma
subtarea). El diagnóstico no es el reintento: la adenda del 2026-09-14 ya lo resuelve — un
corte de `max_turns` deja `session_id` como cualquier otro fallo del harness, y
`run_with_retry` reanuda esa sesión. La segunda corrida de GH-72 lo confirma: el intento 1 y
el 2 de `Developer: rework-figures` comparten sesión. El defecto es la frase que
`_retry_input` le manda a esa sesión reanudada: `"<PreviousAttemptFailed>...</PreviousAttemptFailed>\n\nRetry.
Fix exactly what failed."` — una orden de **arreglar un fallo**, para un modelo cuyo trabajo
no falló, solo se cortó. Esa frase es por qué el segundo intento vuelve a leer el worktree y
anuncia "my prior work is still in the worktree (uncommitted)" antes de poder entregar: nadie
le dijo que el corte no fue un error suyo.

**La solución es una frase, no plomería nueva.** `rules.turn_cut_retry_message(role)`
(`domain/rules.py`) compone el mensaje que el reintento recibe cuando
`failures[-1]["reason"] == "max_turns"`, seleccionado en `_retry_input`
(`application/run_role.py`); toda otra razón de fallo sigue recibiendo la frase de hoy, byte a
byte (test en el mismo diff). El mensaje afirma solo lo que el runner puede afirmar
honestamente: el tope de turnos terminó el intento, no algo que falló; la conversación y los
commits ya hechos están intactos; el checkout alrededor pudo haberse movido. Nunca nombra una
rama o un head — eso es de `returned_developer_delta`, que lee el estado que
`turn_cut_retry_message` no tiene.

**Tres brazos sobre el rol, el conservador por default:**

1. **`role.executes`** (el Developer, en su propia subtarea o en la integración): commitear
   antes de entregar, y que una entrega rechazada también cuesta turnos — con los chequeos en
   rojo, commitear y entregar igual, la brecha nombrada en `summary`. Ninguna de las dos
   órdenes puede volverse un loop: `develop.committed` levanta excepción sobre un árbol sucio
   (commitear no es opcional) y los rechazos del presubmit están acotados en dos.
2. **Un participante de entrevista enumerado** (`rules.INTERVIEW_PARTICIPANT_SLUGS`, el
   conjunto de slugs que ocupan el asiento de interviewer, interviewee o consultant en
   `INTERVIEW_DEFINITION`, `INTERVIEW_SOLUTION` e `INTERVIEW_SOLUTION_PLATFORM`): cerrar este
   turno con lo que tiene, la misma instrucción que `interview.CLOSE` ya le da al interviewer
   cuando se agotan las rondas — acá, para el turno que cortó el SDK, no la entrevista entera.
3. **Todo lo demás, por default** — `product_analyst_bug` es el rol para el que existe este
   brazo: no entregar un dictamen al que no se llegó; gastar lo que queda en la lectura que
   decide la entrega; si aun así no alcanza, decir en la salida qué quedó sin leer. Deriva
   literalmente de `docs/product/role-failures.md`: *"Un presupuesto agotado no se disfraza de
   entrega [...] Lo que sí se le pide es lo contrario de una escapatoria: priorizar la lectura
   que decide su entrega y, si igual no llega, que el motivo del intento fallido nombre qué le
   faltaba leer"*. El brazo por default, no una lista de roles permitidos, para que un rol que
   una tarea futura agregue no herede en silencio permiso para fabricar una entrega.

**Lo que esta adenda no toca.** No enriquece `max_turns_motive` ni lo que el runner
raspa para alimentarlo: los cinco cortes medidos leen desnudos (`max_turns (N) exhausted
without a result`), y ningún mensaje puede pedirle a un modelo que escriba ahí — ese campo lo
llena el sistema, nunca el rol. No agrega valor a `FailureReason`, no cambia
`ROLE_RETRIES`, el tope por intento, el tope de tarea, ni el ruteo de una devolución por el
fan-in. No edita `docs/product/role-failures.md`: no hay PM en este camino, y el texto arriba
cita el párrafo del que deriva en vez de tocarlo.

**La compuerta declarada para la subtarea B (`turn-notice`).** Antes de escribir ningún hook,
un spike en `tests/llm/` tiene que establecer tres cosas contra el SDK fijado:

1. Que **alguna señal del lado del hook** rastree el tope que el CLI de verdad hace cumplir
   (no `num_turns`: una corrida quedó con `turns: 17` bajo `max_turns: 12`; no `calls`: cuenta
   tráfico de sub-agentes, 168 llamadas contra 3 turnos en `tech_lead_review`).
2. Que un evento de hilo principal se distinga de uno de sub-agente — qué hace `agent_id` en
   el hilo principal.
3. La **unidad**: correr un rol hasta un corte deliberado y comparar el conteo del lado del
   hook en el corte contra `max_turns` y contra el `num_turns` reportado.

**Si las tres se sostienen**, un aviso — solo para `DEVELOPER` y `DEVELOPER_INTEGRATOR` — al
acercarse al tope, con un hook `PostToolUse` que cuenta, ignora todo evento con atribución de
sub-agente y devuelve solo `additionalContext` (nunca una decisión de permiso, ADR-0011,
cuarta adenda). **Si alguna falla**, la salida correcta es no escribir el hook: una frase en
`developer.md` y `developer_integrator.md` que le encargue el presupuesto al propio rol (ya
reciben `{{max_turns}}`), y nada más. Esos dos prompts son los únicos que esa subtarea puede
tocar, y la ruptura de fingerprint que eso produce ya queda contada aquí, para no
descubrirla en un panel: si B toca esos prompts, `prompts.fingerprint` cambia y la línea base
y el set de evaluación de antes de este commit dejan de ser comparables.

## Adenda (2026-09-21): se revierte "una dependencia que no se puede mergear sí frena la subtarea antes de pagarla"

La quinta adenda decidió que un conflicto de dependencia escalara al humano antes de que
ningún Developer corriera: "desenredar dos ramas es decisión del humano". En gym-saas #16
(tipado estricto, cadena `column-expression-spelling` → `routers-and-models` →
`persisted-id-adoption`) esa regla pausó la tarea dos veces con el mismo motivo — cada
subtarea borra su propio bloque `[[tool.mypy.overrides]]` de `pyproject.toml`, y como los
bloques son vecinos, git no sabe fusionar dos borrados adyacentes — y la resolución humana
fue, las dos veces, borrar el hunk entero (la unión de los dos borrados) en un turno, sin
ambigüedad ninguna. Costo medido: ~20 minutos por escalado con alguien mirando, horas sin
nadie; 2 de los 7 fallos de esa tarea.

**Se revierte esa frase.** El Developer de la subtarea, que ya tiene el worktree, las
herramientas y los checks declarados, recibe un turno para resolver el conflicto antes de
escalar: el mismo tipo de trabajo que el humano ya hizo dos veces a mano. `Workspace.merge`
y `integrate._merge_approved` no cambian — el conflicto que aborta sigue siendo el del fan-in,
sobre el estado compartido de una ola, donde una rama a medio mergear dejaría la siguiente
iteración fallando por una razón ajena — sino que `develop.py` gana un camino aparte,
`Workspace.merge_keeping_conflicts`, que nunca aborta, y `Workspace.dependency_state`, que
lee los hechos (contenido, en progreso, marcadores) sin más verbos de git de los que el
veredicto necesita. El sistema decide que la dependencia entró — ancestro, sin merge en
curso, sin marcador en los archivos que el propio merge reportó — nunca la palabra del
modelo; el caso que seguía sin nada para resolver (rama inexistente, árbol sucio, error de
git) sigue escalando `unmergeable`, con el mismo mensaje y la misma clase, byte a byte —
el test de `tests/core/process/domain/test_rules.py` no se toca.

**Techo de costo, para que la reversión no cambie el problema por otro.** Un intento de
Developer (80 turnos, el tope más alto del sistema; 10 USD, el tope por intento) por cada
arista de dependencia en conflicto, contra una pausa de horas sin nadie mirando. Si ese
intento tampoco resuelve — marcadores que quedan, o la dependencia que sigue sin estar — la
escalación de siempre se paga una vez, no en bucle: el veredicto posterior a la entrega
(`rules.dependency_delivered`) es lo único que decide seguir escalando.

## Adenda (2026-09-22): la compuerta declarada se revisa — el aviso disparaba y el intento moría igual (issue #102)

La séptima adenda declaró el aviso, y salió: `c840a41` lo shippeó como el hook `PostToolUse`
de hoy, un conteo del lado del hook y un margen de 39 contra `role.max_turns`, disparando una
vez por intento. Lo que el 2026-09-21/22 dejó por escrito sobre este mismo repo es que el
mecanismo estaba vivo y disparando en las diez corridas que agotaron el tope sin entregar:
leído verbatim de `.agentic/tasks/`, todo fallo `slug: developer` con `reason: max_turns` trae
`turn_notice: true` — GH-91 `Developer: dependency-merge` ($4.6204), GH-74 `Developer: types`
($2.2653), GH-88 `Developer: deliveries-count` ($3.4996) y `Developer: schema-guard`
($3.1735), el fallo que escaló en GH-38. Cero traen `false`. El defecto no es si el aviso
existe: es cuándo y con qué fuerza dispara.

**La cantidad que se cuenta cambia de asiento.** El conteo de hoy es el propio `PostToolUse`
del hook: un turno puede traer varias llamadas a herramienta (inofensivo, el conteo corre
adelante) o ninguna (fatal — una cola dentro de un sub-agente, a la que las skills `tdd` y
`resolving-merge-conflicts` del propio Developer recurren, deja un solo `PostToolUse` de hilo
principal por los turnos que sea que el sub-agente haya consumido, y el aviso nunca llega a
su ventana). El runner ya distingue la cantidad correcta: el loop de `run_once` incrementa
`calls` en cada `AssistantMessage` y prueba `parent_tool_use_id is None` una línea después. Un
`AssistantMessage` de hilo principal es >=1 por turno del harness por construcción, así que
este conteo no puede quedar atrás del tope como el anterior podía. El cambio es un incremento
adentro de esa rama que ya existe (`turns.value += 1`, `hooks.TurnCount`), y un holder mutable
construido junto a `_mark_notice` que viaja por el mismo parámetro que `on_notice` ya recorre
— `calls` y cada métrica que alimenta quedan byte a byte iguales. El filtro por `agent_id`
que protegía el conteo desaparece con el conteo mismo: `parent_tool_use_id` ya excluye el
mensaje de un sub-agente del lado del runner, así que el conteo no necesita mirar la
atribución de nuevo. El hook sí sigue mirando `agent_id`, pero por otra razón: disparar
sobre el evento de un sub-agente pondría la frase de cierre en la conversación de ese
sub-agente, no en la del rol que tiene que leerla, y sumaría un disparo a la cuenta con la
que se calibra la constante que no es ninguno de los suyos.

**Lo que este cambio no alcanza a cubrir, dicho en vez de escondido.** Una cola sin ninguna
llamada a herramienta de hilo principal — un turno final de solo texto — sigue sin recibir
nada: `additionalContext` en un `PostToolUse` es el único asiento que llega a un modelo
corriendo, y comprar un segundo mecanismo está prohibido por el primer invariante del plan
(GH-102). El conteo también puede quedar uno o dos mensajes desactualizado entre lo que el
loop ya escribió y lo que el hook lee: el efecto es un disparo levemente tarde, benigno contra
una ventana de este ancho.

**Un solo aviso, directivo, que vuelve a disparar.** El aviso de hoy se borra entero: disparó
en los diez cortes de `max_turns` de Developer con `max_turns: 80` y no cambió nada, y
dejarlo al lado de una segunda frase mandaría dos mensajes que un modelo no distingue, el
primero de los cuales ya demostró que sabe sobrevivir. Lo que queda es una sola ventana de
cierre por rol cubierto, que vuelve a disparar en cada evento de hilo principal pasado el
umbral — no un latch de una vez por intento — con una frase directiva en el mismo orden
cerrar-entonces-entregar que `rules.turn_cut_retry_message` ya usa después de un corte
(commitear lo que hay, entregar; solo si los checks no dan verde, entregar igual y nombrar la
brecha en `summary`).

**Umbral absoluto, nunca una resta contra `role.max_turns`.** `domain/turn_notice.py` no
nombra `role.max_turns` en ningún lado: el predicado es `count >= TURN_NOTICE_AT[role.slug]`,
una comparación absoluta sobre la cantidad contada, nunca una resta contra una cifra en otra
unidad. La razón es un dato ya escrito en el plan de esta tarea: una corrida que **entregó**
quedó registrada con `turns: 98` bajo `max_turns: 80` — ni `num_turns` ni este conteo son la
misma cosa que el tope que el CLI hace cumplir, y la vieja resta `role.max_turns - count <=
margin` mezclaba tres cantidades que no comparten unidad.

**El valor, 15, es un punto de partida declarado, no una derivación.** Este conteo (el
`AssistantMessage` de hilo principal) no existía antes de este commit, así que no hay ninguna
cifra registrada en su propia unidad para citar; escribir una disfrazada de medición es
exactamente la falla que esta revisión corrige — el 39 heredado era en sí mismo una cita, en
otra unidad, de un solo peor caso (GH-72, `Developer: emission`, resumida). 15 queda por
encima del cierre ordinario ya registrado (6-27 turnos de una corrida resumida y cortada, la
evidencia propia de este issue) más el margen acotado de dos entregas rechazadas por el
presubmit (ADR-0011, segunda adenda) — cómodamente por encima del techo de ese rango, así que
un cierre ordinario entra en la ventana que abre. Su propia calibración hacia adelante es
`turn_notice_firings`, el conteo que cada intento avisado deja escrito: un solo disparo en
todo el set de evaluación dice que la ventana abre demasiado tarde para importar, y un conteo
alto en una corrida que sí entrega dice que abre demasiado temprano.

**Cada disparo es un hecho distinto del anterior.** `additionalContext` se agrega a la
conversación, así que un disparo se paga en el contexto de cada llamada posterior — el costo
es lineal en la ventana, no una frase pagada una sola vez. Una directiva repetida palabra por
palabra treinta veces es la mejor evidencia que el propio modelo tiene de que no significa
*ahora*: el mecanismo exacto por el que el aviso de una sola vez falló, ahora a mayor
frecuencia si nada cambiara. Por eso cada disparo lleva su propio índice (`domain/turn_notice.
notice_context`), la única cifra que cambia entre disparos del mismo intento sin volver a leer
`role.max_turns` ni `num_turns`.

**Lo que queda escrito, y cómo se lee.** `turn_notice` mantiene su nombre, su tipo y su
significado verbatim: si el aviso disparó alguna vez en el intento, `True` — ni una palabra
distinta de lo que ya significaba (ADR-0008: las claves se agregan, nunca se renombran).
`turn_notice_firings: int` se agrega al lado, en `RawResult`, `AttemptFailed`, `Failure` y el
frontmatter, con default `.get(..., 0)` documentado para todo documento escrito antes de este
commit — el mismo tratamiento que `denials` y `deliveries` ya tienen. La nota que el sistema
escribe en la sección (`turn_notice.notice_note`) ahora distingue dos casos: un intento que
entró a la ventana y entregó en el mismo intento, de uno que entró, el harness lo cortó, y un
intento posterior entregó — y ambas variantes dicen cuántas veces disparó en total.

**El riesgo que se acepta, con su vigilancia nombrada.** Un Developer al que se le dice
"entregá ahora" puede gastar sus dos rechazos del presubmit y entregar en rojo donde antes
hubiera seguido puliendo. No se mitiga escondiéndolo: la ventana angosta y el orden de la
frase (cerrar lo que hay, commitear, entregar; solo si no llega a verde, entregar y nombrar la
brecha) son la mitigación, y lo que se vigila es la familia `process-*` de
`agentic.review.findings` junto con el resultado del presubmit — ninguna serie nueva, ninguna
movida.

**Lo que esta adenda no cambia.** `.agentic/stack.yaml`, `ROLE_RETRIES`, el tope por intento,
el tope de tarea y las clases de escalación quedan fuera: un corte por `max_turns` sigue
siendo un fallo de rol, y su segunda ocurrencia sigue siendo una escalación, byte a byte.
Ningún archivo bajo `domain/prompts/` se toca: `{{max_turns}}` renderiza el mismo valor de
siempre, así que `prompts.fingerprint` no se mueve mientras el comportamiento en tiempo de
ejecución sí cambia — la comparación segmentada por fingerprint de antes de este commit no
va a mostrar este cambio, y esa es la razón por la que se dice acá en vez de descubrirse en
un panel.

## Adenda (2026-10-05): el tope de tarea es un techo plano de 250, sin fórmula

**Por qué.** El pedido del issue #181 y el caso que lo disparó: GH-166 pausó en 47.69 de
45.00 USD con una sola subtarea, y el plan ya había gastado 27.60. Los números de la cuarta
adenda salieron de la cuarta corrida de GH-12 y no se recalibraron después del issue #18,
como se había dicho.

**El corpus.** Fuente: el `cost_usd` del frontmatter de los documentos de las tareas
entregadas de agentic-system, apostrophe y gym-saas al 2026-10-05, agrupadas por cantidad de
subtareas, tal como las reporta el pedido del issue #181. 98 tareas entregadas:

| Subtareas | n | mediana USD | p90 USD | máx USD | tope por fórmula |
|---|---|---|---|---|---|
| 1 | 42 | 23.5 | 31.9 | 93.0 | 45 |
| 3 | 17 | 28.9 | 37.3 | 92.2 | 75 |
| 4 | 17 | 36.2 | 82.1 | 143.2 | 90 |
| 5 | 10 | 45.6 | 77.2 | 97.4 | 105 |
| 6+ | 12 | 84.7 | 128.2 | 181.4 | 120–195 |

9 terminaron por encima del tope que les daba la fórmula:

- apostrophe: GH-8 (143.2/90), GH-55 (93.0/45), GH-26 (92.2/75), GH-58 (181.4/165), GH-63 (45.7/45);
- agentic-system: GH-154 (61.9/45), GH-137 (96.6/90), GH-151 (48.3/45), GH-112 (46.2/45).

Ninguna de las 98 llegó a 250 (máximo 181.4, apostrophe GH-58).

**Decisión.**

- `rules.task_cap(ceiling, override)` es `max(ceiling, override)`, sin `TaskState`: el tamaño
  del plan no puede llegar a la cuenta.
- Los campos `task_budget_base_usd` y `task_budget_per_subtask_usd` y sus variables
  `AGENTIC_TASK_BUDGET_*` salen de `Configuration`; `task_max_budget_usd` (250) es el tope.
- `suggested_budget` propone lo gastado más un tope por intento.
- Alcanzar el tope sigue escalando y pausando.
- `--task-budget` sube el tope y nunca lo baja.
- El tope por intento sigue en 10 USD: ningún documento registra un fallo `reason: limit`.
- El máximo de 12 subtareas de `plan_check` se queda.

**Qué reemplaza.** De la cuarta adenda: la fórmula `min(techo, base + por_subtarea ×
unidades)`, los campos `task_budget_base_usd` y `task_budget_per_subtask_usd`, y "X lo gastado
más una subtarea" (ahora "lo gastado más un tope por intento"). De ADR-0034: "la base de 30
USD" para una revisión de stack.

**Consecuencias.**

- Las corridas sin plan (definición, fase de plan, revisión de stack) pasan de 30 a 250.
- Una revisión de stack queda acotada por su propio lazo en unos 200 USD: a lo sumo 4 rondas
  de entrevista de dos corridas de rol, un turno de cierre del entrevistador y una corrida del
  autor (`stack_review.py:59,64`, `interview.py:246,292`), cada una de a lo sumo 2 intentos de
  10 USD. La cota se lee del lazo, no está medida.
- Una tarea pausada por el tope antes del 2026-10-05 reanuda sin `--task-budget` si lo gastado
  es menor que 250. Su `reason:` guardado muestra el tope viejo como historia, junto a
  `task cap: 250.00 USD (ceiling 250)`.
- Un `--task-budget` igual o menor que 250 no hace nada, y el resumen muestra el techo. Un
  aviso es otro issue.
- Los nombres `AGENTIC_TASK_BUDGET_*` viejos se ignoran, no se rechazan. Hasta que el humano
  saque `AGENTIC_TASK_MAX_BUDGET_USD=1000000` del `.env` de los dos stage0 tras el merge, ese
  valor manda.
- Ninguna señal se mueve, y `prompts.fingerprint` no se mueve.

## Adenda (2026-10-27): las subtareas ya no son un nodo cada una ni van en olas

GH-364. Dos frases de este ADR quedan superadas por el ADR-0067: "un nodo por subtarea,
checkpointeado" (el nodo `pipeline` corre ahora un loop propio, y se pierde el checkpoint por
rama, que reemplazan el documento y `rules.resume_source`) y, en la adenda quinta, "Cuesta una
barrera por ola" (una subtarea arranca cuando `rules.next_to_start` la habilita, sin esperar
al resto de su ola). La revisión dentro de la rama, el fan-in del sistema y la vuelta de una
devolución por `integrate` siguen como se dijeron.
