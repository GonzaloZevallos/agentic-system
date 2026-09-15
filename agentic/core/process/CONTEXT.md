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
Escribir archivos del producto y correr comandos. Solo Developer ejecuta; Tech Lead lee el código y el diff, y los entrevistadores escriben únicamente el glosario y los ADRs.

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
Lo que el dueño de una decisión marca como fuera de su alcance: ni el issue ni el conocimiento de producto lo deciden. En la definición la marca PM entrevistado; en la solución, PM consultado. El Tech Lead nunca pregunta al humano. Escala la tarea con la lista de preguntas al terminar la entrevista.

**Revisión**:
Pase de Tech Lead sobre la entrega de una subtarea o de la pieza integrada, con veredicto: aprobada o devuelta. Una subtarea tiene tantas revisiones como pases recibió.
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

**Devolución interna**:
Devolución que se resuelve dentro del par sin pasar por PjM. Se reporta a PjM igual.

**Límite de devoluciones**:
Máximo de devoluciones por par antes de escalar. Hoy 2.

**Escalado**:
Entrega de la tarea al humano por PjM, con historial y motivo. Pausa la tarea.

**Fallo de rol**:
El rol se rompe: su ejecución falla, se le acaban los turnos sin resultado, la salida estructurada falta o es inválida, el `markdown` está vacío. Distinto de devolución; no cuenta para el límite.
_Avoid_: error (demasiado amplio)

**Reintento**:
Nueva corrida de un rol tras un fallo de rol, con el error como entrada. Continúa la conversación del intento fallido cuando la hay. Distinto de devolución.

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
