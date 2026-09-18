# Observabilidad

Dos herramientas a distinta altitud (ADR-0008), un solo collector:

```
Claude Code ──┐                      ┌──> Langfuse local     (todas las trazas, con prompts)
              ├──> collector :4318 ──┤
sistema ──────┘                      └──> Grafana Cloud      (trazas y métricas del sistema)
```

| Directorio | Qué es |
|---|---|
| `collector/` | La config del OpenTelemetry Collector: recibe todo, filtra por `service.name` y reparte. `probe.py` verifica que Langfuse pricea las llamadas de Claude Code |
| `langfuse/` | El `docker-compose` de Langfuse. También levanta el collector, porque comparte su red |
| `grafana/` | El dashboard del sistema y cómo conectar la instancia de Grafana Cloud |

## Qué sale a la nube

Solo lo que emite el sistema, identificado como `service.name: agentic-system`:

- Un span por tarea, uno por rol y, desde esta tarea, uno por **corrida de rol** (una
  entrada de `runs` en el documento), hijo del span de rol. Los tres siguen las
  convenciones semánticas GenAI de OpenTelemetry (ADR-0008, adenda): se llaman
  `invoke_agent <rol>`, con `gen_ai.operation.name=invoke_agent`, `gen_ai.agent.name`
  (`Role.name`) y `gen_ai.agent.id` (`Role.slug`). El span de rol es la agregación de la
  patch entera (puede cubrir varias corridas, una entrevista por ejemplo) y sigue
  llevando el modelo, la sesión y el hash de prompt de la *última* corrida, más
  `agentic.role.prompt_hash` como la **tupla** de cada hash que corrió (ya no
  last-writer-wins); el span de corrida lleva el modelo, la sesión, el costo y el hash
  de prompt de **esa** corrida sola, con su propio `start_time`/`end_time`
  (`RoleRun.started_at`/`.ended_at`). Lo que la convención no nombra queda bajo
  `agentic.`: costo, retrabajo, versión de la entrega, modelo, turnos, `session_id`,
  hash del prompt y categorías de devolución (`agentic.role.cost_usd`, `.rework`,
  `.version`, `.model`, `.turns`, `.session_id`, `.prompt_hash`, `.return_categories`).
  `product`, `role` y `role.outcome` quedan sin prefijo: ya son el mismo nombre en la
  métrica. El collector borra `task.title` antes de mandarlo.
- Las métricas del sistema (`agentic/core/measurement/CONTEXT.md`), listadas abajo. Son
  eventos: una muestra por hecho (tarea terminada, corrida de rol, corrida individual,
  fallo, muestra residual), exportada al cerrar (ADR-0008, adenda), nada se acumula
  entre corridas. Un `record()` nunca setea el mismo instrumento dos veces con el mismo
  juego de labels: donde varios hechos comparten labels el valor es su conteo
  (`agentic.role.failures`), y donde el valor no es aditivo el hecho se registra y se
  exporta solo (`Run._emit`, una vez por corrida en vez de una vez por nodo). Un span
  siempre cierra con sus atributos: si un nodo lanza fuera del manejo de fallo del caso
  de uso, el rol queda `role.outcome=failed` y la tarea `task.status=crashed`, un valor
  que solo existe en telemetría, nunca en el estado.

