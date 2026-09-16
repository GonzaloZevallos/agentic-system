# Proceso

Los roles, la tarea y su documento, el camino y sus reglas, y la corrida de cada rol. Es
el dominio central del sistema: los otros dos contextos lo conocen, él no los conoce.

## Language

### Actores

**Rol**:
Un agente con responsabilidad fija dentro del proceso. Hay 17.
_Avoid_: agente (a secas, cuando se habla de uno concreto)
_Campo_: role — CrewAI, como atributo de su Agent; el resto del campo dice `agent` a secas

**Humano**:
Aprobador y destino de los escalados. Revisa en lote, una vez por día.
_Campo_: human-in-the-loop — estándar (LangGraph, OpenAI Agents SDK, Google ADK)

**PjM** (Project Manager):
El rol que clasifica, enruta, registra todo, controla loops y cierra.
_Avoid_: Lead, orquestador
_Campo_: de la organización de software humana; el patrón agéntico es supervisor (LangGraph) u orchestrator (Microsoft Agent Framework)

**PM** (Product Manager):
Dueño de la definición única y del conocimiento de producto, que destila al aprobar.
_Campo_: de la organización de software humana

**Pensador**:
Rol que produce: Product Analyst, UX Designer, UI Designer, Developer, Tech Writer. En el código y en los prompts se llama `actor`.
_Campo_: actor — Reflexion (Shinn 2023), que lo define junto a Evaluator y Self-Reflection; generator en Cobbe 2021 y Lightman 2023

**Ejecutar**:
Escribir archivos del producto y correr comandos sin aprobación por comando. Solo Developer ejecuta, dentro de una jaula: su checkout, un tope de costo y lo que el sistema le niega aun así. Correr los chequeos del producto sin escribir no es ejecutar: Tech Lead, al revisar, lee el código y el diff y corre los chequeos sobre el test que respalda una objeción, y los entrevistadores escriben únicamente el glosario y los ADRs.
_Campo_: sin término de arte para el permiso; el campo nombra el mecanismo (tool use), no la autonomía que le concede

**Validador**:
Rol que revisa a un pensador aportando una fuente que el pensador no tuvo. En el código y en los prompts se llama `verifier`, no `judge`: el judge opina y el verifier ejecuta, y desde ADR-0016 el Tech Lead corre los chequeos que respaldan cada objeción.
_Avoid_: revisor, judge
_Campo_: verifier — Cobbe 2021 y Lightman 2023 ("Let's Verify Step by Step"); Evaluator en Reflexion, critic en CriticGPT, LLM-as-a-judge en Zheng 2023

**Par**:
El rol que devuelve y el rol que recibe la devolución. Las devoluciones se cuentan por par; cuando hay subtarea, por subtarea.
_Campo_: sin término de arte

**Tech Lead efímero**:
Segundo Tech Lead que existe solo durante la entrevista técnica, con modelo más capaz y prompt de adversario. Entrevista a Tech Lead; no decide. No es un rol de la lista: es el entrevistador del pingpong.
_Campo_: sin término de arte

**Developer integrador**:
El Developer que termina de unir las subtareas en la rama de tarea: lo que la integración incremental no pudo mergear solo, y el trabajo de integración que el plan declare. Corre solo si hay algo de eso; sin conflicto y sin trabajo declarado, la rama ya está unida y va directo a su revisión. La revisión de la pieza integrada, en cambio, corre siempre. Cuando la tarea tiene una sola subtarea, no hay integración: la rama de esa subtarea es la rama de tarea.
_Campo_: sin término de arte

**Rol proactivo**:
Rol con reloj propio que genera tareas o insumos sin que nadie se lo pida.
_Campo_: ambient agent — LangChain (Chase, enero de 2025): un agente que escucha un flujo de eventos en vez de un mensaje humano. Google ADK usa el nombre sin definirlo

### Trabajo

**Tarea**:
Unidad de trabajo que entra al sistema. Siempre de un tipo: bug, feature o platform enhancement.
_Avoid_: ticket, issue (el issue es la bandeja, no la tarea)
_Campo_: task — estándar (CrewAI, Google ADK, Temporal); instance en SWE-bench

