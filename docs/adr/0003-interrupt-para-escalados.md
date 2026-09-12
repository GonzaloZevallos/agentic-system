---
status: accepted
date: 2026-09-12
---

# Escalados al humano con `interrupt()`, no terminando el turno del grafo

Cuando un par llega al límite de devoluciones, PjM escala al humano y la tarea queda pausada
hasta que responde, a veces un día después. Implementamos la pausa con `interrupt()` de
LangGraph y reanudamos con `Command(resume=decisión)`, sobre un checkpointer durable
(SqliteSaver en `.agentic/state.sqlite` del producto).

`open_deep_research`, que tomamos como referencia, hace lo contrario: termina el turno con
`Command(goto=END)` y confía en que el usuario vuelva sobre el mismo `thread_id`. Ese patrón
funciona porque su pausa está en un punto fijo al inicio del grafo. La nuestra puede caer en
cualquiera de cinco pares en el medio del camino. Terminar el turno obligaría a que cada nodo
sepa reanudarse; `interrupt()` guarda la posición exacta.

## Consecuencias

- El grafo se exporta sin compilar para `langgraph dev`; la CLI lo compila con SqliteSaver.
- `thread_id` es el id de la tarea.
- Alguien tiene que llamar `resume`: la CLI `agentic resume` lee el comentario del issue cuando
  la etiqueta `needs-human` ya no está. Un poller con reloj propio la reemplaza en la etapa 5.
