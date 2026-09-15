# Proceso

Los roles, la tarea y su documento, el camino y sus reglas, y la corrida de cada rol. Es
el dominio central del sistema: los otros dos contextos lo conocen, él no los conoce.

## Language

### Actores

**Rol**:
Un agente con responsabilidad fija dentro del proceso. Hay 17.
_Avoid_: agente (a secas, cuando se habla de uno concreto)

**Humano**:
Aprobador y destino de los escalados. Revisa en lote, una vez por día.

**PjM** (Project Manager):
El rol que clasifica, enruta, registra todo, controla loops y cierra.
_Avoid_: Lead, orquestador

**PM** (Product Manager):
Dueño de la definición única y del conocimiento de producto, que destila al aprobar.

**Pensador**:
Rol que produce: Product Analyst, UX Designer, UI Designer, Developer, Tech Writer.

**Ejecutar**:
Escribir archivos del producto y correr comandos sin jaula. Solo Developer ejecuta. Correr tests sin escribir no es ejecutar: Tech Lead, al revisar, lee el código y el diff y corre los tests que respaldan una objeción, y los entrevistadores escriben únicamente el glosario y los ADRs.

**Validador**:
Rol que revisa a un pensador aportando una fuente que el pensador no tuvo.
_Avoid_: revisor

**Par**:
El rol que devuelve y el rol que recibe la devolución. Las devoluciones se cuentan por par; cuando hay subtarea, por subtarea.

**Tech Lead efímero**:
Segundo Tech Lead que existe solo durante la entrevista técnica, con modelo más capaz y prompt de adversario. Entrevista a Tech Lead; no decide. No es un rol de la lista: es el entrevistador del pingpong.

**Developer integrador**:
El Developer que Tech Lead designa para unir las subtareas en la rama de tarea. Cuando la tarea tiene una sola subtarea, no hay integración: la rama de esa subtarea es la rama de tarea.

**Rol proactivo**:
Rol con reloj propio que genera tareas o insumos sin que nadie se lo pida.

### Trabajo

**Tarea**:
Unidad de trabajo que entra al sistema. Siempre de un tipo: bug, feature o platform enhancement.
_Avoid_: ticket, issue (el issue es la bandeja, no la tarea)

**Subtarea**:
Parte de una tarea que Tech Lead asigna a un Developer.

**Camino**:
La secuencia de roles que recorre una tarea según su tipo, solo de ida. Adónde vuelve una devolución lo decide el motivo, no el camino.
_Avoid_: flujo, pipeline

**Etapa de definición**:
Tramo del camino de feature entre Product Analyst y la definición única. Estrictamente secuencial.

**Definición única**:
El texto de PM que gobierna el resto de la tarea: comportamiento, flujo, diseño y textos.
_Avoid_: spec, requerimientos

**Flag**:
Marca de alcance sobre una tarea, como "toca UI" o "toca textos", tomada del vocabulario de flags del producto. Los propone Product Analyst y los valida PM; el set validado reemplaza al propuesto.

**Pieza integrada**:
El código de todas las subtareas unido por el Developer integrador en la rama de tarea y revisado por Tech Lead.

**Rama de tarea**:
La rama del producto donde termina el código de una tarea, una por tarea. Cada subtarea trabaja en su propia rama, aislada de las otras, y el integrador las une acá.

**Corrida**:
Una ejecución del sistema sobre una tarea, desde que entra hasta el cierre o el escalado.
_Avoid_: run, ejecución

**Corrida de rol**:
Una ejecución de un rol dentro de una corrida. Una tarea tiene tantas como roles visitó,
contando repeticiones.

### Interacciones

**Consulta**:
Pregunta de un rol al dueño de una decisión, respondida dentro de la misma corrida, sin entregar la tarea. No es devolución ni escalado y no cuenta para ningún límite. Hoy: el Tech Lead consulta a PM durante la entrevista de solución lo que la definición no dice; PM responde en la misma corrida y su respuesta aclara la definición.
_Avoid_: pregunta para el humano (eso es lo que el dueño tampoco puede decidir)