**Subtarea**:
Parte de una tarea que Tech Lead asigna a un Developer.
_Campo_: subtask — usado por MetaGPT y CrewAI, sin definición compartida entre frameworks

**Camino**:
La secuencia de roles que recorre una tarea según su tipo, solo de ida. Adónde vuelve una devolución lo decide el motivo, no el camino.
_Avoid_: flujo, pipeline
_Campo_: workflow — estándar (Microsoft Agent Framework, OpenAI, Google ADK, Temporal, CrewAI Flows); graph, con node y edge, en LangGraph

**Etapa de definición**:
Tramo del camino de feature entre Product Analyst y la definición única. Estrictamente secuencial.
_Campo_: sin término de arte

**Definición única**:
El texto de PM que gobierna el resto de la tarea: comportamiento, flujo, diseño y textos.
_Avoid_: spec, requerimientos
_Campo_: PRD — de producto humano. `spec` en el mundo agéntico viene pegado a un flujo de archivos (GitHub Spec Kit: `/specify` → `/plan` → `/tasks`) que no es el nuestro

**Flag**:
Marca de alcance sobre una tarea, como "toca UI" o "toca textos", tomada del vocabulario de flags del producto. Los propone Product Analyst y los valida PM; el set validado reemplaza al propuesto.
_Campo_: sin término de arte

**Pieza integrada**:
El código de todas las subtareas unido en la rama de tarea y revisado por Tech Lead. Lo une el sistema, subtarea por subtarea apenas cada una se aprueba; el Developer integrador resuelve lo que esa unión no pudo hacer sola y el trabajo de integración que el plan declare.
_Campo_: sin término de arte

**Ola**:
El conjunto de subtareas que el sistema despacha junto: cada una con su Developer y su revisión, en paralelo con las demás. Entre dos olas corre el sistema solo. Una devolución se atiende en la ola siguiente, no dentro de la misma.
_Avoid_: lote, fan-out (el mecanismo, no el concepto)
_Campo_: super-step — LangGraph, tomado de Pregel: los nodos que corren en paralelo son el mismo super-step. Nombra el paso del grafo, no el conjunto de subtareas

**Rama de tarea**:
La rama del producto donde termina el código de una tarea, una por tarea. Cada subtarea trabaja en su propia rama, aislada de las otras, y el sistema las trae acá.
_Campo_: branch — de git, no del campo agéntico

**Corrida**:
Una ejecución del sistema sobre una tarea, desde que entra hasta el cierre o el escalado.
_Avoid_: run, ejecución
_Campo_: run — estándar (LangSmith, Langfuse, OpenTelemetry); session en Google ADK y el Claude Agent SDK, thread en LangGraph

**Corrida de rol**:
Una ejecución de un rol dentro de una corrida. Una tarea tiene tantas como roles visitó,
contando repeticiones.
_Campo_: invoke_agent — OpenTelemetry GenAI semantic conventions, hoy en estado Development. Es el único nombre preciso que existe: LangGraph dice super-step, OpenAI y el Claude Agent SDK turn, CrewAI iteration, Google ADK agent call, SWE-agent step

### Cómo se piden cosas

**Consulta**:
Pregunta de un rol al dueño de una decisión, respondida dentro de la misma corrida, sin entregar la tarea. No es devolución ni escalado y no cuenta para ningún límite. Hoy: el Tech Lead consulta a PM durante la entrevista de solución lo que la definición no dice; PM responde en la misma corrida y su respuesta aclara la definición.
_Avoid_: pregunta para el humano (eso es lo que el dueño tampoco puede decidir)
_Campo_: sin término de arte

**Entrevista**:
Rondas de preguntas de un rol a otro para afinar una decisión antes de escribirla: el entrevistador pregunta y recomienda, el entrevistado decide. Hay dos: Product Analyst entrevista a PM sobre la definición, y el Tech Lead efímero entrevista a Tech Lead sobre la solución. Tiene tope de rondas; la transcripción entera queda en el documento de tarea. Lo que el entrevistado no puede decidir va a una consulta si la entrevista tiene consultado, y si no, al humano como pregunta.
_Avoid_: pingpong, consulta (la consulta es una pregunta suelta; la entrevista es la forma del trabajo)
_Campo_: sin término de arte

