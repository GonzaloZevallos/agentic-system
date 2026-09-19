---
status: accepted
date: 2026-09-19
---

# La compuerta de GH-70 pasa: `TRACEPARENT` ya alcanza; lo que falta es el marcador, no la propagación

**La compuerta pasa.** El spike (`tests/llm/test_trace_propagation.py`, extracto abajo) corrió
un rol real contra el SDK y el CLI fijados, con `Agent` entre sus tools, e inyectó un
`TRACEPARENT` propio en el entorno del hijo — exactamente lo que `runner.py:119` ya hace
hoy. Las tres condiciones que el plan declaró antes de correrlo:

1. **¿Un `claude_code.llm_request` de un sub-agente lleva algún padre cuando
   `TRACEPARENT` está en el entorno del hijo?** Sí: 7 de 7 spans en la corrida marcada, 8
   de 8 en la de control, todos con `parent_span_id` no vacío.
2. **¿A qué span parenta?** A la cadena real de la interacción: el span de sub-agente
   (`claude_code.tool.execution`) cuelga de `claude_code.tool`, que cuelga de
   `claude_code.interaction`, que cuelga del span que `TRACEPARENT` señaló —no de la raíz
   de una traza nueva.
3. **¿Una segunda variable, o nada, cambia eso?** Nada: la corrida con
   `OTEL_RESOURCE_ATTRIBUTES` (nuestro marcador) y la corrida sin él parentan idéntico.
   El marcador identifica; no propaga.

