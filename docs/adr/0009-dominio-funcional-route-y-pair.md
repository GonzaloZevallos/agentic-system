---
status: accepted
date: 2026-09-13
---

# El dominio crece en datos y funciones: `Route` como dato, `Pair` como valor, sin agregado con métodos

ADR-0007 dejó las capas; esta decisión fija cómo crece `domain/` cuando entren los doce
roles que faltan sin reescribirlo. El glosario ya nombra Camino, Par, Escalado y Cierre;
el código los tenía como strings sueltos (`"PM→Product Analyst"`, `escalated: str`) y
como funciones de ruteo a mano, una por nodo (`after_classify`, `after_define`,
`after_validate`), con un `Status` que suma estados por etapa (`defined`,
`definition_ready`). Con tres roles alcanza; con diecisiete es una función y un estado
nuevo por rol, y el camino queda repartido entre el grafo y las reglas.

Cuatro decisiones, y cuál entra ahora:

**El agregado Tarea es `TaskState` más `rules.py`, no una clase.** El DDD de libro
pide `Task.devolver()`, `Task.escalar()`. Acá los casos de uso devuelven un patch parcial
que LangGraph funde con reducers (ADR-0007); un agregado mutable no tiene dónde vivir en
ese modelo. Las invariantes son funciones puras sobre el estado, cada una un test unitario.
Se escribe para que nadie lo "arregle" a clase.

**El Camino es un dato, `Route`, y es solo la ida.** Pasos ordenados por tipo de tarea,
cada uno con su condición por flag (`[si toca UI]`) y sus salidas tempranas (el bug de
"comportamiento esperado" cierra descartado). Las devoluciones van en una tabla aparte,
`(quien devuelve, motivo) → destino`, porque el destino depende del motivo y no del par:
QA devuelve a PM si es de comportamiento y al Developer de esa subtarea si es técnico
(README §3). El tramo técnico es un paso que por dentro es un sub-route, que es lo que el
subgrafo de ADR-0004 ya separa. Espeja cómo el README lo cuenta: §2 es la ida, §3 las
vueltas. El ruteo pasa a ser una función, `next_step(state, route)`, y `Status` baja a
cinco valores independientes del camino (`open`, `in_progress`, `returned`,
`waiting_on_human`, `closed`) más `outcome` (`approved`, `discarded`); la posición se lee
de `current_role`. **Se modela ahora y se implementa cuando entre QA Lead**: hoy hay un
solo camino, y generalizar desde un ejemplo es lo que ADR-0007 evitó al posponer el caso
de uso por `kind` hasta el segundo par. QA Lead trae ese segundo par, y con QA Funcional,
la primera devolución cuyo destino depende del motivo.

**El Par es un value object, `Pair`, y entra ahora.** `by`, `to` y `subtask` opcional;
la clave persistida es `"A→B"` o `"A→B:subtarea"`. El par Tech Lead→Developer con
subtarea llega en la misma etapa 1, y un string formateado en tres lugares es el bug que
un tipo evita. Redefine **Par** en el glosario: era "un validador y su pensador", pero
QA→PM y QA→Tech Lead cuentan para el límite y ni PM ni Tech Lead son pensadores. Ahora
es quien devuelve y quien recibe, y Pensador y Validador dejan de definirlo.

**Lenguaje ubicuo con código en inglés.** El glosario está en castellano y el código en
inglés; la regla es que cada término tenga exactamente un nombre en `agentic.domain`, y el
mapeo vive en `tests/domain/test_language.py`, que además exige que cada término mapeado
exista en `CONTEXT.md`. `CONTEXT.md` no lleva identificadores. Camino se llama `Route`
porque `Path` es `pathlib.Path`, que el dominio ya importa.

## Opciones descartadas

- **Clase `Task` con métodos**: choca con el patch model de ADR-0007; habría que
  serializar el agregado a patch en cada caso de uso.
- **Una sola estructura que codifique ida, vueltas, consultas y fan-out**: es un
  mini-motor de workflow, que es justamente lo que ADR-0007 sacó del grafo para no tener.
- **Eventos de dominio**: `runs` y `failures` en el estado más los spans de OTel
  (ADR-0008) ya son el registro; eventos lo duplican.
- **Implementar `Route` ahora**: diseño desde un solo ejemplo. Se retoma con QA Lead.
- **Dividir `domain/` en subdominios** (proceso, conocimiento de producto, medición):
  716 líneas en ocho módulos no lo justifican; se revisa cuando el `domain/` único duela.
- **Identificadores en `CONTEXT.md`**: el glosario es lenguaje, no implementación; el
  test es el lugar del mapeo.
- **`Escalado` como tipo ahora**: sigue siendo un string con motivo; cambia cuando
  lleve historial.

## Consecuencias

- `CONTEXT.md`: Par y Cierre redefinidos (Cierre es aprobado o descartado; el bug de
  "comportamiento esperado" cerraba sin aprobación y el glosario decía que siempre las
  requería), Camino aclarado como la ida, entra **Motivo de devolución**.
- Cada rol nuevo suma su paso a `Route` cuando `Route` exista, y su término al test de
  lenguaje si entra al código.
- README §12 registra `Route` y `Status` chico como pendiente de etapa 2; §13 anota las
  dos definiciones reemplazadas.

## Adenda (2026-09-16): el Camino se llama `Workflow`, no `Route`

`Route` nunca se implementó (README §12, pendiente 16: hoy el ruteo es una función por
nodo en `rules.py`), así que el rename cuesta esta edición y no una migración. El motivo
es que ni `route` ni `pipeline` son términos de arte en ningún framework: el dominante es
**`workflow`** (Microsoft Agent Framework, OpenAI, Google ADK, Temporal, CrewAI Flows) y
el segundo es `graph`, con `node` y `edge` (LangGraph, ADK v2, AgentKit).

Gana `Workflow` sobre `Graph` por una razón de capas y no de gusto: `graph` ya está
ocupado acá abajo. El `StateGraph` de LangGraph es el adaptador (ADR-0007), y llamar
`Graph` al dato del dominio haría que "el grafo" signifique dos cosas a un archivo de
distancia. `Workflow` nombra la secuencia de roles por tipo de tarea, que es lo que el
dato es, sin chocar con nada.

El razonamiento original vale igual con el nombre nuevo: sigue siendo solo la ida, las
devoluciones siguen en su tabla aparte, y sigue sin implementarse hasta que entre QA Lead.
La entrada **Camino** del glosario suma `_Campo_: workflow`.

El título de este ADR y su nombre de archivo quedan como estaban: son el registro de una
decisión fechada, y lo que se lee hoy es esta adenda.
