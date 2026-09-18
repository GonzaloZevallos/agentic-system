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