**Pregunta para el humano**:
Lo que el dueño de una decisión marca como fuera de su alcance: ni el issue ni el conocimiento de producto (definiciones, glosario, ADRs) lo deciden, y sin la respuesta la definición cambia. Lo que un ADR decide nunca es pregunta para el humano; lo que no cambia la definición tampoco, es un supuesto. En la definición la marca PM entrevistado; en la solución, PM consultado. El Tech Lead nunca pregunta al humano. Se plantea una sola vez: nadie la responde durante la corrida, así que lo ya planteado no se repite en rondas siguientes ni el Tech Lead lo vuelve a consultar. Se retira cuando una ronda posterior la resuelve: el dueño la responde y la saca de la lista. Escala la tarea con las preguntas que siguen abiertas al terminar la entrevista.
_Avoid_: duda (una duda que PM resuelve con un supuesto no es pregunta)
_Campo_: sin término de arte: el mecanismo tiene nombre (interrupt, LangGraph) y la pregunta misma no

**Supuesto**:
Decisión que el dueño toma sobre lo que ni el pedido ni el conocimiento resuelven pero no cambia la definición, y deja escrita en su respuesta. No va al humano.
_Avoid_: pregunta para el humano
_Campo_: sin término de arte

**Decisión del humano**:
Lo que el humano responde a un escalado: todo lo que comenta en el issue después del último aviso del sistema. Entra al documento de tarea, la lee todo rol y no se vuelve a preguntar: vale para toda la tarea y pesa sobre lo que contradiga.
_Avoid_: respuesta, comentario
_Campo_: sin término de arte

**Handoff**:
El bloque "Para el siguiente rol" con que cierra cada sección.
_Campo_: handoff — de OpenAI (primero Swarm, después el Agents SDK), hoy vocabulario compartido con AutoGen y langgraph-swarm. Allá es la transferencia de control a otro agente; acá es el bloque de texto que se le deja al que sigue

### Cómo se entrega y se revisa

**Puerta de entrega**:
Lo que el sistema mira cuando un Developer entrega, antes de que la entrega cuente: que haya commit, que el árbol esté limpio y que los chequeos del producto pasen. Si algo falta, rechaza la entrega con el motivo dentro del mismo turno, como mucho dos veces; después la deja pasar con el resultado de los chequeos escrito por el sistema en la sección del Developer. Un rechazo de la puerta no es devolución ni fallo de rol.
En el código se llama `Presubmit`, el port, y `CheckoutPresubmit`, la implementación sobre el checkout de un Developer.
_Avoid_: gate, validación, hook (el mecanismo, no el concepto)
_Campo_: presubmit check — Software Engineering at Google, cap. 20: "an analysis that blocks committing a pending code change". required status check en GitHub, gated commit en Azure DevOps, admission control (validating, no mutating) en Kubernetes

**Revisión**:
Pase de Tech Lead sobre la entrega de una subtarea o de la pieza integrada, con hallazgos y un veredicto que se deriva de ellos: aprobada o devuelta. Una subtarea tiene tantas revisiones como pases recibió.
_Avoid_: review, code review (el nombre de la skill, no del pase)
_Campo_: code review — estándar

**Revisión incremental**:
Revisión que arranca del head revisado en el pase anterior, no de la base de la subtarea, y toma la devolución anterior como lo que hay que verificar. Toda revisión después de una devolución es incremental.
_Campo_: sin término de arte; GitHub lo ofrece sin nombrarlo, como "changes since your last review"

**Head revisado**:
El commit hasta donde llegó la última revisión de una subtarea.
_Campo_: sin término de arte