**Entrevista**:
Rondas de preguntas de un rol a otro para afinar una decisión antes de escribirla: el entrevistador pregunta y recomienda, el entrevistado decide. Hay dos: Product Analyst entrevista a PM sobre la definición, y el Tech Lead efímero entrevista a Tech Lead sobre la solución. Tiene tope de rondas; la transcripción entera queda en el documento de tarea. Lo que el entrevistado no puede decidir va a una consulta si la entrevista tiene consultado, y si no, al humano como pregunta.
_Avoid_: pingpong, consulta (la consulta es una pregunta suelta; la entrevista es la forma del trabajo)

**Pregunta para el humano**:
Lo que el dueño de una decisión marca como fuera de su alcance: ni el issue ni el conocimiento de producto (definiciones, glosario, ADRs) lo deciden, y sin la respuesta la definición cambia. Lo que un ADR decide nunca es pregunta para el humano; lo que no cambia la definición tampoco, es un supuesto. En la definición la marca PM entrevistado; en la solución, PM consultado. El Tech Lead nunca pregunta al humano. Se plantea una sola vez: nadie la responde durante la corrida, así que lo ya planteado no se repite en rondas siguientes ni el Tech Lead lo vuelve a consultar. Se retira cuando una ronda posterior la resuelve: el dueño la responde y la saca de la lista. Escala la tarea con las preguntas que siguen abiertas al terminar la entrevista.
_Avoid_: duda (una duda que PM resuelve con un supuesto no es pregunta)

**Supuesto**:
Decisión que el dueño toma sobre lo que ni el pedido ni el conocimiento resuelven pero no cambia la definición, y deja escrita en su respuesta. No va al humano.
_Avoid_: pregunta para el humano

**Revisión**:
Pase de Tech Lead sobre la entrega de una subtarea o de la pieza integrada, con hallazgos y un veredicto que se deriva de ellos: aprobada o devuelta. Una subtarea tiene tantas revisiones como pases recibió.
_Avoid_: review, code review (el nombre de la skill, no del pase)

**Revisión incremental**:
Revisión que arranca del head revisado en el pase anterior, no de la base de la subtarea, y toma la devolución anterior como lo que hay que verificar. Toda revisión después de una devolución es incremental.

**Head revisado**:
El commit hasta donde llegó la última revisión de una subtarea.

**Devolución**:
Rechazo con entrega hacia atrás. Cuenta para el límite del par.
_Avoid_: rechazo (salvo para el acto puntual de QA), rebote

**Motivo de devolución**:
Por qué se devuelve: de comportamiento o técnico. Decide quién recibe una devolución de QA: PM si es de comportamiento, el Developer de la subtarea si es técnico, Tech Lead cuando el camino no tiene PM.

**Categoría de devolución**:
El motivo de una devolución como dato, de una lista cerrada: violación de un estándar (arquitectura, tipado silenciado, costuras, otro), brecha contra la spec (comportamiento, test faltante, alcance), entrega que no es entrega (sin commitear, suite en rojo), calibración del plan o problema que cruza subtareas. Las devoluciones de PM a Product Analyst llevan la categoría de definición. Es lo que la medición cuenta y lo que hace computable "el mismo motivo entre los mismos roles". Distinta del motivo en texto, que sigue siendo para quien recibe la devolución.
_Avoid_: tipo de devolución, motivo (para la categoría)

**Hallazgo**:
Lo que Tech Lead encuentra al revisar, con su lugar, la afirmación, la evidencia (el comando que corrió y su salida, o la cita del estándar o del brief), la severidad, el alcance y la categoría. Solo un hallazgo bloqueante sobre la subtarea la devuelve; el veredicto se deriva de los hallazgos. Una objeción de comportamiento que no se reprodujo es una observación.
_Avoid_: comentario, issue

**Devolución interna**:
Devolución que se resuelve dentro del par sin pasar por PjM. Se reporta a PjM igual.

**Límite de devoluciones**:
Máximo de devoluciones por par antes de escalar. Hoy 2.

**Escalado**:
Lo que un rol, un par o el tope dejan dicho cuando la tarea no puede seguir sin el humano: el motivo de escalado, y las preguntas si las hay. El sistema lo entrega al humano en el issue y pausa la tarea.

**Motivo de escalado**:
Por qué la tarea no puede seguir sin el humano, de una lista cerrada: PjM no puede clasificarla o su camino no existe, un par llegó a su límite, un rol falló dos veces, se alcanzó el tope de tarea, o quedaron preguntas para el humano. Decide qué hace falta para reanudar.
_Avoid_: causa, tipo de escalado

