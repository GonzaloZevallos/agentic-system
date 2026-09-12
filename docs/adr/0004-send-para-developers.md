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
