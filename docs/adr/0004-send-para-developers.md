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
