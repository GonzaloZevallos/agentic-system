# Observabilidad

Un solo destino desde ADR-0037, un solo collector:

```
Claude Code ──┐
              ├──> collector :4318 ──> Langfuse local (todas las trazas, con prompts)
sistema ──────┘
```

| Directorio | Qué es |
|---|---|
| `collector/` | La config del OpenTelemetry Collector: recibe todo, filtra por `service.name` y lo manda a Langfuse. `probe.py` verifica que Langfuse pricea las llamadas de Claude Code |
| `langfuse/` | El `docker-compose` de Langfuse. También levanta el collector, porque comparte su red |

## Qué sale a la nube

Solo lo que emite el sistema, identificado como `service.name: agentic-system`:

- Un span por tarea, uno por rol y uno por **corrida de rol** (una entrada de `runs` en
  el documento), hijo del span de rol. Los tres siguen las convenciones semánticas GenAI
  de OpenTelemetry (ADR-0008, adenda): se llaman `invoke_agent <rol>`, con
  `gen_ai.operation.name=invoke_agent`, `gen_ai.agent.name` (`Role.name`) y
  `gen_ai.agent.id` (`Role.slug`). El span de rol es la agregación de la patch entera
  (puede cubrir varias corridas, una entrevista por ejemplo) y lleva el modelo, la
  sesión y el hash de prompt de la *última* corrida, más `agentic.role.prompt_hash` como
  la **tupla** de cada hash que corrió; el span de corrida lleva el modelo, la sesión, el
  costo y el hash de prompt de **esa** corrida sola, con su propio `start_time`/`end_time`
  (`RoleRun.started_at`/`.ended_at`). El span de tarea lleva el número de issue (`task.id`)
  desde ADR-0037: la restricción de cardinalidad que lo prohibía era de Prometheus, y ese
  backend ya no existe de este lado.
- Los hechos que hasta ADR-0037 salían como métricas (`agentic/core/measurement/CONTEXT.md`),
  listados abajo: cada uno o ya vivía como atributo del span de rol o de tarea, o pasa a
  vivir ahí ahora, casi siempre como un array de strings `"<dimensión>=<conteo>"` en vez
  de una muestra por dimensión (la forma que ADR-0008, adenda del 2026-09-17, ya usaba
  para `agentic.role.return_categories`). Lo que no tiene span dueño queda listado con su
  lector (`agentic eval compare`, el documento de tarea, o una consulta a la API de
  Langfuse).
- Los labels que antes segmentaban una métrica (`product`, `system_baseline`, `arm`,
  `system_version`, `type`, `role`, `model`) siguen siendo atributos del span, sin
  cambios: eso es justamente lo que ya eran (ADR-0008, adenda).

