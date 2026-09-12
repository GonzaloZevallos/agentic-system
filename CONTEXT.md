# Sistema agéntico de desarrollo

Un equipo de desarrollo modelado como 17 roles agénticos más un humano, que mantiene y hace
crecer un producto que vive en otro repo. Este glosario fija el vocabulario; el proceso está
en DISENO.md y las decisiones con trade-off en `docs/adr/`.

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
Dueño de la definición única y única fuente de verdad de producto.

**Pensador**:
Rol que produce: Product Analyst, UX Designer, UI Designer, Developer, Tech Writer.

**Validador**:
Rol que revisa a un pensador aportando una fuente que el pensador no tuvo.
_Avoid_: revisor

**Par**:
Un validador y su pensador. Las devoluciones se cuentan por par.

**Tech Lead efímero**:
Segundo Tech Lead que existe solo durante el pingpong, con modelo más capaz y prompt de adversario.

**Rol proactivo**:
Rol con reloj propio que genera tareas o insumos sin que nadie se lo pida.

### Trabajo

**Tarea**:
Unidad de trabajo que entra al sistema. Siempre de un tipo: bug, feature o platform enhancement.
_Avoid_: ticket, issue (el issue es la bandeja, no la tarea)

**Subtarea**:
Parte de una tarea que Tech Lead asigna a un Developer.

**Camino**:
La secuencia de roles que recorre una tarea según su tipo.
_Avoid_: flujo, pipeline

**Etapa de definición**:
Tramo del camino de feature entre Product Analyst y la definición única. Estrictamente secuencial.

**Definición única**:
El texto de PM que gobierna el resto de la tarea: comportamiento, flujo, diseño y textos.
_Avoid_: spec, requerimientos

**Flag**:
Marca de alcance sobre una tarea, como "toca UI" o "toca textos". Los propone Product Analyst y los valida PM.

**Pieza integrada**:
El código de todas las subtareas unido por el Developer integrador y revisado por Tech Lead.

### Interacciones

**Consulta**:
Pregunta sin entregar la tarea. No cuenta para el límite.

**Devolución**:
Rechazo con entrega hacia atrás. Cuenta para el límite del par.
_Avoid_: rechazo (salvo para el acto puntual de QA), rebote

**Devolución interna**:
Devolución que se resuelve dentro del par sin pasar por PjM. Se reporta a PjM igual.

**Límite de devoluciones**:
Máximo de devoluciones por par antes de escalar. Hoy 2.

**Escalado**:
Entrega de la tarea al humano por PjM, con historial y motivo. Pausa la tarea.

**Fallo de rol**:
El rol se rompe: excepción del harness, turnos agotados sin resultado, salida estructurada ausente o inválida, texto vacío. Distinto de devolución; no cuenta para el límite.
_Avoid_: error (demasiado amplio)

**Handoff**:
El bloque "Para el siguiente rol" con que cierra cada sección.

**Cierre**:
PjM da por terminada la tarea. Requiere las aprobaciones del tipo.

### Artefactos

**Documento de tarea**:
Un archivo por tarea con frontmatter YAML de PjM y una sección Markdown por rol. Fuente de verdad del trabajo de la tarea.
_Avoid_: log, historial

**Sección**:
La parte del documento que escribe un rol. Cada rol declara qué secciones lee.

**Bandeja**:
Los issues de GitHub del repo del producto con la etiqueta `agentic`. La entrada, no el documento.

**Cola de escalados**:
Los issues con la etiqueta `needs-human`.

**Fuente de verdad de producto**:
Las definiciones acumuladas que mantiene PM.

**Contexto de producto**:
El directorio que el sistema recibe como parámetro con todo lo que varía por producto.

**Override de prompt**:
Un prompt de rol que vive en el repo del producto y pisa el default del sistema.

**Set de evaluación**:
Las cinco tareas fijas que se re-corren idénticas contra cada versión de prompts.

### Repos

**Sistema**:
Este repo: roles, prompts, caminos, grafo.

**Producto**:
El repo sobre el que trabaja el sistema. El sistema nunca escribe en el repo del sistema mientras corre.
