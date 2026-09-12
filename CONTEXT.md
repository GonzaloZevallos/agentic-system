# Glosario del sistema agéntico

Vocabulario canónico. Cuando un término tiene sinónimos que evitamos, se anotan.
Sin detalles de implementación: eso vive en DISENO.md y en el código.

## Actores

- **Rol**: un agente con responsabilidad fija dentro del proceso. Hay 17. Evitar "agente" a secas cuando se habla de uno concreto.
- **Humano**: aprobador y destino de los escalados. Revisa en lote, una vez por día.
- **PjM** (Project Manager): el rol que clasifica, enruta, registra, controla loops y cierra. Ex "Lead"; no usar "Lead" ni "orquestador".
- **PM** (Product Manager): dueño de la definición única y única fuente de verdad de producto.
- **Pensador**: rol que produce (Product Analyst, UX Designer, UI Designer, Developer, Tech Writer).
- **Validador**: rol que revisa a un pensador y trae una fuente que el pensador no tuvo (PM, Data Analyst, UX Lead, Design Lead, Tech Lead, los QA).
- **Par**: un validador y su pensador. Las devoluciones se cuentan por par.
- **Tech Lead efímero**: segundo Tech Lead que existe solo durante el pingpong, con modelo más capaz y prompt de adversario.
- **Rol proactivo**: rol con reloj propio que genera tareas o insumos sin que nadie se lo pida (SRE/Ops, Tech Lead proactivo, PM proactivo, Data Analyst, Customer Insights).

## Trabajo

- **Tarea**: unidad de trabajo que entra al sistema. Siempre de un tipo: **bug**, **feature** o **platform enhancement**.
- **Subtarea**: parte de una tarea que Tech Lead asigna a un Developer.
- **Camino**: la secuencia de roles que recorre una tarea según su tipo.
- **Etapa de definición**: tramo del camino de feature entre Product Analyst y la definición única. Estrictamente secuencial.
- **Definición única** (o definición consolidada): el texto de PM que gobierna el resto de la tarea. Incluye comportamiento, flujo, diseño y textos.
- **Flag**: marca de alcance sobre una tarea ("toca UI", "toca textos"). Los propone Product Analyst, los valida PM. No es una lista fija.
- **Pieza integrada**: el código de todas las subtareas unido por el Developer integrador y revisado por Tech Lead.

## Interacciones

- **Consulta**: pregunta sin entregar la tarea. No cuenta para el límite.
- **Devolución**: rechazo con entrega hacia atrás. Cuenta para el límite del par. Evitar "rechazo" salvo para la acción puntual de QA.
- **Devolución interna**: devolución que se resuelve dentro del par sin pasar por PjM. Se reporta a PjM igual.
- **Límite de devoluciones**: máximo de devoluciones por par antes de escalar. Hoy 2.
- **Escalado**: entrega de la tarea al humano por PjM, con historial y motivo. Pausa la tarea.
- **Fallo de rol**: el rol se rompe (salida inválida, vacía, o sin turnos). Distinto de devolución. No cuenta para el límite.
- **Handoff** ("Para el siguiente rol"): bloque fijo con que cierra cada sección, escrito para quien sigue.
- **Cierre**: PjM da por terminada la tarea. Requiere las aprobaciones del tipo.

## Artefactos

- **Documento de tarea** (documento único): un archivo por tarea con frontmatter YAML de PjM y una sección Markdown por rol. Fuente de verdad del trabajo de la tarea.
- **Sección**: la parte del documento que escribe un rol. Cada rol declara qué secciones lee.
- **Bandeja**: los issues de GitHub del repo del producto con la etiqueta `agentic`. Es la entrada, no el documento.
- **Cola de escalados**: los issues con la etiqueta `needs-human`.
- **Fuente de verdad de producto**: las definiciones acumuladas que mantiene PM.
- **Contexto de producto**: el directorio que el sistema recibe como parámetro y que contiene todo lo que varía por producto.
- **Set de evaluación**: las cinco tareas fijas que se re-corren contra cada versión de prompts.

## Repos

- **Sistema**: este repo. Roles, prompts, caminos, grafo.
- **Producto**: el repo sobre el que trabaja el sistema. El sistema nunca escribe en el repo del sistema mientras corre.