El `product` sale de lo que el producto declara en su `.agentic/stack.yaml`, no del nombre
del directorio ni del remoto de git: desde ADR-0017 hay dos productos, y un panel que los
suma compara un SaaS de gimnasios con un sistema agéntico (ADR-0008, adenda; issue #47).

Los spans nativos de Claude Code llevan prompts, respuestas y tool calls: se quedan en
Langfuse. Si algún día hace falta verlos en Grafana, se cambia el filtro del collector, no
el sistema.

## Métricas

Una fila por métrica. `system.version` y `prompts.fingerprint` son las dos únicas etiquetas
de cardinalidad no acotada (el SHA del checkout y el hash del set de prompts); toda otra
etiqueta es un enum, un `Role.slug` (17 valores) o la salida de una función `rules.*`
cerrada — nunca un id de tarea, un título, un id de sesión, una línea de comando o una ruta.

| Métrica | Labels | Valor | Unidad |
|---|---|---|---|
| `agentic.task.cost_usd` | `product`, `task.type`, `task.status`, `system.version`, `prompts.fingerprint` | Costo acumulado de la tarea | USD |
| `agentic.task.rework_usd` | ídem | Costo del retrabajo de la tarea | USD |
| `agentic.task.duration` | ídem | Tiempo de reloj de la tarea | s |
| `agentic.task.finished` | ídem | 1, una muestra por tarea que llegó a un fin | — |
| `agentic.task.returns` | ídem + `return.category` | Devoluciones de la tarea, una muestra por categoría (`none` en 0 si no hubo) | — |
| `agentic.role.cost_usd` | `product`, `role`, `model`, `rework`, `role.slug`, `phase`, `role.outcome`, `system.version` | Costo de una corrida de rol: una muestra por entrada de `runs` de la patch, más una muestra **residual** por lo que ninguna corrida contabiliza (un intento fallido sin entrega, un nodo que lanzó sin patch), en 0 si la patch está vacía. La suma de las muestras de una patch es su `cost_usd` | USD |
| `agentic.role.duration` | `product`, `role`, `role.outcome` | Tiempo de reloj del **nodo** (la patch entera, no la corrida) — sin cambios desde antes de esta tarea | s |
| `agentic.role.run_duration` | ídem que `agentic.role.cost_usd` | Tiempo de reloj de **una corrida**, dentro del cupo de concurrencia (`ended_at` − `started_at`); ausente en una corrida grabada antes de este campo | s |
| `agentic.role.queue_wait` | ídem | Segundos que esa corrida esperó un cupo de concurrencia, antes de empezar a correr; nunca incluido en `run_duration` | s |
| `agentic.role.failures` | `product`, `role`, `role.slug`, `phase` (si la falla tiene sección), `reason` | Intentos fallidos de una patch, una muestra por motivo con el conteo como valor | — |
| `agentic.role.failure_cost_usd` | ídem | Costo de los intentos fallidos de una patch, por motivo. **No es aditivo con `agentic.role.cost_usd`**: el mismo dólar ya cuenta una vez, dentro del costo de una corrida que reintentó o dentro de la muestra residual | USD |
| `agentic.role.tokens` | `product`, `role`, `model`, `rework`, `role.slug`, `phase`, `role.outcome` + `kind` | Tokens de **una corrida**, de `ResultMessage.usage`, una muestra por `kind` ∈ {input, output, cache_read, cache_write}; ausente en una corrida grabada antes de este campo o cuyo intento falló (un intento fallido no reporta tokens) | tokens |
| `agentic.role.calls` | ídem sin `kind` | Llamadas al modelo de esa corrida: una por `AssistantMessage` que el runner vio antes de la entrega — el SDK no distingue "llamada" de "turno" por sí solo, así que esta es la definición del sistema (`ClaudeSdkRunner.run_once`); ausente en una corrida grabada antes de este campo | — |
| `agentic.role.turns` | ídem | `num_turns` de esa corrida, tal como lo reporta el SDK; a diferencia de las cuatro anteriores, siempre presente (el campo ya existía antes de esta tarea) | — |
| `agentic.role.tool_calls` | ídem + `tool` | Bloques `tool_use` de esa corrida, una muestra por herramienta (Bash, Read, Edit, Write, Grep, Glob, Agent, Skill, StructuredOutput, `other` para cualquier otra incluidas las MCP); ausente en una corrida grabada antes de este campo | — |
| `agentic.role.subagent_cost_usd` | ídem | Lo que un sub-agente gastó de `cost_by_model` de esa corrida, todo modelo que no sea el del rol (ADR-0013). **No es aditivo con `agentic.role.cost_usd`**: ese dólar ya cuenta una vez, dentro del total de la corrida; ausente en una corrida grabada antes de este campo | USD |

Las cinco últimas comparten las labels de `agentic.role.cost_usd` **sin `system.version`**:
un panel de tokens agrupa por rol y modelo, nunca por el checkout que corrió (issue #24).
| `agentic.review.findings` | `product`, `severity`, `category`, `status`, `review.kind`, `system.version` | Hallazgos de una revisión: una muestra por celda (severidad, categoría, estado) con el conteo de hallazgos de esa revisión que caen ahí como valor. Nada si la revisión no dejó hallazgos | — |
| `agentic.presubmit.checks` | `product`, `role`, `role.slug`, `phase` (si la patch tiene una corrida con veredicto), `check`, `result`, `final` | Una muestra por chequeo de cada invocación de la puerta de entrega que llegó a correr los chequeos: 1 si el chequeo pasó (`result=green`) o falló (`result=red`). Una entrega puede ser rechazada y reintentar dentro del mismo turno; solo la última invocación es `final=true` | — |
| `agentic.presubmit.duration` | ídem | Cuánto tardó ese chequeo, en esa invocación | s |
| `agentic.invocation.cost_usd` | `product`, `invocation.kind`, `system.version` | Lo que **esa invocación** (`run` o `resume`) gastó ella sola, nunca el acumulado de la tarea | USD |
| `agentic.invocation.rework_usd` | ídem | Retrabajo de esa invocación sola | USD |
| `agentic.invocation.duration` | ídem | Tiempo de reloj de esa invocación | s |
| `agentic.invocation.returns` | ídem + `return.category` | Devoluciones que esa invocación registró, una muestra por categoría (ninguna si no hubo) | — |
| `agentic.task.invocations` | `product`, `invocation.kind`, `task.status`, `system.version` | 1, una muestra por invocación, con el estado en el que terminó (incluye `crashed`, que solo existe en telemetría) | — |
| `agentic.task.paused` | `product`, `escalation.class`, `system.version` | 1, una muestra por pausa | — |
| `agentic.task.human_decisions` | `product`, `invocation.kind`, `system.version` | 1, una muestra por decisión que el humano dio al reanudar | — |

`role.slug` es el `Role.slug` de la corrida (17 valores; `unknown` en una corrida grabada
antes de este campo). `phase` ∈ {definition, plan, development, review} (`rules.phase_of`,
derivado de la sección que la corrida escribió). `role.outcome` ∈ {delivered, returned,
escalated, failed}: el veredicto de la patch (`escalated`, `status`) se aplica solo a la
corrida contra la que se escribió (la última cuya sección es una clave de
`patch["sections"]`) y a la muestra residual cuando no hay ninguna; toda otra corrida de
una patch con varias (una entrevista) es `delivered`. `reason` ∈ {schema, check, max_turns,
limit, sdk, other}, decidido en el sitio donde se lanza `AttemptFailed`, nunca por parsear
el mensaje de error.

### `ResultMessage.usage` es por llamada, no acumulado de la sesión

Pregunta abierta de esta tarea (issue #24): si `usage` trae los tokens de esa sola
llamada a `query()` o el acumulado de toda la sesión de Claude Code, incluidas las
llamadas anteriores que reanudó. La respuesta importa porque `run_with_retry` reanuda
la sesión de un intento fallido en una llamada nueva, y una entrevista o una revisión
incremental pueden reanudar una sesión que abrió un nodo anterior: si `usage` fuera
acumulativo, sumar sus cifras tal cual contaría esos tokens más de una vez.

La prueba (`tests/llm/test_token_usage.py`, `uv run pytest -m llm`) abre una sesión,
lee su `usage`, la reanuda en una llamada `query()` nueva y lee el `usage` de esa
segunda llamada. Salida real, corrida el 2026-09-18:

```
TURN 1 usage: {"input_tokens": 2, "cache_creation_input_tokens": 1862, "cache_read_input_tokens": 0, "output_tokens": 5, ...}
TURN 1 total_cost_usd: 0.00845
TURN 1 num_turns: 1
TURN 2 usage: {"input_tokens": 2, "cache_creation_input_tokens": 63, "cache_read_input_tokens": 1862, "output_tokens": 4, ...}
TURN 2 total_cost_usd: 0.0006684
TURN 2 num_turns: 1
```

La segunda llamada no repite las cifras de la primera: `cache_read_input_tokens` de la
vuelta 2 (1862) es exactamente lo que la vuelta 1 creó en caché (`cache_creation_input_
tokens`: 1862), y el resto de la vuelta 2 es apenas el intercambio nuevo (2 tokens de
entrada, 4 de salida) — no la suma de las dos vueltas. **`usage` es por llamada a
`query()`, nunca acumulado de la sesión**: una llamada que reanuda una sesión, la haya
abierto este mismo nodo o uno anterior, reporta solo lo que ella gastó, así que
`agentic.role.tokens` lee `usage` tal cual, sin restar nada (`_usage`,
`ClaudeSdkRunner.run_once`; el test por payload fabricado que codifica esta regla está
en `tests/core/process/adapters/claude_sdk/test_runner.py`, incluido el caso sin línea
de base de una sesión abierta en un nodo anterior). Queda documentado, no corregido: la
primera vuelta de una sesión reanudada entre nodos paga de nuevo el `cache_creation`
de reconstruir el contexto en un proceso nuevo, y esa vuelta sale más cara en tokens
que una continuación dentro del mismo proceso — un costo real, ya reflejado en
`cost_usd`, que el desglose por `kind` no distingue de una sesión abierta de cero.
`review.kind` ∈ {first, incremental, integration} (`rules.review_kind`): `integration`
para la revisión de la pieza integrada, siempre, sea cual sea el pase; `first` para
cualquier otra subtarea hasta que una devolución la manda de vuelta, `incremental`
(ADR-0015) en todo pase después de esa. `category` es la lista cerrada de `FindingCategory`
(ADR-0016) y `status` el que la tabla de hallazgos le dio (`open`, `dismissed`; `fixed` es
derivado y no se emite, porque se sabe recién en un pase posterior). Ambas se escriben en
el propio patch de `review` — una clave de medición, como `subtask_id`: nunca viaja al
padre del fan-out ni se persiste en el documento.

`check` es el nombre del chequeo tal como lo declara `.agentic/stack.yaml` del producto
(`tests`, `lint`, `format` hoy; cerrado por el producto, no por el sistema). `final` marca
la última invocación de la puerta de entrega de una corrida de Developer: la que se
convirtió en la entrega, o la que el backstop dejó pasar igual, en rojo, agotados los
rechazos. Las invocaciones de una corrida viajan en el patch de `develop` bajo
`presubmit_checks`, otra clave de medición: nunca se persiste, y por eso una entrega
rechazada dos veces dentro del mismo turno deja tres juegos de muestras y no uno.
`role` y `role.slug` son los del nodo que corrió la puerta de entrega (Developer o
Integrator), igual que en `agentic.role.failures`; `phase` sale de la corrida contra la
que se escribió el veredicto de la patch (`target_run`, la misma fuente que la muestra
residual), y falta cuando la patch no tiene una — por ejemplo un fallo antes de que el
rol entregara nada.
## Métricas de entrevista

Una entrevista (glossary: Entrevista, ADR-0014) deja sus propios conteos, leídos de
`interview_stats`, la clave de la patch que `interview()` arma solo para medición (igual
que `subtask_id`: nunca se renderiza en el documento de tarea). Se emiten desde el nodo
que corrió la entrevista (`define` para la de definición, `plan` para la de solución),
junto con el resto de las muestras de `RoleRun.finish`; un nodo que no corrió ninguna
(una definición que vuelve de una devolución, `test_define_on_a_return_skips_the_interview`)
no deja ninguna de las tres.

| Métrica | Labels | Valor | Unidad |
|---|---|---|---|
| `agentic.interview.rounds` | `product`, `interview` | Ciclos de pregunta-respuesta que completó la entrevista (no cuenta la ronda de cierre, que no le pregunta nada al entrevistado) | — |
| `agentic.interview.questions` | `product`, `interview`, `to` | Preguntas de la entrevista por destino: una muestra por cada uno de {interviewee, pm, human} | — |
| `agentic.interview.invariants` | `product`, `interview`, `state` | Invariantes propuestos por el entrevistador (`state=proposed`), y los que el plan adoptó de esa misma patch, cuando la patch trae su propia clave `invariants` (`state=adopted`, solo en la entrevista de solución) | — |

`interview` ∈ {definition, solution} (`Interview.stage`, issue #28): la de definición la
corre Product Analyst con PM, la de solución el Tech Lead efímero con Tech Lead. `to` ∈
{interviewee, pm, human}: `pm` es la Consulta (issue #46 no la tiene, siempre 0 en el
camino platform); `human` es el neto ya con las devoluciones restadas, el mismo conteo que
`questions_for_human` al terminar la entrevista. `state` ∈ {proposed, adopted}: adoptado
sale del plan, nunca de la entrevista misma.
**Deltas por invocación y motivo de escalado (issue #24, subtarea `task-events`).**
`invocation.kind` ∈ {run, resume}. `escalation.class` ∈ {classification, returns, failure,
cap, questions, unmergeable, setup} — `TaskState.escalation_class`, escrito solo por
`rules.escalation` junto con `escalated` en cada uno de los siete sitios que escalan la
tarea, así que una patch nunca pone uno sin el otro; `unknown` en una pausa grabada antes
de que el campo existiera (`rules.pair_at_limit` y `rules.stopped_by_cap` siguen parseando
el texto para esos documentos). Las cuatro `agentic.invocation.*` y las tres
`agentic.task.*` de arriba se emiten una sola vez, desde `cli._invoke`, al cerrar la
invocación, contra el delta entre el estado con el que entró y el estado final
(`task.delta`) — nunca desde un nodo del grafo, desde `pause` (que se re-ejecuta entero en
cada resume) o desde un span de rol (una rama del fan-out no puede avisarle al humano una
sola vez). Las cuatro series `agentic.task.cost_usd`/`.rework_usd`/`.duration`/`.returns`
existentes siguen significando lo acumulado de la tarea, sin cambios; estas son la
invocación sola. **`langgraph dev` no deja ninguna de estas siete muestras**: corre el
grafo directamente, sin pasar por `cli._invoke`, así que solo las métricas de rol (las de
arriba) se ven ahí.

## Costo en Langfuse

Langfuse pricea cada llamada al modelo (`claude_code.llm_request`) a partir de sus tokens
y su tabla de precios; el collector traduce los nombres de Claude Code (`input_tokens`,
`cache_read_tokens`...) a `gen_ai.usage.*`, que es lo único que Langfuse lee. El sistema no
manda dólares: el costo de un rol o de una tarea en Langfuse es la suma de sus llamadas, y
el span de rol va marcado como `agent`. La cifra exacta por rol (`agentic.role.cost_usd`, la que
reporta el SDK) sigue en el documento de tarea y en Grafana; en Langfuse puede quedar algo
corta porque Claude Code emite algunas llamadas fuera de toda interacción y esas no cuelgan
de ningún rol (ADR-0008, adenda).

Para comprobar que el collector traduce, después de recrearlo:

```bash
uv run observability/collector/probe.py
```

Manda una llamada de prueba y espera a que Langfuse la pricee (hasta dos minutos). Falla
con el motivo si el collector sigue con la config vieja o Langfuse no está arriba.

## Levantar

```bash
cd observability/langfuse
docker compose up -d
```

Requiere en `observability/langfuse/.env` (no versionado): `LANGFUSE_OTEL_BASIC` y los
tres de Grafana Cloud (`grafana/README.md`). Sin ellos el collector no arranca.

En el `.env` de la raíz, las `OTEL_*` del `.env.example` apuntan al collector. El sistema
solo exporta si `OTEL_EXPORTER_OTLP_ENDPOINT` está seteada.