**Integración incremental**:
Traer cada subtarea a la rama de tarea apenas se aprueba, en vez de todas juntas al final. Lo hace el sistema, entre dos olas. Un merge que no aplica no es un fallo: queda anotado para el integrador.
_Campo_: de la integración continua, de la que toma la idea (integrar seguido y chico) y no el mecanismo

**Invariante**:
Regla del plan que toda subtarea cumple, verificable sobre el diff de una sola. La propone el Tech Lead efímero desde lo que destapó la entrevista; Tech Lead la adopta en el plan o la rechaza con motivo. Llega al brief de cada Developer y la revisión la verifica como verifica el brief y las costuras. Es el único canal entre revisiones que funciona en la primera ola, porque está escrito antes de que nadie codee.
_Avoid_: regla, restricción, contrato
_Campo_: invariant — de Design by Contract (Meyer): una condición que se sostiene en todo momento. Allá es sobre una estructura de datos o un loop; acá es sobre un plan repartido entre subtareas que no se ven

**Hallazgo**:
Lo que Tech Lead encuentra al revisar, con su lugar, la afirmación, la evidencia (el comando que corrió y su salida, o la cita del estándar o del brief), la severidad, el alcance y la categoría. Solo un hallazgo bloqueante sobre la subtarea la devuelve; el veredicto se deriva de los hallazgos. Una objeción de comportamiento que no se reprodujo es una observación.
_Avoid_: comentario, issue
_Campo_: finding — de auditoría y seguridad de nube (AWS Security Finding Format), no de code review: SARIF dice result, que es el dato, y GitHub dice alert, que es la presentación

**Tabla de hallazgos**:
Los hallazgos de todas las revisiones de una tarea, cada uno con su estado: abierto, arreglado o descartado. Una fila es un lugar más una afirmación, y el veredicto de la primera vez es el que queda: una revisión posterior no relitiga lo que otra ya descartó. Una fila se cierra sólo porque un revisor la dio por saldada; dejar de nombrarla no la cierra, porque una revisión incremental tiene instrucción de no volver a mirar lo que ya aprobó. La leen el revisor, el Developer y el integrador, en la sección del documento, que lleva las abiertas; el registro crudo de todas las pasadas vive en el frontmatter. La escribe el sistema en el fan-in, que es el único lugar que ve todas las ramas de una ola; cada revisión solo agrega. Muere con la tarea.
_Avoid_: backlog, lista de issues, deuda
_Campo_: sin término de arte; lo más cerca es la lista de alerts de GitHub code scanning, que vive en el repositorio y no muere con el cambio que la abrió

### Cómo se rechaza trabajo

**Devolución**:
Rechazo con entrega hacia atrás. Cuenta para el límite del par. En el código se llama `ChangesRequested`, y su categoría `ChangeCategory`; el veredicto sigue siendo el valor `returned`, que es dato ya escrito, igual que la métrica `agentic.task.returns` y las claves del documento de tarea (ADR-0008, adenda).
_Avoid_: rechazo (salvo para el acto puntual de QA), rebote
_Campo_: request changes — GitHub, uno de los tres valores de `event` de una review (APPROVE, REQUEST_CHANGES, COMMENT); el par critique + revision en Constitutional AI (Bai 2022)

**Motivo de devolución**:
Por qué se devuelve: de comportamiento o técnico. Decide quién recibe una devolución de QA: PM si es de comportamiento, el Developer de la subtarea si es técnico, Tech Lead cuando el camino no tiene PM.
_Campo_: sin término de arte
Son tres términos y no uno porque cada uno tiene un lector distinto: el motivo es texto para quien recibe la devolución, la categoría es dato cerrado que la medición cuenta, y la devolución es el hecho que cuenta para el límite del par.

**Categoría de devolución**:
El motivo de una devolución como dato, de una lista cerrada: violación de un estándar (arquitectura, tipado silenciado, costuras, otro), brecha contra la spec (comportamiento, test faltante, alcance), entrega que no es entrega (sin commitear, suite en rojo), calibración del plan o problema que cruza subtareas. Las devoluciones de PM a Product Analyst llevan la categoría de definición. Es lo que la medición cuenta y lo que hace computable "el mismo motivo entre los mismos roles". Distinta del motivo en texto, que sigue siendo para quien recibe la devolución.
_Avoid_: tipo de devolución, motivo (para la categoría)
_Campo_: lo más cerca son las nueve etiquetas de Conventional Comments (praise, nitpick, suggestion, issue, todo, question, thought, chore, note), pero clasifican el comentario y no la devolución

