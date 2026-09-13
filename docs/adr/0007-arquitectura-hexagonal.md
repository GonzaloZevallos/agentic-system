---
status: accepted
date: 2026-09-13
---

# Arquitectura hexagonal: LangGraph y el SDK son adaptadores, los ports los define el sistema

El paquete `agentic/` se organiza en tres capas con una regla de dependencia estricta:
`domain/` (roles, estado, documento, conocimiento, reglas del proceso) no importa nada
del proyecto ni de la infraestructura; `application/` (ports y casos de uso, uno por nodo
del camino) importa solo el dominio; `adapters/` implementa los ports o dirige los casos
de uso, y solo `composition.py` y la CLI los conocen. Un test de arquitectura vigila la
regla sobre los imports.

Dos decisiones dentro de esta que no son obvias:

**LangGraph es un adaptador, no la capa de aplicación.** Los casos de uso son funciones
async puras que reciben `Configuration` y un contenedor `Ports`. El grafo solo cablea:
cada nodo arma la configuración desde su `config`, construye los ports y llama al caso de
uso dentro del span del rol. `interrupt()`, `Send`, los reducers y el checkpointer viven
ahí (ADR-0003, ADR-0004 siguen intactos). Con tres roles ya dolía testear reglas a través
del grafo; con diecisiete y fan-out de Developers cada regla probada así sería un test
lento y frágil. El escalado es un dato del patch que devuelve el caso de uso; pausar
sobre él es del adaptador, así que los casos de uso nunca saben que existe una pausa.

**Los ports los define lo que el sistema necesita, con una sola implementación.**
`RoleRunner`, `TaskDocuments`, `ProductKnowledge`, `Prompts`, `FlagVocabulary` e `Inbox`
tienen hoy una implementación cada uno, sobre el SDK o sobre `product_dir`. Esto no
contradice ADR-0002 ni README §8, que posponen el adaptador *de producto* hasta el
segundo producto: aquello es generalizar sobre qué varía entre productos; esto es separar
regla de disco. `product_dir` sigue siendo el único parámetro de la costura.

Otras consecuencias de forma:

- El port del LLM devuelve crudo (`RawResult`) o lanza `AttemptFailed`. La política de
  un reintento con el error (README §3), la validación del esquema y del vocabulario y el
  `check` del nodo son de la aplicación, así un harness nuevo no las copia.
- Los prompts `.md` viven en `domain/prompts/`: son la definición de conducta del rol,
  como `Role.reads`. El overlay del producto y el hash los resuelve el port `Prompts`.
- `Configuration` se construye con `from_sources(overrides, env)`; el adaptador de
  LangGraph le pasa `configurable`, la CLI nada. La precedencia no cambia.
- La observabilidad es transversal: el nodo abre el span, el adaptador del SDK propaga
  el traceparent, la aplicación no la ve. El `Printer` de `--verbose` vive con el SDK y el
  runner se construye con `verbose`; el port no lleva callbacks.
- Los tests espejan las capas: `tests/domain` puros, `tests/application` con fakes en
  memoria, `tests/adapters` con `tmp_path` y el grafo compilado, `tests/llm` como antes.

## Opciones descartadas

- **LangGraph como capa de aplicación** (los nodos son los casos de uso): la mitad del
  refactor, pero cada regla del proceso queda pegada a la mecánica del grafo.
- **Port `HumanGate.ask()`** para el escalado: envolver `interrupt()` funciona, pero mete la
  mecánica de LangGraph en la firma de cada caso de uso que pueda escalar.
- **`adapters/driving/` y `adapters/driven/`**: ceremonia con este tamaño; el nombre de la
  tecnología ya dice qué es cada adaptador.
- **Un caso de uso por `kind` (pensador / validador)** parametrizado por par: generalizar
  el validador con un solo par de ejemplo. QA Lead será el segundo par y ahí se extrae.

## Consecuencias

- Reemplaza el "Paquete" plano de README §14; `langgraph.json` exporta
  `agentic.composition:graph`.
- Cada rol nuevo agrega su `Role`, su prompt y su esquema al dominio y su caso de uso a la
  aplicación; el grafo solo suma un nodo.
- `composition.py` es el único módulo que importa todos los adaptadores, y exporta el
  grafo ya cableado con los ports reales para `langgraph dev`. Ningún adaptador lo importa.