**Pausa**:
Una tarea escalada queda esperando al humano con su estado guardado, sin gastar. Se retoma desde el mismo punto.
_Avoid_: bloqueo, espera

**Decisión del humano**:
Lo que el humano responde a un escalado: todo lo que comenta en el issue después del último aviso del sistema. Entra al documento de tarea, la lee todo rol y no se vuelve a preguntar: vale para toda la tarea y pesa sobre lo que contradiga.
_Avoid_: respuesta, comentario

**Reanudación**:
La tarea sigue desde donde se pausó. Hace falta una decisión del humano cuando hay preguntas abiertas o un par llegó a su límite, y con ella el contador de ese par vuelve a cero; un tope alcanzado o un fallo de rol se reanudan sin decisión. Nada se reanuda bajo un tope ya gastado. Si el estado guardado se perdió, se reconstruye desde el documento de tarea, cruzado con git.
_Avoid_: reintento (eso es tras un fallo de rol), relanzar (eso es empezar de cero)

**Fallo de rol**:
El rol se rompe: su ejecución falla, se le acaban los turnos sin resultado, la salida estructurada falta o es inválida, el `markdown` está vacío. Distinto de devolución; no cuenta para el límite.
_Avoid_: error (demasiado amplio)

**Reintento**:
Nueva corrida de un rol tras un fallo de rol, con el error como entrada. Continúa la conversación del intento fallido cuando la hay. Distinto de devolución.

**Tope por intento**:
Gasto máximo de un intento de cualquier rol. Excederlo es un fallo de rol. Red de seguridad contra un rol desbocado, no presupuesto de trabajo. Hoy 10 USD.
_Avoid_: presupuesto (eso sería lo que se espera gastar)

**Tope de tarea**:
Gasto acumulado de una tarea a partir del cual el sistema escala y pausa en vez de seguir. Crece con el plan: una base hasta que hay plan, después la base más una parte por subtarea, integración incluida, con un techo que limita esa cuenta. Se mira antes de cada intento de cualquier rol. Subirlo al reanudar es decisión del humano, que puede pasar el techo y nunca lo baja. No es fallo de rol ni devolución y no cuenta para ningún límite. Como el tope por intento, es red de seguridad, bien arriba de lo que cuesta una tarea sana. Hoy 30 USD de base, 15 por subtarea, techo 250.
_Avoid_: presupuesto de tarea

**Handoff**:
El bloque "Para el siguiente rol" con que cierra cada sección.

**Cierre**:
PjM da por terminada la tarea: aprobada, con las aprobaciones del tipo, o descartada, cuando PM dictamina que el comportamiento es el esperado.

**Listo para revisión**:
La tarea tiene su pieza integrada en un pull request del producto y espera al humano. Mientras el camino no tenga QA, el merge del humano sin tocar el diff es el cierre, y es lo único que puntúa en el set de evaluación.
_Avoid_: terminada, cerrada (todavía no lo está)

### Artefactos

**Documento de tarea**:
Un archivo por tarea con frontmatter YAML de PjM y una sección Markdown por rol. El registro
crudo del trabajo de la tarea, materia prima del conocimiento de producto: guarda también las
entregas que una devolución pisó y las corridas de cada rol. Nadie lee tareas viejas para
saber qué está vigente; para eso está el conocimiento de producto.

**Sección**:
La parte del documento que escribe un rol. Cada rol declara qué secciones lee.

**Bandeja**:
Los issues de GitHub del repo del producto con la etiqueta `agentic`. La entrada, no el documento.

**Cola de escalados**:
Los issues con la etiqueta `needs-human`.

**Pull request de tarea**:
El pull request del producto que abre el sistema desde la rama de tarea, con la definición única y el plan de Tech Lead en el cuerpo. Es la entrega al humano; el issue recibe el link al pull request y al documento de tarea. Nada más se escribe en el issue: los roles se hablan por el documento.

**Override de prompt**:
Un prompt de rol que vive en el repo del producto y pisa el default del sistema.

### Repos

**Sistema**:
Este repo: roles, prompts, caminos y las reglas del proceso.

**Producto**:
El repo sobre el que trabaja el sistema. El sistema nunca escribe en el repo del sistema mientras corre.

**Contexto de producto**:
La raíz del repo del producto, que el sistema recibe como parámetro. Ahí está todo lo que varía por producto: el código, los ADRs y `.agentic/`.