**Devolución interna**:
Devolución que se resuelve dentro del par sin pasar por PjM. Se reporta a PjM igual.
_Campo_: sin término de arte

**Límite de devoluciones**:
Máximo de devoluciones por par antes de escalar. Hoy 2.
_Campo_: sin término de arte; `max_iter` en CrewAI y `max_turns` en el OpenAI Agents SDK topean el loop de un agente solo, no el ida y vuelta entre dos

### Cómo termina una corrida

**Listo para revisión**:
La tarea tiene su pieza integrada en un pull request del producto y espera al humano. Mientras el camino no tenga QA, el merge del humano sin tocar el diff es el cierre, y es lo único que puntúa en el set de evaluación.
_Avoid_: terminada, cerrada (todavía no lo está)
_Campo_: ready for review — GitHub, el estado de un pull request que sale de draft

**Cierre**:
PjM da por terminada la tarea: aprobada, con las aprobaciones del tipo, o descartada, cuando PM dictamina que el comportamiento es el esperado.
_Campo_: sin término de arte

**Escalado**:
Lo que un rol, un par o el tope dejan dicho cuando la tarea no puede seguir sin el humano, con las preguntas si las hay. El sistema lo entrega al humano en el issue y pausa la tarea: queda esperando con su estado guardado, sin gastar, y se retoma desde el mismo punto. Son cinco las situaciones que lo provocan, y cuál fue decide qué hace falta para reanudar: PjM no puede clasificarla o su camino no existe, un par llegó a su límite, un rol falló dos veces, se alcanzó el tope de tarea, o quedaron preguntas para el humano. El motivo viaja como texto libre, no como dato cerrado.
_Avoid_: motivo de escalado, causa, tipo de escalado (el motivo es parte del escalado, no otro término), pausa, bloqueo, espera
_Campo_: escalation — de soporte y operaciones, no del campo agéntico; ahí el mecanismo es interrupt (LangGraph): frena el grafo, expone un valor al cliente y se reanuda con `Command(resume=...)`

**Reanudación**:
La tarea sigue desde donde se pausó. Hace falta una decisión del humano cuando hay preguntas abiertas o un par llegó a su límite, y con ella el contador de ese par vuelve a cero; un tope alcanzado o un fallo de rol se reanudan sin decisión. Nada se reanuda bajo un tope ya gastado. Si el estado guardado se perdió, se reconstruye desde el documento de tarea, cruzado con git.
_Avoid_: reintento (eso es tras un fallo de rol), relanzar (eso es empezar de cero)
_Campo_: resume — estándar (LangGraph, Temporal, el Claude Agent SDK)

**Fallo de rol**:
El rol se rompe: su ejecución falla, se le acaban los turnos sin resultado, la salida estructurada falta o es inválida, el `markdown` está vacío. Se reintenta una vez con el error como entrada, continuando la conversación del intento fallido cuando la hay. Distinto de devolución; no cuenta para el límite.
_Avoid_: error (demasiado amplio), reintento (es parte del fallo, no otro término)
_Campo_: sin término de arte; el reintento sí lo tiene, `retry`, pero nombra la vuelta y no la rotura

**Tope por intento**:
Gasto máximo de un intento de cualquier rol. Excederlo es un fallo de rol. Red de seguridad contra un rol desbocado, no presupuesto de trabajo. Hoy 10 USD.
_Avoid_: presupuesto (eso sería lo que se espera gastar), guardrail (no significa tope de gasto en ningún framework: en el OpenAI Agents SDK es validación de input y output con tripwire, en CrewAI validación del output de una task)
_Campo_: per_instance_cost_limit — SWE-agent

