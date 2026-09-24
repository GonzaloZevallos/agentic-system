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
del directorio ni del remoto de git. Lo que se mide es el sistema, no el producto: desde
ADR-0028 el eje de comparación es `system_baseline` (Scorecard, Experimentos y Diagnóstico),
nunca `system_version` de todo panel -- `system_version` sigue existiendo para trazar la
corrida y es un filtro (`$version`), no el eje. `product` es un filtro (default All) y una
covariable, la que muestra sobre qué mezcla de tareas corrió cada línea de base, para que una
que tomó tareas más grandes no parezca peor. Ningún panel agrupa por producto salvo el de
mezcla, en la fila Control (ADR-0008, adenda; issues #47 y #24). `agentic.role.duration` es
la única que se lee por rol: congelada en el grano del **nodo** (issue #24 la dejó así a
propósito, ADR-0008, 2026-09-18), la respuesta por corrida está en `agentic.role.run_duration`,
que sí lleva `system.version`, `system.baseline` y `arm`.

## Las cuatro filas del dashboard (ADR-0028)

1. **Scorecard**, eje `system_baseline` (una sola, la que elige `$baseline`): las cuatro
   figuras del glosario de Medición -- autonomía, fallos propios por tarea, costo por tarea
   terminada, retrabajo -- cada una con dos targets PromQL: el nivel bajo `$baseline` y la
   diferencia contra `$baseline_prev`, ambos lados `sum(...)` sin `by` para que resten sin
   join. Un panel más con el n: Corridas terminadas y entregas juzgadas bajo `$baseline`.
2. **Experimentos**: las mismas cuatro figuras agrupadas `by (arm)` dentro de `$baseline`.
3. **Diagnóstico**, eje `system_baseline`, `$version` como filtro adicional: los paneles de
   rol, fase, tokens, hallazgos, presubmit, entrevista y fallos, más minutos a merge, commits
   humanos por entrega, costo de fallo por motivo y turnos sobre el tope.
4. **Control**: la mezcla de tareas por producto y tipo (el único panel que agrupa por
   producto, eje `system_version`, nunca `system_baseline`: su pregunta es si una versión
   tomó tareas distintas, no cuál línea de base es mejor), versiones por línea de base y
   brazos por línea de base.

**Sin alerta hasta que una línea de base junte diez tareas terminadas** (ADR-0028, decisión
4): con una a cuatro tareas por línea de base una alerta dispara por ruido puro.

**Ningún literal de rango pasa los 32 días** (ADR-0008, primer addendum: el techo de
Grafana Cloud por consulta) y el rango por defecto del dashboard (`now-30d`) tampoco. Un
scorecard sólo es válido cuando la ventana elegida contiene las dos líneas de base de punta a
punta -- el panel del n es el tell: si `$baseline_prev` muestra cero terminadas, la ventana no
la alcanza.

**Dos líneas de base resuelven distinto, y sólo coinciden salvo un caso.** Las familias
`agentic.role.*`/`agentic.task.*` llevan la línea de base **de la corrida** (la que
`telemetry.system_baseline()` resolvió al momento de esa invocación); `agentic.delivery.*`
lleva la línea de base **del documento** (la que quedó escrita cuando `agentic close` juzgó
el pull request). Coinciden siempre salvo una tarea reanudada a través de un tag `bl-*` nuevo
o hacia otro brazo, donde la corrida ve una línea de base que el documento todavía no tiene.
`agentic eval compare` es la lectura donde no pueden discrepar: lee el documento entero, nunca
mezcla las dos fuentes a mitad de tarea.

Las dos tablas de Control (versiones y brazos por línea de base) **no filtran por `$baseline`
a propósito**: existen para mostrar `untagged` y todo lo demás a la vez, no la línea elegida.
Tampoco dejan pasar la muestra sin la etiqueta en absoluto (`system_baseline!=""`): esas son
anteriores a ADR-0030 y no deben aparecer como un segundo balde sin nombre junto a `untagged`,
que sí es un valor real que `system_baseline()` emite.

**Costo por entrega tiene una sola implementación, `agentic eval compare`, y PromQL no puede
tenerla**: `agentic.delivery.*` no lleva costo y `agentic.task.cost_usd` no lleva un label
`judged`; unir las dos por tarea pediría un id de tarea que ADR-0008 prohíbe en una etiqueta.
El panel de Scorecard mide **costo por tarea terminada** en cambio, que sí es una sola familia
dividida por sí misma.

**El motivo de fallo que el Scorecard excluye es `account_limit` y `upstream`, nunca
`reason!="limit"`**: ADR-0029 es el único enunciado normativo de esa exclusión
(`scorecard.EXTERNAL_FAILURE_REASONS`); `limit` es nuestro (el tope por intento) y se queda
adentro. No se restatea el razonamiento acá -- ADR-0029 lo lleva entero.

Los spans nativos de Claude Code llevan prompts, respuestas y tool calls: se quedan en
Langfuse. Si algún día hace falta verlos en Grafana, se cambia el filtro del collector, no
el sistema.

## Métricas

Una fila por métrica. `system.version` y `prompts.fingerprint` son las dos únicas etiquetas
de cardinalidad no acotada (el SHA del checkout y el hash del set de prompts); toda otra
etiqueta es un enum, un `Role.slug` (17 valores) o la salida de una función `rules.*`
cerrada — nunca un id de tarea, un título, un id de sesión, una línea de comando o una ruta.
`system.baseline` (`bl-YYYY-MM-DD` que el humano declara, o `untagged`, ADR-0028) y `arm`
(el producto cerrado de `review_mode` x `skip_ephemeral`, ADR-0030) acompañan a
`system.version` en toda familia de abajo que ya lo lista, cardinalidad acotada las dos.
`reviewer_model` no es una de las tres — es texto libre, no un enum — y por eso se queda en
el frontmatter de la tarea, nunca en `arm` (ADR-0030).

| Métrica | Labels | Valor | Unidad |
|---|---|---|---|
| `agentic.task.cost_usd` | `product`, `task.type`, `task.status`, `system.version`, `system.baseline`, `arm`, `prompts.fingerprint` | Costo acumulado de la tarea | USD |
| `agentic.task.rework_usd` | ídem | Costo del retrabajo de la tarea | USD |
| `agentic.task.duration` | ídem | Tiempo de reloj de la tarea | s |
| `agentic.task.finished` | ídem | 1, una muestra por tarea que llegó a un fin | — |
| `agentic.task.returns` | ídem + `return.category` | Devoluciones de la tarea, una muestra por categoría (`none` en 0 si no hubo) | — |
| `agentic.role.cost_usd` | `product`, `role`, `model`, `rework`, `role.slug`, `phase`, `role.outcome`, `system.version`, `system.baseline`, `arm` | Costo de una corrida de rol: una muestra por entrada de `runs` de la patch, más una muestra **residual** por lo que ninguna corrida contabiliza (un intento fallido sin entrega, un nodo que lanzó sin patch), en 0 si la patch está vacía. La suma de las muestras de una patch es su `cost_usd` | USD |
| `agentic.role.duration` | `product`, `role`, `role.outcome` | Tiempo de reloj del **nodo** (la patch entera, no la corrida) — sin cambios desde antes de esta tarea | s |
| `agentic.role.run_duration` | ídem que `agentic.role.cost_usd` | Tiempo de reloj de **una corrida**, dentro del cupo de concurrencia (`ended_at` − `started_at`); ausente en una corrida grabada antes de este campo | s |
| `agentic.role.queue_wait` | ídem | Segundos que esa corrida esperó un cupo de concurrencia, antes de empezar a correr; nunca incluido en `run_duration` | s |
| `agentic.role.failures` | `product`, `role`, `role.slug`, `phase` (si la falla tiene sección), `reason`, `system.version`, `system.baseline`, `arm` | Intentos fallidos de una patch, una muestra por motivo con el conteo como valor | — |
| `agentic.role.failure_cost_usd` | ídem | Costo de los intentos fallidos de una patch, por motivo. **No es aditivo con `agentic.role.cost_usd`**: el mismo dólar ya cuenta una vez, dentro del costo de una corrida que reintentó o dentro de la muestra residual | USD |
| `agentic.role.tokens` | `product`, `role`, `model`, `rework`, `role.slug`, `phase`, `role.outcome`, `system.version`, `system.baseline`, `arm` + `kind` | Tokens de **una corrida**, de `ResultMessage.usage`, una muestra por `kind` ∈ {input, output, cache_read, cache_write}; ausente en una corrida grabada antes de este campo o cuyo intento falló (un intento fallido no reporta tokens) | tokens |
| `agentic.role.calls` | ídem sin `kind` | Llamadas al modelo de esa corrida: una por `AssistantMessage` que el runner vio antes de la entrega — el SDK no distingue "llamada" de "turno" por sí solo, así que esta es la definición del sistema (`ClaudeSdkRunner.run_once`); ausente en una corrida grabada antes de este campo | — |
| `agentic.role.turns` | ídem | `num_turns` de esa corrida, tal como lo reporta el SDK; a diferencia de las cuatro anteriores, siempre presente (el campo ya existía antes de esta tarea) | — |
| `agentic.role.max_turns` | ídem que `agentic.role.turns` | El tope de turnos del rol al momento de esa corrida (`Role.max_turns`), con las mismas labels que `agentic.role.turns` para que un panel los grafique lado a lado; ausente en una corrida grabada antes de este campo | — |
| `agentic.role.tool_calls` | ídem + `tool` | Bloques `tool_use` de esa corrida, una muestra por herramienta (Bash, Read, Edit, Write, Grep, Glob, Agent, Skill, StructuredOutput, `other` para cualquier otra incluidas las MCP); ausente en una corrida grabada antes de este campo | — |
| `agentic.role.subagent_cost_usd` | ídem que `agentic.role.turns` | Lo que un sub-agente gastó de `cost_by_model` de esa corrida, todo modelo que no sea el del rol (ADR-0013). **No es aditivo con `agentic.role.cost_usd`**: ese dólar ya cuenta una vez, dentro del total de la corrida; ausente en una corrida grabada antes de este campo | USD |
| `agentic.role.cold_fallback` | `product`, `role`, `role.slug`, `phase`, `system.version`, `system.baseline`, `arm` | 1, una muestra por corrida (o intento fallido) que arrancó en frío porque `run_with_retry` descartó la sesión que el llamador pasaba y no había ninguna que reanudar (ADR-0004, sexta adenda). Sin `model`, `rework` ni `role.outcome`: nombra la decisión del sistema sobre el rol y la fase, nunca una causa (ADR-0008, adenda 2026-09-21) | — |

El panel `$ / 1k tokens de salida, por rol y modelo` divide el costo de las corridas de
un rol (`agentic.role.cost_usd`, intentos fallidos incluidos: esa métrica no separa la
muestra de retrabajo de la primaria de una corrida que es ella misma retrabajo) sobre
los tokens de salida de las corridas que los reportaron (`agentic.role.tokens{kind="output"}`);
la muestra residual (`model=""`) no tiene corrida propia y por lo tanto ningún token, y
queda fuera del denominador por eso. No es "dólar por token entregado": ninguna etiqueta
separa el dólar de un intento fallido del dólar de la corrida que sí entregó.
| `agentic.review.findings` | `product`, `severity`, `category`, `status`, `review.kind`, `system.version`, `system.baseline`, `arm` | Hallazgos de una revisión: una muestra por celda (severidad, categoría, estado) con el conteo de hallazgos de esa revisión que caen ahí como valor. Nada si la revisión no dejó hallazgos | — |
| `agentic.presubmit.checks` | `product`, `role`, `role.slug`, `phase`, `system.version`, `system.baseline`, `arm`, `check`, `result`, `final` | Una muestra por chequeo de cada invocación de la puerta de entrega que llegó a correr los chequeos: 1 si el chequeo pasó (`result=green`) o falló (`result=red`). Una entrega puede ser rechazada y reintentar dentro del mismo turno; solo la última invocación es `final=true` | — |
| `agentic.presubmit.duration` | ídem | Cuánto tardó ese chequeo, en esa invocación | s |
| `agentic.invocation.cost_usd` | `product`, `invocation.kind`, `system.version`, `system.baseline`, `arm` | Lo que **esa invocación** (`run` o `resume`) gastó ella sola, nunca el acumulado de la tarea | USD |
| `agentic.invocation.rework_usd` | ídem | Retrabajo de esa invocación sola | USD |
| `agentic.invocation.duration` | ídem | Tiempo de reloj de esa invocación | s |
| `agentic.invocation.returns` | ídem + `return.category` | Devoluciones que esa invocación registró, una muestra por categoría (ninguna si no hubo) | — |
| `agentic.task.invocations` | `product`, `invocation.kind`, `task.status`, `system.version`, `system.baseline`, `arm` | 1, una muestra por invocación, con el estado en el que terminó (incluye `crashed`, que solo existe en telemetría) | — |
| `agentic.task.paused` | `product`, `invocation.kind`, `escalation.class`, `system.version`, `system.baseline`, `arm` | 1, una muestra por pausa; una invocación que se cae ya pausada (crash de un resume) no la repite: solo cuenta cuando esa invocación causó la pausa, comparando `escalated`/`escalation_class` contra el estado con el que entró | — |
| `agentic.task.human_decisions` | `product`, `invocation.kind`, `system.version`, `system.baseline`, `arm` | 1, una muestra por decisión que el humano dio al reanudar | — |

`role.slug` es el `Role.slug` de la corrida (17 valores; `unknown` en una corrida grabada
antes de este campo). `phase` ∈ {definition, plan, development, review} (`rules.phase_of`,
derivado de la sección que la corrida escribió). `role.outcome` ∈ {delivered, returned,
escalated, failed}: el veredicto de la patch (`escalated`, `status`) se aplica solo a la
corrida contra la que se escribió (la última cuya sección es una clave de
`patch["sections"]`) y a la muestra residual cuando no hay ninguna; toda otra corrida de
una patch con varias (una entrevista) es `delivered`. `reason` ∈ {schema, check, max_turns,
limit, account_limit, upstream, sdk, other}, decidido en el sitio donde se lanza
`AttemptFailed`, nunca por parsear el mensaje de error; `account_limit` y `upstream` salen de
`sdk` para lo externo, sin renombrar nada (ADR-0029).

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
(`tests`, `lint`, `format` hoy; cerrado por el producto, no por el sistema). No es un
`enum` de este diff ni un `Role.slug`: es la clave de un mapa (`checks:`) que el
`preflight` de cada producto valida una vez al arrancar, así que su conjunto es cerrado
y chico (hoy 3 valores) pero vive en la configuración del producto, no en el código de
esta tarea — la misma tensión que el panel de presubmit hereda. Sigue sin ser texto
libre (ni id de tarea, ni sesión, ni ruta): un producto que agregara un chequeo lo hace
declarándolo en su `stack.yaml`, no escribiéndolo en una corrida. `final` marca
la última invocación de la puerta de entrega de una corrida de Developer: la que se
convirtió en la entrega, o la que el backstop dejó pasar igual, en rojo, agotados los
rechazos. Las invocaciones de una corrida viajan en el patch de `develop` bajo
`presubmit_checks`, otra clave de medición: nunca se persiste, y por eso una entrega
rechazada dos veces dentro del mismo turno deja tres juegos de muestras y no uno.
`role` y `role.slug` son los del nodo que corrió la puerta de entrega (Developer o
Integrator), igual que en `agentic.role.failures`; `phase` sale de la corrida contra la
que se escribió el veredicto de la patch (`target_run`, la misma fuente que la muestra
residual), nunca ausente (ADR-0008, 2026-09-18 segunda adenda): `unknown` cuando la
patch no tiene una — por ejemplo un fallo antes de que el rol entregara nada.

## Métricas emitidas y no leídas

`tests/readme_metrics._not_read()` deriva el partido contra `_documented()`: ningún conteo
se restatea a mano acá arriba. Cada línea dice por qué ningún panel la lee hoy.

- `agentic.role.duration`: congelada en el grano del nodo (ADR-0008, 2026-09-18); su panel
  lee `agentic.role.run_duration` + `agentic.role.queue_wait` a propósito, nunca esta.
- `agentic.role.calls`: llamadas al modelo por corrida; sin panel propio, sub-eje de
  `agentic.role.tokens`/`.turns` que sí tienen el suyo.
- `agentic.role.subagent_cost_usd`: no es aditiva con `agentic.role.cost_usd` (ya cuenta ahí);
  sin panel para no invitar a sumarla encima del costo total.
- `agentic.role.cold_fallback`: señal de arranque en frío por rol y fase, todavía sin un
  panel que la lea; ninguna decisión de este dashboard depende de ella aún.
- `agentic.presubmit.duration`: cuánto tardó cada chequeo; el panel de presubmit hoy
  sólo cuenta entregas rojas, no su duración.
- `agentic.interview.invariants`: propuestos vs. adoptados por entrevista; sin panel porque
  el scorecard y el diagnóstico de esta tarea no leen calidad de entrevista, sólo su volumen.
- `agentic.task.duration`: tiempo de reloj de la tarea entera; el dashboard mide costo
  y retrabajo, no duración de tarea.
- `agentic.task.returns`: devoluciones por categoría; sin panel, cubierta indirectamente por
  el retrabajo que esas devoluciones generan.
- `agentic.task.human_decisions`: decisiones del humano al reanudar; sin panel propio.
- `agentic.invocation.cost_usd`: detalle por invocación (`run`/`resume`) de una serie que el
  dashboard ya lee acumulada por tarea (`agentic.task.cost_usd`); sin panel para no duplicar
  la misma pregunta dos veces.
- `agentic.invocation.rework_usd`: ídem, detalle por invocación de `agentic.task.rework_usd`.
- `agentic.invocation.duration`: ídem, detalle por invocación de `agentic.task.duration`.
- `agentic.invocation.returns`: ídem, detalle por invocación de `agentic.task.returns`.

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
| `agentic.interview.rounds` | `product`, `interview`, `system.version`, `system.baseline`, `arm` | Ciclos de pregunta-respuesta que completó la entrevista (no cuenta la ronda de cierre, que no le pregunta nada al entrevistado) | — |
| `agentic.interview.questions` | ídem + `to` | Preguntas de la entrevista por destino: una muestra por cada uno de {interviewee, pm, human} | — |
| `agentic.interview.invariants` | ídem que `agentic.interview.rounds` + `state` | Invariantes propuestos por el entrevistador (`state=proposed`), y los que el plan adoptó de esa misma patch, cuando la patch trae su propia clave `invariants` (`state=adopted`, solo en la entrevista de solución) | — |

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

## Métricas de entrega

El veredicto del humano sobre el pull request de una tarea (glossary: Resultado de
entrega, `agentic/core/measurement/CONTEXT.md`), leído y emitido por `agentic close
--issue N`, nunca por un nodo del grafo ni por ningún revisor del sistema: no hay
span ni Corrida de la que colgarlo, así que `record_delivery` es su propia función,
no un `Run`, con el mismo `_EMIT_LOCK` que cualquier otro emisor pero un solo
record-and-flush para las cuatro. `close` lee el `pull_request` que el propio
documento de la tarea nombra con `gh`, nunca la rama que tenga `product_dir` en ese
momento.

| Métrica | Labels | Valor | Unidad |
|---|---|---|---|
| `agentic.delivery.outcome` | `product`, `system.version`, `system.baseline`, `arm`, `prompts.fingerprint`, `task.type`, `outcome` | 1, una muestra con `outcome` ∈ {merged, merged_with_changes, closed} | — |
| `agentic.delivery.human_commits` | `product`, `system.version`, `system.baseline`, `arm`, `prompts.fingerprint`, `task.type` | Commits del pull request que son del humano, no del sistema (por email de autor de git, nunca por login) | — |
| `agentic.delivery.human_lines_changed` | ídem | `additions` + `deletions` de esos commits humanos; 0 si el humano fusionó la entrega tal cual | — |
| `agentic.delivery.time_to_merge` | ídem | Segundos desde que se abrió el pull request (`createdAt`) hasta que se fusionó (`mergedAt`); ausente si nunca se fusionó, porque no hay nada que medir | s |

El timestamp de la muestra es el del **cierre**, nunca el de la entrega: una versión
que salió ayer muestra cero entregas mientras su `agentic.role.cost_usd` ya está
lleno, y una ventana cerrada cuenta los merges de esta semana contra versiones de
hace tres semanas — el humano decide cuándo fusiona, no el sistema. Un squash merge
con edición por la UI de GitHub no deja ningún commit propio en la rama y lee
`merged`: la serie subreporta `merged_with_changes` por construcción, no por un
error de conteo. Un click de "Update branch" cuyo `messageHeadline` escapa a
`MERGE_COMMIT_HEADLINE` infla `human_lines_changed` con todo lo que `main` sumó
desde la base, no con lo que el humano escribió a mano. Un documento escrito antes
de esta tarea no tiene `system_version` en su frontmatter; `close` normaliza eso —
ausente, vacío o nulo — al único literal `unknown`, que la variable `$version` del
dashboard no ofrece nunca (sale de `agentic_role_cost_usd`, que si existe ya trae una
versión real), así que esas muestras solo se ven bajo `All`.

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