El `product` sale de lo que el producto declara en su `.agentic/stack.yaml`, no del nombre
del directorio ni del remoto de git: se mide el sistema, no el producto, y un span sin
`product` es un span que se suma con el de otro producto (issue #47). `reviewer_model` es
texto libre, no un enum, y por eso se queda en el frontmatter de la tarea, nunca en `arm`
(ADR-0030).

## Métricas: qué pasó con cada familia (ADR-0037)

Langfuse no ingiere métricas OTel — sólo trazas, observaciones y scores — así que esto no
fue copiar la tabla anterior columna por columna. Cada fila de abajo es una de las
familias que este sistema medía hasta ADR-0037: **Atributo** nombra la clave que lleva el
mismo hecho ahora (bare o `agentic.<familia>.<nombre>`, nunca renombrado), **Grano** el
span donde vive (`rol`, `tarea`, `invocación`), y — para las que no tienen span dueño —
la última columna nombra a quién preguntarle en su lugar. `tests/readme_metrics.py` lee
esta tabla como datos: `tests/core/measurement/adapters/otel/test_telemetry.py` la cruza
contra lo que `telemetry.py` realmente marca, con la misma disciplina de un solo lector
que ya tenía (ADR-0030): igualdad, nunca un subconjunto, y una familia que no queda
marcada en ningún lado hace fallar el chequeo con su propio nombre.

| Métrica (hasta ADR-0037) | Atributo ahora | Grano | Qué es / quién la lee |
|---|---|---|---|
| `agentic.task.cost_usd` | `task.cost_usd` | tarea | Costo acumulado de la tarea, bare desde antes de esta tarea, sin cambios |
| `agentic.task.rework_usd` | `task.rework_usd` | tarea | Costo del retrabajo de la tarea (`metrics.rework_cost_usd`), sin cambios |
| `agentic.task.duration` | — | — | Sin atributo nuevo: el span de tarea ya tiene su propio inicio y fin; se lee vía la API de Langfuse |
| `agentic.task.finished` | `task.status` | tarea | Terminada si el valor ∈ `scorecard.TERMINATED_STATUSES`; ya viajaba en el span, no hacía falta un atributo aparte |
| `agentic.task.returns` | `agentic.task.returns` | tarea | Devoluciones por categoría: ahora un array `"categoría=conteo"` en vez de una muestra por categoría |
| `agentic.role.cost_usd` | `agentic.role.cost_usd` | rol | Costo de la patch entera, un único valor en vez de una muestra por corrida más una residual; el desglose por corrida sigue en el span de corrida, con su propio `agentic.role.cost_usd` |
| `agentic.role.duration` | — | — | Sin atributo nuevo: el span de rol ya tiene su propio inicio y fin; se lee vía la API de Langfuse |
| `agentic.role.run_duration` | — | — | Sin atributo: el span de corrida ya tiene su propio inicio y fin (`RoleRun.started_at`/`.ended_at`); se lee vía la API de Langfuse |
| `agentic.role.queue_wait` | — | — | Sin span ni atributo propio: sigue en el documento de tarea, `runs[].queue_wait_s` |
| `agentic.role.failures` | `agentic.role.failures` | rol | Intentos fallidos de la patch, array `"motivo=conteo"` (sin `role.slug`/`phase`: esa dimensión no tiene span propio, sigue en el documento de tarea) |
| `agentic.role.failure_cost_usd` | `agentic.role.failure_cost_usd` | rol | Costo de los intentos fallidos, array `"motivo=costo"`, misma reducción de dimensión que la anterior |
| `agentic.role.tokens` | `agentic.role.tokens` | rol | Tokens de la patch entera, array `"kind=conteo"`, sumados sobre todas sus corridas; antes una muestra por corrida |
| `agentic.role.calls` | — | — | Sin atributo: sigue en el documento de tarea, `runs[].calls` |
| `agentic.role.turns` | `agentic.role.turns` | rol | Turnos de la última corrida de la patch, bare desde antes de esta tarea |
| `agentic.role.max_turns` | — | — | Sin atributo: sigue en el documento de tarea, `runs[].max_turns` |
| `agentic.role.tool_calls` | `agentic.role.tool_calls` | rol | Herramientas usadas por la patch entera, array `"tool=conteo"`, sumadas sobre todas sus corridas |
| `agentic.role.subagent_cost_usd` | — | — | Sin atributo: sigue en el documento de tarea, `runs[].subagent_cost_usd` |
| `agentic.role.playbook_hash` | `agentic.role.playbook_hash` | rol | Nueva con ADR-0043, sin métrica anterior: la **tupla** de los hashes de la sección de playbook que la patch registró (`playbooks` y `playbooks_changed` del documento), cada uno una vez; ausente si la patch no registró ninguno (rol sin playbook, o con el que la tarea ya tenía). Qué roles difieren por playbook y cuáles por plantilla lo dice `agentic eval compare` |
| `agentic.role.cold_fallback` | `agentic.role.cold_fallback` | rol | `true` si alguna corrida o intento fallido de la patch arrancó en frío; antes una muestra por rol y fase, ahora un booleano sobre la patch entera |
| `agentic.review.findings` | `agentic.review.findings` | rol | Hallazgos de la revisión, array `"severidad/categoría/estado=conteo"`, en el span del rol que revisó |
| `agentic.presubmit.checks` | `agentic.presubmit.checks` | rol | Chequeos de cada invocación de la puerta de entrega, array `"check/result/final=conteo"` (sin `phase`: sigue en el documento de tarea si hace falta); con un plan de chequeos afectados (ADR-0046) el estado de un chequeo es su última fila y el modo (`affected-only`/`full`) se lee de `presubmit_checks` del documento, no del span |
| `agentic.presubmit.duration` | `agentic.presubmit.duration` | rol | Duración de cada chequeo, array `"check=segundos"`, una entrada por chequeo e invocación, sin agregar entre invocaciones; con un plan de chequeos afectados la duración de un chequeo es la suma de sus filas (la corrida afectada y su repetición completa); con `parallel_checks` la suma de las entradas de una puerta no es su tiempo de pared, que es su máximo |
| `agentic.invocation.cost_usd` | `agentic.invocation.cost_usd` | invocación | Lo que esa invocación sola gastó, sin cambios |
| `agentic.invocation.rework_usd` | `agentic.invocation.rework_usd` | invocación | Retrabajo de esa invocación sola, sin cambios |
| `agentic.invocation.duration` | `agentic.invocation.duration` | invocación | Tiempo de reloj de esa invocación, sin cambios |
| `agentic.invocation.returns` | `agentic.invocation.returns` | invocación | Devoluciones de esa invocación por categoría, array en vez de una muestra por categoría |
| `agentic.task.invocations` | — | — | Sin atributo propio: cada invocación abre su propia traza de tarea con el mismo `task.id`; contarlas es una consulta a la API de Langfuse |
| `agentic.task.paused` | `agentic.task.paused` | invocación | Se marca sólo cuando esa invocación causó la pausa, igual que antes; el valor es la clase de escalado, ya no hace falta el conteo `1` de una muestra |
| `agentic.task.human_decisions` | `agentic.task.human_decisions` | invocación | `true` cuando el humano contestó al reanudar; ya no hace falta el conteo `1` de una muestra |
| `agentic.interview.rounds` | `agentic.interview.rounds` | rol | Sin cambios de valor, ahora un entero en el span en vez de una muestra |
| `agentic.interview.questions` | `agentic.interview.questions` | rol | Preguntas por destino, array `"destino=conteo"` en vez de una muestra por destino |
| `agentic.interview.invariants` | `agentic.interview.invariants` | rol | Propuestos/adoptados, array `"estado=conteo"` en vez de una muestra por estado |
| `agentic.delivery.outcome` | — | — | Ya no es una muestra: `agentic close` la escribe en el documento de tarea (`delivery_outcome`: `merged`, `merged_with_changes`, `closed` y, desde ADR-0045, `auto_merged`, el merge del poller, sin veredicto humano) |
| `agentic.delivery.human_commits` | — | — | Ya no es una muestra: en el documento de tarea (`human_commits`) |
| `agentic.delivery.human_lines_changed` | — | — | Ya no es una muestra: en el documento de tarea (`human_lines_changed`) |
| `agentic.delivery.time_to_merge` | — | — | Ya no es una muestra: en el documento de tarea (`time_to_merge_seconds`) |

`role.slug` (17 valores) y `phase` ∈ {definition, plan, development, review} ya no
acompañan a `agentic.role.failures`/`.tool_calls`/`.tokens`/`.presubmit.checks` como
dimensión del array: esa segmentación fina, cuando hace falta, se lee del documento de
tarea, que ya lleva cada corrida con su `slug` y su sección. `reason` sigue siendo la
única dimensión de `agentic.role.failures`/`.failure_cost_usd` ∈ {schema, check,
max_turns, limit, account_limit, upstream, network, sdk, other}, decidido en el sitio
donde se lanza `AttemptFailed`, nunca por parsear el mensaje de error (`network`: un
`api_error` sin status HTTP, ADR-0039).

### El scorecard (ADR-0028) se calcula igual, de una sola fuente

Las cuatro figuras — autonomía, fallos propios por tarea (excluye `account_limit`,
`upstream` y `network`, ADR-0029 y ADR-0039), costo por entrega, retrabajo — siguen calculándose en
`core/measurement/domain/scorecard.py`, leyendo los documentos de tarea de una línea de
base: el mismo camino que `agentic eval compare` recorre. Langfuse no deriva ningún
scorecard propio, y esta tarea no le empuja ninguno: no hay dashboard de Langfuse que lo
muestre (la compuerta de ADR-0037 dio `unreachable`/`unfavorable` en tres de sus cuatro
respuestas), así que el lector sigue siendo `agentic eval compare`, sobre los mismos
documentos que este mismo módulo ya recorría.

### Qué se pierde, dicho en ADR-0037

- **La lectura agregada de 13 meses** que sostenía Prometheus: `observability/langfuse/
  docker-compose.yml` corre con volúmenes Docker nombrados en una laptop, sin TTL
  declarado y sin backup. El documento de tarea es el registro durable; Langfuse es una
  superficie de lectura sobre lo que la instancia sostenga.
- **El detector de olor de retrabajo por panel**, la resta entre `agentic.role.cost_usd`
  y `task.rework_usd` sobre una ventana cerrada de Grafana. Su reemplazo es leer los
  mismos dos números del documento de tarea o de la traza (`agentic eval compare`),
  sin panel agregado detrás.
- **Todo lo demás que un panel de Grafana mostraba sin equivalente directo**: la fila de
  su propia familia, arriba, nombra a quién preguntarle ahora. Esta sección no repite esa
  lista; ADR-0037 tampoco.

## Scores por tarea (GH-273)

Los widgets de Langfuse agregan sólo valores de score, nunca metadata de span, así que los
números de negocio de una tarea terminada salen además como **scores** sobre una traza
portadora propia (una traza por tarea y por incarnación: `agentic.task.scores <id>`). Las
trazas y los atributos de arriba no cambian. Cómo se arma la identidad, el tiempo y la
escritura está en la adenda de GH-273 de ADR-0037; esta tabla dice qué trae cada score.

| Score | Fuente en el documento de tarea | Tipo | Se omite cuando |
|---|---|---|---|
| `cost_usd` | `cost_usd`, la misma cifra que `task.cost_usd` | numérico | nunca |
| `cycle_h` | horas desde `createdAt` del issue hasta `merged_at` del PR | numérico | el PR no se mergeó, o falló una lectura de `gh` |
| `active_h` | suma de `ended_at − started_at − queue_wait_s` de `runs` | numérico | alguna corrida no tiene marca de tiempo legible |
| `returns` | `metrics.changes_requested_total` | numérico | nunca |
| `failed_cost_usd` | suma de `failures[].cost_usd` | numérico | nunca |
| `delivered` | `delivery_outcome`: `merged`, `merged_with_changes` y `auto_merged` son verdadero, `closed` falso | booleano | no hay `delivery_outcome` (sin cierre, o descartada) |
| `origin` | `title`: `followup` si empieza por `Follow-up of #`, si no `original` | categórico | no hay título |
| `type` | `type` | categórico | nunca |

Reglas para leerlos:

- **El producto es un filtro, nunca una suma.** Cada score lleva el producto como su
  `environment` nativo; ningún widget suma entre `environment`.
- **El eje comparado es el `release`**, el `system_version` que el documento registró, y no
  el del proceso que escribe. `arm` y `baseline` (`system_baseline`) van en la metadata de
  cada score y en el span portador, también leídos del documento.
- **Cada score lleva el tiempo de la tarea**, de sus corridas (luego el `created_at` del PR,
  luego el `createdAt` del issue), nunca el momento de escribir.
- **`delivered` cuenta `auto_merged`**: el pedido lo define como "el PR se mergeó"; si lo
  juzgó una persona lo dice el scorecard (ADR-0045), no este score.
- Un score `BOOLEAN` o `CATEGORICAL` se agrega por su valor de texto, no por `value`.

`agentic scores backfill --product-dir <dir> [--dry-run]` escribe los mismos scores de todo
documento terminado (`ready_for_review`, `discarded` o cerrado) con las mismas funciones que
la escritura en vivo; una tarea sin terminar sale `skipped:not_finished` y se puntúa en vivo
cuando termine. Los ids son derivados, así que repetirlo completa huecos sin duplicar. Su
costo es lineal en los documentos: a lo sumo dos lecturas de `gh` y dos envíos (el span y el
lote de scores) por documento.

## Costo en Langfuse

Langfuse pricea cada llamada al modelo (`claude_code.llm_request`) a partir de sus tokens
y su tabla de precios; el collector traduce los nombres de Claude Code (`input_tokens`,
`cache_read_tokens`...) a `gen_ai.usage.*`, que es lo único que Langfuse lee. El sistema no
manda dólares: el costo de un rol o de una tarea en Langfuse es la suma de sus llamadas, y
el span de rol va marcado como `agent`. La cifra exacta por rol (`agentic.role.cost_usd`, la
que reporta el SDK) sigue en el documento de tarea y en el propio span; en Langfuse puede
quedar algo corta porque Claude Code emite algunas llamadas fuera de toda interacción y
esas no cuelgan de ningún rol (ADR-0008, adenda).

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

Requiere en `observability/langfuse/.env` (no versionado): `LANGFUSE_OTEL_BASIC`. Sin
ella el collector no arranca.

En el `.env` de la raíz, las `OTEL_*` del `.env.example` apuntan al collector. El sistema
solo exporta si `OTEL_EXPORTER_OTLP_ENDPOINT` está seteada.