**Tope de tarea**:
Gasto acumulado de una tarea a partir del cual el sistema escala y pausa en vez de seguir. Crece con el plan: una base hasta que hay plan, después la base más una parte por subtarea, integración incluida, con un techo que limita esa cuenta. Se mira antes de cada intento de cualquier rol, contra lo que gastó la corrida entera y no sólo la rama que va a gastar. Subirlo al reanudar es decisión del humano, que puede pasar el techo y nunca lo baja. Alcanzado, lo que ya está corriendo termina: sólo frena lo que todavía no arrancó. No es fallo de rol ni devolución y no cuenta para ningún límite. Como el tope por intento, es red de seguridad, bien arriba de lo que cuesta una tarea sana. Hoy 30 USD de base, 15 por subtarea, techo 250.
_Avoid_: presupuesto de tarea
_Campo_: total_cost_limit — SWE-agent

### Artefactos

**Documento de tarea**:
Un archivo por tarea con frontmatter YAML de PjM y una sección Markdown por rol. El registro
crudo del trabajo de la tarea, materia prima del conocimiento de producto: guarda también las
entregas que una devolución pisó y las corridas de cada rol. Nadie lee tareas viejas para
saber qué está vigente; para eso está el conocimiento de producto.
_Campo_: sin término de arte. `blackboard`, la palabra que uno esperaría, es de la IA clásica (Hearsay-II; Nii 1986) y no aparece en el código de LangGraph, AutoGen, CrewAI, Google ADK ni el OpenAI Agents SDK

**Sección**:
La parte del documento que escribe un rol. Cada rol declara qué secciones lee.
_Campo_: sin término de arte

**Bandeja**:
Los issues de GitHub del repo del producto con la etiqueta `agentic`. La entrada, no el documento.
_Campo_: inbox — de los sistemas de tickets; el contenedor concreto es el issue tracker

**Cola de escalados**:
Los issues con la etiqueta `needs-human`.
_Campo_: sin término de arte

**Pull request de tarea**:
El pull request del producto que abre el sistema desde la rama de tarea, con la definición única y el plan de Tech Lead en el cuerpo. Es la entrega al humano; el issue recibe el link al pull request y al documento de tarea. Nada más se escribe en el issue: los roles se hablan por el documento.
_Campo_: pull request — GitHub; merge request en GitLab

**Override de prompt**:
Un prompt de rol que vive en el repo del producto y pisa el default del sistema.
_Campo_: sin término de arte

### Repos

**Sistema**:
Este repo: roles, prompts, caminos y las reglas del proceso. Es también uno de los dos productos sobre los que trabaja (ADR-0017).
_Campo_: sin término de arte. `self-hosting`, la palabra de compiladores para el programa que se construye a sí mismo, está tomada: en Temporal y Kubernetes significa "operás vos la infra"

**Producto**:
El repo sobre el que trabaja el sistema, que recibe como parámetro. Hay dos: el SaaS de gimnasios y el propio sistema, que desde ADR-0017 se desarrolla a sí mismo. El sistema nunca escribe en el checkout desde el que corre, que es stage0; que el repo del producto sea también el suyo no cambia esa regla.
_Campo_: sin término de arte

**Contexto de producto**:
La raíz del repo del producto, que el sistema recibe como parámetro. Ahí está todo lo que varía por producto: el código, los ADRs y `.agentic/`.
_Campo_: workspace root — de las herramientas de desarrollo; `cwd` en el Claude Agent SDK

**Stage0**:
La copia del sistema, fijada a un sha, que ejecuta una corrida. No es la que se está editando: el sistema que corre y el que se modifica son dos, aunque el repo sea el mismo. Por analogía con el bootstrapping de un compilador, **stage1** es el sistema nuevo corrido por el viejo y **stage2** el sistema nuevo corrido por sí mismo; la prueba de que salió bien no es un número sino que stage3 dé idéntico a stage2.
_Avoid_: runtime (es el harness de cada rol), sistema fijado
_Campo_: stage0 — del bootstrapping de rustc (rustc-dev-guide), que llama "the same-result test" a la comparación de stage3 con stage2