Conclusión: el primer punto del pedido original ("propagar el contexto de traza... con
`TRACEPARENT` explícito en el entorno que el SDK hereda") ya está resuelto por el código
que existe desde antes de esta tarea. La sospecha de la Pregunta 3 de la ronda 1 de la
entrevista se confirma con evidencia, no con lectura de código: no entra código de
propagación nuevo. Lo único que la subtarea `marker` agrega es el marcador de identidad
(`agentic.role.slug`, `agentic.trace_id` en `OTEL_RESOURCE_ATTRIBUTES`), para que la
métrica de abajo sea calculable sobre *nuestras* raíces y no sobre cualquier raíz que
llegue al mismo collector sin autenticación.

## Extracto del spike

```
$ uv run pytest -m llm tests/llm/test_trace_propagation.py -q -s
--- with TRACEPARENT + our OTEL_RESOURCE_ATTRIBUTES marker ---
  span=9ef7f803 parent=eaf1b182 session=acf6141b-f02c-4649-ba9a-8d6ba0946970 name=claude_code.interaction
  span=f262b9d9 parent=9ef7f803 session=acf6141b-f02c-4649-ba9a-8d6ba0946970 name=claude_code.llm_request
  span=d8de8b79 parent=9ef7f803 session=acf6141b-f02c-4649-ba9a-8d6ba0946970 name=claude_code.tool
  span=fce18125 parent=d8de8b79 session=acf6141b-f02c-4649-ba9a-8d6ba0946970 name=claude_code.tool.blocked_on_user
  span=1f79079a parent=d8de8b79 session=acf6141b-f02c-4649-ba9a-8d6ba0946970 name=claude_code.tool.execution
  span=1e15e71a parent=1f79079a session=acf6141b-f02c-4649-ba9a-8d6ba0946970 name=claude_code.llm_request
  span=f6c88b27 parent=9ef7f803 session=acf6141b-f02c-4649-ba9a-8d6ba0946970 name=claude_code.llm_request

--- with TRACEPARENT only, no resource-attributes marker ---
  span=b797ac39 parent=5dbd4ca9 session=0493fecd-3481-41d6-b058-33d65cac476c name=claude_code.interaction
  span=37c332cb parent=b797ac39 session=0493fecd-3481-41d6-b058-33d65cac476c name=claude_code.llm_request
  span=09fd182f parent=b797ac39 session=0493fecd-3481-41d6-b058-33d65cac476c name=claude_code.tool
  span=cc7459fe parent=09fd182f session=0493fecd-3481-41d6-b058-33d65cac476c name=claude_code.tool.blocked_on_user
  span=040908d3 parent=09fd182f session=0493fecd-3481-41d6-b058-33d65cac476c name=claude_code.tool.execution
  span=b1f68ad3 parent=040908d3 session=0493fecd-3481-41d6-b058-33d65cac476c name=claude_code.llm_request
  span=d03b7f59 parent=b797ac39 session=0493fecd-3481-41d6-b058-33d65cac476c name=claude_code.llm_request
  span=dacb21cb parent=af85345d session=0493fecd-3481-41d6-b058-33d65cac476c name=claude_code.llm_request

condition (1) any parent at all: True (7/7)
condition (2) parent span id(s) seen: ['1f79079a8cf64c15', '9ef7f803ffa62b94', 'd8de8b799298edc1', 'eaf1b182c05df487']
condition (3) parenting without the marker: True (8/8) -- same as with the marker
.
1 passed in 28.31s
```

Nota sobre alcance: el spike ejercitó un sub-agente lanzado por la tool `Agent` **dentro
de la misma sesión CLI** — la forma en que el skill `code-review` la usa en una corrida
que entrega normal. No ejercitó el otro cubo del pedido original: un sub-agente que
sigue corriendo **después** de que el rol entregó y el `break` de `run_once` cierra el
generador (issue #16). Ese cubo es el "riesgo conocido" de más abajo, no algo que este
spike pueda probar sin deshacer esa decisión — que el invariante 1 prohíbe.

## La métrica, por rama, medida en la que se tomó

**El plan escribió la métrica de la rama "pasa" antes del resultado**: (i) control
positivo — el número de pares distintos `(agentic.role.slug, agentic.trace_id)` con
nuestro marcador en una corrida completa iguala el número de corridas de rol que el
documento de tarea registra, y todo valor de marcador mapea a una corrida ahí; (ii) cero
raíces `claude_code.llm_request` sin padre que lleven nuestro marcador. La subtarea
`marker` es quien la mide sobre una corrida real, porque es la que agrega el marcador a
lo que el runner ya spawea; esta subtarea no cambia `runner.py` (fuera de alcance,
explícito en el brief) y no tiene un runner instrumentado para medirla todavía.

## La partición de los huérfanos históricos

Consulta, contra el ClickHouse local de Langfuse, en la forma que
`observability/collector/probe.py` ya usa (`events_core FINAL`, autenticado con
`CLICKHOUSE_PASSWORD` de `observability/langfuse/.env`), corrida el 2026-09-19 a las
07:5x UTC, commit `42614fa` (HEAD de la rama base, `agentic-system`), ventana
2026-09-13T00:00:00Z–2026-09-19T00:00:00Z (la ventana del pedido original, 13–18 de
septiembre):

```sql
-- raíces huérfanas de la ventana
SELECT trace_id, span_id, session_id, start_time
FROM events_core FINAL
WHERE name = 'claude_code.llm_request' AND parent_span_id = ''
  AND start_time >= '2026-09-13 00:00:00' AND start_time < '2026-09-19 00:00:00'
-- total: 1194 filas, 223 session_id distintos
```

El discriminador declarado es el `session_id`, cruzado contra el de las corridas de rol
que registran los documentos de tarea bajo `.agentic/tasks/` (`resumed_from` incluido).
**Esos documentos no están comiteados en ningún ref de este repo** — se verificó con
`git log --all --diff-filter=A -- '.agentic/tasks/'` y `git rev-list --all | xargs git
ls-tree -r --name-only | grep '^.agentic/tasks/'`, ambos vacíos — así que `git show
<ref>:<path>` no tiene qué mostrar; se leyeron directo del working tree del checkout
principal (`/home/zeta/dev/repos/agentic-system/.agentic/tasks/*.md`, 12 documentos) al
mismo commit `42614fa`, y esto se deja escrito en vez de fingir un ref que no existe:

```sql
-- huérfanos cuyo session_id aparece en algún documento (470 filas, 85 sesiones)
-- vs. huérfanos cuyo session_id no aparece en ninguno (724 filas, residual)
SELECT
  countIf(session_id IN (<124 session_id de los 12 documentos>)) AS documentados,
  uniqExactIf(session_id, session_id IN (<...>)) AS sesiones_documentadas,
  countIf(session_id NOT IN (<...>)) AS residual
FROM (<la consulta de arriba>)
-- documentados: 470 (85 sesiones) · residual: 724 (138 sesiones)
```

**El split (a)/(b) —dentro de la ventana de la corrida vs. después de que terminó— es
incomputable con lo que hay, y no por falta de intentarlo.** El plan pidió usar el fin
del span de rol desde el propio almacén de trazas en vez de `ended_at` del documento
(los documentos de antes del 2026-09-18 no tienen esos campos). Se buscó el span de rol
por su propio `session_id`, con los dos nombres que tuvo (`invoke_agent <Role.name>`
desde el 2026-09-17, el nombre desnudo del rol antes — ambos existen en ClickHouse,
confirmado por consulta directa), para cualquiera de las 85 sesiones documentadas que
tienen un huérfano: **ninguna coincide**. La razón, una vez mirada: el `session_id` que
Langfuse agrupa para un span nativo de Claude Code es el de conversación del SDK (el
mismo que el documento registra en `runs[].session_id`), pero el span de rol propio del
sistema (`role_span`, `telemetry.py`) no lleva ese atributo — no hay ninguna fila con
nombre de rol y ese `session_id` para cruzar. El resultado correcto, entonces, es el que
el plan previó para este caso exacto: **(a) y el residual quedan resueltos, (b) queda
incomputable** con el discriminador declarado — no se corrió una tercera consulta contra
otro proxy.

Lectura de los números: de los 1194 huérfanos de la ventana, 470 (85 sesiones) están en
una sesión que algún documento de tarea local conoce — dinero que ya cuenta en
`agentic.role.cost_usd` de esa corrida, mal atribuido a nivel de llamada en Langfuse, tal
como la Pregunta 1 de la entrevista corrigió; los 724 restantes (138 sesiones) son
residuales, en sesiones que ningún documento registra, consistente con la Pregunta 2: el
`.env` raíz expone `CLAUDE_CODE_ENABLE_TELEMETRY=1` contra un collector sin
autenticación, y el humano desarrolla este mismo repo con Claude Code a diario. El
número total (1194) y su partición no coinciden con los "505" del pedido original: son
consultas distintas, en momentos distintos, contra una base que sigue recibiendo tráfico
desde que se escribió el issue; no se intentó reconciliarlas.

## Riesgo conocido, escrito para no descubrirlo en un panel

La plata de una llamada que sigue después de que el rol entregó y `run_once` corta el
generador (issue #16) queda fuera de `agentic.role.cost_usd`, de
`agentic.role.subagent_cost_usd` y de todo documento de tarea — el mismo lugar donde ya
vivía. Este task no la recupera: recuperarla pediría no cortar el stream a la primera
entrega, que el invariante 1 prohíbe explícitamente porque gasta turnos contra
`max_turns` y dólares contra el tope por intento sobre un veredicto ya tomado. El
marcador (`marker`, subtarea siguiente) hace esas llamadas *identificables* en Langfuse
—quedan marcadas con el `agentic.trace_id` y el `agentic.role.slug` de la corrida que las
originó, aun sin padre— pero identificable no es lo mismo que contado: su dólar sigue sin
sumar a ninguna cifra que el sistema reporte. Aceptado a sabiendas, no descubierto en un
panel.

## Opciones descartadas

- **Tratar el spike como innecesario porque `runner.py:119` "ya lo hace"**: es
  precisamente la lectura de código que la Pregunta 3 de la entrevista prohibió sin
  evidencia corrida contra el SDK fijado. El spike costó una corrida real y confirmó la
  lectura; sin él, esta ADR sería una opinión sobre el comportamiento del CLI, no un
  hecho verificado (ADR-0016).
- **Forzar el split (a)/(b) contra el `ended_at` del documento**: la adenda del
  2026-09-18 a ADR-0008 ya deja escrito que los documentos de antes de esa fecha no
  tienen ese campo, y la ventana de este issue es casi toda anterior. Habría sido
  inventar una cifra sobre un campo que no existe para casi todos los 12 documentos.
