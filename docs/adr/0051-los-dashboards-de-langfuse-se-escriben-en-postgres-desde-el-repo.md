---
status: accepted
date: 2026-10-17
---

# Los dashboards de Langfuse se escriben en Postgres desde el repo

Enmienda a ADR-0037 por nombre, sin editarlo (un ADR aceptado se enmienda, nunca se edita,
ADR-0010): ADR-0037 dejó a Langfuse local como único destino y anotó que `langfuse:4` no
expone un endpoint público de dashboards. Esta ADR dice cómo se crean igual, como código,
y deja el catálogo de lo que un widget puede leer, medido contra la instancia local el
2026-10-17 (imagen `docker.langfuse.com/langfuse/langfuse:4`, `langfuse-web` 4.35.0).

Todo lo de abajo se leyó de la instancia viva, no de la documentación. Los comandos van
tal cual; las salidas van recortadas sólo donde se dice, y la clave pública se reemplaza por
`<redacted>`. El fixture `tests/fixtures/langfuse_dashboards/golden.json` guarda los mismos
datos como JSON para que la subtarea `definitions` pruebe contra ellos.

## Decisión

`agentic dashboards apply` escribe las filas de `dashboards` y `dashboard_widgets`
directamente en el Postgres de Langfuse, desde el host, con psycopg 3, por el puerto
publicado `127.0.0.1:5432`. La API pública no es una opción (ADR-0037 no encontró el
endpoint) y tRPC es una ruta de sesión de la UI.

- **Ids.** `uuid5(NAMESPACE, f"{producto}/{dashboard}[/{widget}]")`: aplicar dos veces no
  cambia nada.
- **Marcador.** Cada fila lleva en `description` un marcador que nombra al producto.
- **Proyecto.** El de `api_keys.project_id` cuyo `public_key` es `AGENTIC_LANGFUSE_PUBLIC_KEY`
  (sección 5). Una clave que no coincide con ningún proyecto se rechaza. Cada escritura y
  cada borrado van acotados a ese proyecto y a ese marcador.
- **Preflight.** Antes de escribir se compara `information_schema.columns` de las dos
  tablas con el conjunto guardado junto al tag de la imagen (el golden); cualquier
  diferencia rechaza la escritura.
- **Lectura de vuelta.** Tras escribir se leen las filas y el comando falla si difieren del
  plan.
- **Ediciones de la UI.** Una edición en la UI de un dashboard nuestro se pisa en el
  siguiente `apply`, a propósito: el repo es la fuente.
- **`--verify` es manual y nunca corre en CI**: cuenta, por widget, contra ClickHouse.

## Riesgos que esta ADR acepta

1. **El render no está probado hasta el apply vivo.** CI prueba el plan, la forma contra el
   golden y los contratos; sólo el apply vivo, la lectura de vuelta y `--verify` prueban
   que Langfuse dibuja cada widget. Tampoco está probado cómo agrupa y apila la UI los
   tipos `BAR_TIME_SERIES` / `AREA_TIME_SERIES` con una dimensión: el esquema no tiene un
   campo de apilado (sección 4).
2. **Un cambio de imagen puede cambiar la forma del JSON sin cambiar ninguna columna.** El
   golden se refresca en cada cambio de imagen.
3. **El backfill y `--verify` son pasos manuales, posteriores al merge**, del humano:
   ADR-0017 rechaza el backfill desde este checkout. Hoy ClickHouse sólo tiene scores de
   sondas (sección 6): los dashboards salen vacíos hasta que se corra el backfill.

## 1. Columnas de `dashboards` y `dashboard_widgets`

Comando (dentro del contenedor, con el `psql` de la imagen):

```
docker exec langfuse-postgres-1 psql -U postgres -d postgres -c "select table_name, ordinal_position, column_name, data_type, is_nullable, column_default from information_schema.columns where table_schema='public' and table_name in ('dashboards','dashboard_widgets') order by table_name, ordinal_position"
```

```
    table_name     | ordinal_position | column_name  |          data_type          | is_nullable |  column_default   
-------------------+------------------+--------------+-----------------------------+-------------+-------------------
 dashboard_widgets |                1 | id           | text                        | NO          | 
 dashboard_widgets |                2 | created_at   | timestamp without time zone | NO          | CURRENT_TIMESTAMP
 dashboard_widgets |                3 | updated_at   | timestamp without time zone | NO          | CURRENT_TIMESTAMP
 dashboard_widgets |                4 | created_by   | text                        | YES         | 
 dashboard_widgets |                5 | updated_by   | text                        | YES         | 
 dashboard_widgets |                6 | project_id   | text                        | YES         | 
 dashboard_widgets |                7 | name         | text                        | NO          | 
 dashboard_widgets |                8 | description  | text                        | NO          | 
 dashboard_widgets |                9 | view         | USER-DEFINED                | NO          | 
 dashboard_widgets |               10 | dimensions   | jsonb                       | NO          | 
 dashboard_widgets |               11 | metrics      | jsonb                       | NO          | 
 dashboard_widgets |               12 | filters      | jsonb                       | NO          | 
 dashboard_widgets |               13 | chart_type   | USER-DEFINED                | NO          | 
 dashboard_widgets |               14 | chart_config | jsonb                       | NO          | 
 dashboard_widgets |               15 | min_version  | integer                     | NO          | 1
 dashboards        |                1 | id           | text                        | NO          | 
 dashboards        |                2 | created_at   | timestamp without time zone | NO          | CURRENT_TIMESTAMP
 dashboards        |                3 | updated_at   | timestamp without time zone | NO          | CURRENT_TIMESTAMP
 dashboards        |                4 | created_by   | text                        | YES         | 
 dashboards        |                5 | updated_by   | text                        | YES         | 
 dashboards        |                6 | project_id   | text                        | YES         | 
 dashboards        |                7 | name         | text                        | NO          | 
 dashboards        |                8 | description  | text                        | NO          | 
 dashboards        |                9 | definition   | jsonb                       | NO          | 
 dashboards        |               10 | filters      | jsonb                       | NO          | '[]'::jsonb
(25 rows)
```

La misma consulta, ahora sí desde el host por el puerto publicado `127.0.0.1:5432` (el host no
tiene `psql` ni psycopg instalados: se corrió con `uv run --no-project --with 'psycopg[binary]'`,
sin tocar el proyecto). La primera línea es `host puerto` de la conexión; las demás, las filas
verbatim como tuplas de Python:

```
uv run --no-project --with 'psycopg[binary]' python q.py   # q.py: psycopg.connect("postgresql://…@127.0.0.1:5432/postgres") y la consulta de arriba
127.0.0.1 5432
('dashboard_widgets', 1, 'id', 'text', 'NO', None)
('dashboard_widgets', 2, 'created_at', 'timestamp without time zone', 'NO', 'CURRENT_TIMESTAMP')
('dashboard_widgets', 3, 'updated_at', 'timestamp without time zone', 'NO', 'CURRENT_TIMESTAMP')
('dashboard_widgets', 4, 'created_by', 'text', 'YES', None)
('dashboard_widgets', 5, 'updated_by', 'text', 'YES', None)
('dashboard_widgets', 6, 'project_id', 'text', 'YES', None)
('dashboard_widgets', 7, 'name', 'text', 'NO', None)
('dashboard_widgets', 8, 'description', 'text', 'NO', None)
('dashboard_widgets', 9, 'view', 'USER-DEFINED', 'NO', None)
('dashboard_widgets', 10, 'dimensions', 'jsonb', 'NO', None)
('dashboard_widgets', 11, 'metrics', 'jsonb', 'NO', None)
('dashboard_widgets', 12, 'filters', 'jsonb', 'NO', None)
('dashboard_widgets', 13, 'chart_type', 'USER-DEFINED', 'NO', None)
('dashboard_widgets', 14, 'chart_config', 'jsonb', 'NO', None)
('dashboard_widgets', 15, 'min_version', 'integer', 'NO', '1')
('dashboards', 1, 'id', 'text', 'NO', None)
('dashboards', 2, 'created_at', 'timestamp without time zone', 'NO', 'CURRENT_TIMESTAMP')
('dashboards', 3, 'updated_at', 'timestamp without time zone', 'NO', 'CURRENT_TIMESTAMP')
('dashboards', 4, 'created_by', 'text', 'YES', None)
('dashboards', 5, 'updated_by', 'text', 'YES', None)
('dashboards', 6, 'project_id', 'text', 'YES', None)
('dashboards', 7, 'name', 'text', 'NO', None)
('dashboards', 8, 'description', 'text', 'NO', None)
('dashboards', 9, 'definition', 'jsonb', 'NO', None)
('dashboards', 10, 'filters', 'jsonb', 'NO', "'[]'::jsonb")
```

Las 25 filas coinciden con la tabla de arriba; la tabla se leyó dentro del contenedor, la
lectura del host es la que `apply` repetirá.

Los dos `USER-DEFINED` son enums de Postgres:

```
docker exec langfuse-postgres-1 psql -U postgres -d postgres -c "select e.enumtypid::regtype, e.enumlabel from pg_enum e where e.enumtypid::regtype::text in ('\"DashboardWidgetViews\"','\"DashboardWidgetChartType\"') order by 1, e.enumsortorder"
```

```
         enumtypid          |     enumlabel      
----------------------------+--------------------
 "DashboardWidgetViews"     | TRACES
 "DashboardWidgetViews"     | OBSERVATIONS
 "DashboardWidgetViews"     | SCORES_NUMERIC
 "DashboardWidgetViews"     | SCORES_CATEGORICAL
 "DashboardWidgetViews"     | SCORES_BOOLEAN
 "DashboardWidgetChartType" | LINE_TIME_SERIES
 "DashboardWidgetChartType" | BAR_TIME_SERIES
 "DashboardWidgetChartType" | HORIZONTAL_BAR
 "DashboardWidgetChartType" | VERTICAL_BAR
 "DashboardWidgetChartType" | PIE
 "DashboardWidgetChartType" | NUMBER
 "DashboardWidgetChartType" | HISTOGRAM
 "DashboardWidgetChartType" | PIVOT_TABLE
 "DashboardWidgetChartType" | AREA_TIME_SERIES
(14 rows)
```

Claves foráneas (de `\d dashboards` y `\d dashboard_widgets`): `project_id` →
`projects(id)` `ON DELETE CASCADE`; `created_by` y `updated_by` → `users(id)` `ON DELETE SET
NULL`. Clave primaria: `id` (texto). Un `INSERT … ON CONFLICT (id)` es posible.

## 2. Filas existentes

Hay filas preset: **5 dashboards y 36 widgets, todos con `project_id` NULL** (propiedad de
Langfuse, no de un proyecto):

```
docker exec langfuse-postgres-1 psql -U postgres -d postgres -c "select count(*) from dashboards where project_id is null" -c "select count(*) from dashboard_widgets where project_id is null"
 count 
-------
     5
 count 
-------
    36
```

Los dashboards: `Langfuse Latency Dashboard`, `Langfuse Agent Dashboard`, `Langfuse Usage
Management`, `Langfuse Home` (id `langfuse-home-dashboard`) y `Langfuse Cost Dashboard`. Los cuatro
widgets de scores preset (`Total Score Count`) tienen `dimensions` vacío: ninguno agrupa por `name`. Dos filas verbatim, la forma que el
`apply` debe imitar (el resto está en el golden):

```
-[ RECORD ]+--------------------------------------------------------
id           | cmawk5sik00igad07kjetg17j
project_id   | 
name         | Cost by Model Name
description  | Total cost broken down by model name
view         | OBSERVATIONS
dimensions   | [{"field": "providedModelName"}]
metrics      | [{"agg": "sum", "measure": "totalCost"}]
filters      | []
chart_type   | VERTICAL_BAR
chart_config | {"type": "VERTICAL_BAR", "row_limit": 100}
min_version  | 1

-[ RECORD ]+--------------------------------------------------------
id           | cmawltpsx00yaad07f51yvkwg
project_id   | 
name         | Total Score Count (numeric)
description  | Trend of numeric score count over time
view         | SCORES_NUMERIC
dimensions   | []
metrics      | [{"agg": "count", "measure": "count"}]
filters      | []
chart_type   | BAR_TIME_SERIES
chart_config | {"type": "BAR_TIME_SERIES"}
min_version  | 1
```

Y la forma de `dashboards.definition` (`Langfuse Cost Dashboard`, `filters` = `[]`), recortada
a dos de sus once ubicaciones:

```
{"widgets": [{"x": 0, "y": 2, "id": "c1e456c3-9e4a-4693-99de-ea3996e15003", "type": "widget", "x_size": 4, "y_size": 3, "widgetId": "cma2f2ioc001had07f7810kg1"}, {"x": 0, "y": 5, "id": "6d03b598-7950-423b-8e22-25ab4ac98b75", "type": "widget", "x_size": 4, "y_size": 6, "widgetId": "cmawk9xbu00lfad07s9j1bxnx"}, …]}
```

Consecuencia para el borrado: los presets (`project_id` NULL) nunca entran en el alcance de
un borrado acotado por proyecto y marcador.

## 3. Esquema de validación incluido en la imagen

Se leyó con `docker cp langfuse-langfuse-web-1:/app/web/.next/server/chunks` y una búsqueda
de los identificadores en los chunks de servidor (el esquema zod del paquete `shared`, chunk
`_0p5o1eu._.js` de ese build). El código fuente no está en la imagen; es JavaScript
compilado, y lo siguiente es verbatim de ese bundle.

Widget guardado y dashboard (`DashboardDefinitionSchema`, `CreateWidgetInputSchema`):

```
r.DimensionSchema=a.z.object({field:a.z.string()}),r.MetricSchema=a.z.object({measure:a.z.string(),agg:a.z.string()}),r.ChartConfigSchema=a.z.discriminatedUnion("type",[r.LineChartTimeSeriesConfig,r.BarChartTimeSeriesConfig,r.AreaChartTimeSeriesConfig,r.HorizontalBarChartConfig,r.VerticalBarChartConfig,r.PieChartConfig,r.BigNumberChartConfig,r.HistogramChartConfig,r.PivotTableChartConfig]),r.DashboardDefinitionWidgetWidgetSchema=a.z.object({type:a.z.literal("widget"),id:a.z.string(),widgetId:a.z.string(),x:a.z.number().int().gte(0),y:a.z.number().int().gte(0),x_size:a.z.number().int().positive(),y_size:a.z.number().int().positive()}),r.DashboardDefinitionSchema=a.z.object({widgets:a.z.array(r.DashboardDefinitionWidgetSchema)})
r.BaseTimeSeriesChartConfig=a.z.object({}),r.BaseTotalValueChartConfig=a.z.object({row_limit:a.z.number().int().positive().lte(1e3).optional()}),r.LineChartTimeSeriesConfig=r.BaseTimeSeriesChartConfig.extend({type:a.z.literal("LINE_TIME_SERIES")}),r.BarChartTimeSeriesConfig=r.BaseTimeSeriesChartConfig.extend({type:a.z.literal("BAR_TIME_SERIES")}),r.AreaChartTimeSeriesConfig=r.BaseTimeSeriesChartConfig.extend({type:a.z.literal("AREA_TIME_SERIES")})
```

La configuración de un gráfico de serie temporal es `{}` más `type`: **no guarda
granularidad, ni apilado, ni rango de fechas**. `dashboards` tampoco tiene columna de rango:
el rango y la granularidad son estado de la UI.

Validación al escribir por la capa de servicio (`createWidget`, mismo bundle):

```
r.resolveDashboardWidgetMinVersion=(e,t)=>{let r="legacy"===o.env.LANGFUSE_MIGRATION_V4_WRITE_MODE?1:2,n=(0,l.getWidgetRequiredVersion)(e);if(n>r)throw …;return Math.min(Math.max(t??1,n),r)}
```

Con el modo de escritura por defecto de v4 el `min_version` se resuelve a 2: la UI consulta
las vistas v2 (`events_core`). Las vistas v1 leen las tablas `traces` y `observations` de
ClickHouse, que en esta instancia están vacías (`select count() from traces FINAL` → `0`,
`observations` → `0`; `events_core` → 356603 filas). Por eso el `apply` escribe
`min_version = 2` y los widgets usan sólo dimensiones que existen en la vista v2.

Filtros (`singleFilter`) y agregaciones (`metricAggregations`), verbatim:

```
r.filterOperators={datetime:[">","<",">=","<="],string:["=","contains","does not contain","starts with","ends with","is not empty"],stringOptions:["any of","none of"],categoryOptions:["any of","none of"],arrayOptions:["any of","none of","all of"],number:["=",">","<",">=","<="],stringObject:["=","contains","does not contain","starts with","ends with"],numberObject:["=",">","<",">=","<="],booleanObject:["=","<>"],boolean:["=","<>"],null:["is null","is not null"],positionInTrace:["="]}
r.metricAggregations=n.z.enum(["sum","avg","count","max","min","p50","p75","p90","p95","p99","histogram","uniq"])
r.granularities=n.z.enum(["auto","minute","hour","day","week","month","5m","10m","15m","30m","1h","2h","4h","1d","2d","1w"])
```

## 4. El catálogo que un widget puede usar

Vistas (`persistedWidgetViewToQueryView`): `TRACES` → `traces`, `OBSERVATIONS` →
`observations`, `SCORES_NUMERIC` → `scores-numeric`, `SCORES_BOOLEAN` → `scores-boolean`,
`SCORES_CATEGORICAL` → `scores-categorical`.

| Pregunta | Respuesta, de la declaración de vista del bundle |
|---|---|
| ¿Hay mediana (`p50`)? | Sí, en cualquier medida numérica (`getValidAggregationsForMeasureType` devuelve las doce agregaciones para `integer`/`decimal`/`number`; para el resto, `count` y `uniq`). |
| ¿Hay `sum`? | Sí, mismo conjunto. |
| Medidas de scores numéricos | `count` (integer) y `value` (number, agregación por defecto `avg`). Tiempo: `timestamp` del score. |
| Medidas de scores booleanos | `count` y `value` (0/1). Categóricos: sólo `count`. |
| ¿Se puede agrupar por `name` de score? | Sí: dimensión `name` en las tres vistas de scores. También `environment`, `source`, `dataType`, `traceId`, `timestampDay`, `timestampMonth`. |
| ¿Se puede agrupar por `release` en la vista de scores? | Sólo como `traceRelease` (`events_traces.release`, el release del span raíz de la traza del score). En v2 la vista `observations` tiene `release` y `traceRelease`. El carrier trace de GH-273 escribe `langfuse.release`, así que `traceRelease` lo lleva. |
| ¿Se puede filtrar o agrupar por metadata de un score (`type`, `origin`, `release`, `arm`, `baseline`)? | **No.** Las dimensiones de la vista de scores son las listadas arriba; ninguna lee `metadata` del score, salvo `isEvaluatorTest` (interna, `uiHidden`). `type` y `origin` existen como scores categóricos con su propio `name`, pero un widget cuenta filas de una sola vista y no puede agrupar un score numérico por el valor de *otro* score de la misma tarea. |
| Dimensiones de `observations` (v2) | `environment`, `type`, `name`, `level`, `providedModelName`, `traceName`, `release`, `traceRelease`, `tags`, `sessionId`, `userId`, `costType`, … Medidas: `count`, `latency`, `inputTokens`, `outputTokens`, `totalTokens`, `inputCost`, `outputCost`, `totalCost`, `costByType`, … |
| Dimensiones de `traces` (v1) | `id`, `name`, `tags`, `userId`, `sessionId`, `release`, `version`, `environment`, `timestampMonth`. Medidas: `count`, `observationsCount`, `scoresCount`, `latency`, `totalTokens`, `totalCost`. Vista v1: lee la tabla `traces`, vacía aquí. No se usa. |
| Filtros | `singleFilter` (arriba); la columna de un filtro es el nombre de una dimensión de la vista, y `environment` es dimensión de las tres vistas de scores y de `observations`, así que un filtro `stringOptions` `any of` sobre `environment` es válido. |
| Tipos de gráfico | `LINE_TIME_SERIES`, `BAR_TIME_SERIES`, `AREA_TIME_SERIES`, `HORIZONTAL_BAR`, `VERTICAL_BAR`, `PIE`, `NUMBER`, `HISTOGRAM`, `PIVOT_TABLE`. **No hay un campo de apilado** en la configuración: si `BAR_TIME_SERIES` / `AREA_TIME_SERIES` con una dimensión se apilan es comportamiento del render y no se pudo leer del bundle de servidor (riesgo 1). |
| Granularidad temporal | El enum de consulta incluye `week` y `1w`, pero la configuración del widget no la guarda: **no se puede fijar a una semana desde la definición**. La barra de tiempo la elige la UI según el rango. La semana calendario del brief queda como instrucción del README (rango de la UI), no como propiedad del widget. |
| "Últimos 7 días" | No hay rango en la fila del dashboard (sección 3): la definición no puede fijarlo. El README de Operación lo declara como el rango de la UI. |

## 5. De la clave pública al proyecto

```
docker exec langfuse-postgres-1 psql -U postgres -d postgres -c "select k.id, k.public_key, k.project_id, k.scope, p.name from api_keys k left join projects p on p.id=k.project_id"
            id             |                 public_key                 |   project_id   |  scope  |      name      
---------------------------+--------------------------------------------+----------------+---------+----------------
 cmu0a6oos0001m707ho3kikjj | pk-lf-<redacted>                           | agentic-system | PROJECT | agentic-system
```

La relación es `api_keys.project_id` → `projects.id`, y `api_keys.public_key` es único
(`api_keys_public_key_key`). La consulta es `select project_id from api_keys where public_key =
%s and scope = 'PROJECT'`. `project_id` puede ser NULL en una clave de organización
(`scope = 'ORGANIZATION'`): esa también se rechaza.

## 6. Qué hay hoy en ClickHouse

```
curl -s "http://127.0.0.1:8123/?user=clickhouse&password=<password>" --data-binary "select environment, name, count() AS n, min(timestamp) AS first, max(timestamp) AS last from scores FINAL group by environment,name order by 1,2 format PrettyCompactMonoBlock"
   ┌─environment────────┬─name───────────────────┬─n─┬───────────────────first─┬────────────────────last─┐
1. │ agentic-gate-probe │ agentic.gate.cost_usd  │ 2 │ 2025-10-09 09:53:20.000 │ 2025-10-09 09:53:20.000 │
2. │ agentic-gate-probe │ agentic.gate.delivered │ 2 │ 2025-10-09 09:53:20.000 │ 2025-10-09 09:53:20.000 │
3. │ agentic-gate-probe │ agentic.gate.origin    │ 2 │ 2025-10-09 09:53:20.000 │ 2025-10-09 09:53:20.000 │
4. │ default            │ agentic.gate.known     │ 2 │ 2026-09-24 14:50:27.529 │ 2026-09-24 14:50:58.797 │
5. │ default            │ agentic.gate.unknown   │ 2 │ 2026-09-24 14:50:27.689 │ 2026-09-24 14:51:01.877 │
   └────────────────────┴────────────────────────┴───┴─────────────────────────┴─────────────────────────┘
```

Sólo hay scores de sondas: el backfill de GH-273 todavía no corrió contra esta instancia.

## Qué lee cada widget

Filtros: `environment` es un filtro `stringOptions` `any of` con el producto; "`name` `any of` la familia" es un filtro `stringOptions` `any of` con la lista enumerada de los nombres de la familia (derivada de `SCORE_NAMES`), nunca `starts with`; `booleanValue` = true es un filtro de tipo `boolean` (operador `=`) sobre la dimensión `booleanValue` de `scores-boolean`, que el golden registra.

Todos los widgets llevan el filtro `environment` del producto (lo agrega `build_dashboards`),
leen `min_version = 2` y se ubican en el tiempo por el **fin de la última corrida de la
tarea** (`task_time.end`, el `timestamp` de cada score), nunca por la fecha de merge;
`cycle_h` es donde se lee el tiempo hasta el merge. El eje de versión es `traceRelease`.

La división "por tipo" y "por origen" se resuelve con **familias de nombre de score**, no con
metadata: la sección 4 prueba que la metadata no se puede agrupar ni filtrar y que un score
categórico no parte a uno numérico. Familias nuevas que se piden a `scores`, además de las
del plan: `delivered_cost_usd.type.<tipo>` y `cost_usd.origin.<origen>`; si `type` no tiene
un conjunto cerrado en el dominio, esa división se descarta (fila de Panorama marcada) en
vez de escribir un literal junto al widget. Un score de valor desconocido se omite; ningún
widget filtra `unknown` fuera de un desglose.

| Dashboard | Título | Vista | Medida y agregación | Dimensión / desglose | Filtros | Score o campo nativo | Tiempo |
|---|---|---|---|---|---|---|---|
| Panorama | Costo por PR mergeado (mediana), por tipo | SCORES_NUMERIC | `value` · `p50` | `name` (familia `delivered_cost_usd.type.<tipo>`), `traceRelease` | `environment`; `name` `any of` la familia enumerada | `delivered_cost_usd` (se omite si la tarea no se entregó) | fin de la última corrida |
| Panorama | PRs mergeados por semana de entrega | SCORES_BOOLEAN | `count` · `count` | `traceRelease` | `environment`; `name` = `delivered`; `booleanValue` = true | `delivered` | fin de la última corrida (no la fecha de merge) |
| Panorama | Tiempo de ciclo (h, calendario) | SCORES_NUMERIC | `value` · `p50` | `traceRelease` | `environment`; `name` = `cycle_h` | `cycle_h` | fin de la última corrida |
| Panorama | Devoluciones por tarea | SCORES_NUMERIC | `value` · `avg` | `traceRelease` | `environment`; `name` = `returns` | `returns` | fin de la última corrida |
| Panorama | Plata perdida: costo de fallos | SCORES_NUMERIC | `value` · `sum` | `traceRelease` | `environment`; `name` = `failed_cost_usd` | `failed_cost_usd` | fin de la última corrida |
| Panorama | Plata perdida: escalaciones | SCORES_NUMERIC | `value` · `sum` | `name` (familia `escalations.<clase>`) | `environment`; `name` `any of` la familia enumerada | `escalations.<clase>` | fin de la última corrida |
| Panorama | Gasto: original vs. follow-ups | SCORES_NUMERIC | `value` · `sum` | `name` (familia `cost_usd.origin.<origen>`) | `environment`; `name` `any of` la familia enumerada | `cost_usd.origin.<origen>` | fin de la última corrida |
| Mejora del sistema | Retrabajo por categoría de devolución | SCORES_NUMERIC | `value` · `sum` | `name` (familia `returns.<categoría>`), `traceRelease` | `environment`; `name` `any of` la familia enumerada | `returns.<categoría>` | fin de la última corrida |
| Mejora del sistema | Fallos por razón | SCORES_NUMERIC | `value` · `sum` | `name` (familia `failures.<razón>`), `traceRelease` | `environment`; `name` `any of` la familia enumerada | `failures.<razón>` | fin de la última corrida |
| Mejora del sistema | Roles más caros | SCORES_NUMERIC | `value` · `sum` | `name` (familia `cost_usd.role.<slug>`), `traceRelease` | `environment`; `name` `any of` la familia enumerada | `cost_usd.role.<slug>` | fin de la última corrida |
| Mejora del sistema | Horas activas por versión | SCORES_NUMERIC | `value` · `p50` | `traceRelease` | `environment`; `name` = `active_h` | `active_h` | fin de la última corrida |
| Mejora del sistema | Calidad de la entrega: hallazgos importantes | SCORES_NUMERIC | `value` · `avg` | `traceRelease` | `environment`; `name` = `findings_important` | `findings_important` | fin de la última corrida |
| Mejora del sistema | Calidad de la entrega: follow-ups | SCORES_NUMERIC | `value` · `avg` | `traceRelease` | `environment`; `name` = `followups` | `followups` | fin de la última corrida |
| Mejora del sistema | Calidad de la entrega: chequeos en rojo | SCORES_NUMERIC | `value` · `avg` | `traceRelease` | `environment`; `name` = `checks_red` | `checks_red` | fin de la última corrida |
| Operación | Escalaciones por clase | SCORES_NUMERIC | `value` · `sum` | `name` (familia `escalations.<clase>`) | `environment`; `name` `any of` la familia enumerada | `escalations.<clase>` | fin de la última corrida; rango "7 días" de la UI |
| Operación | Fallos por límite de cuenta, por día | SCORES_NUMERIC | `value` · `sum` | `timestampDay` | `environment`; `name` = `failures.account_limit` | `failures.account_limit` | fin de la última corrida; por día, no por hora |
| Operación | Entregas por día | SCORES_BOOLEAN | `count` · `count` | `timestampDay` | `environment`; `name` = `delivered`; `booleanValue` = true | `delivered` | fin de la última corrida |
| Dónde va la plata | Costo por rol | SCORES_NUMERIC | `value` · `sum` | `name` (familia `cost_usd.role.<slug>`) | `environment`; `name` `any of` la familia enumerada | `cost_usd.role.<slug>` | fin de la última corrida |
| Dónde va la plata | Costo por modelo | OBSERVATIONS | `totalCost` · `sum` | `providedModelName` | `environment` | medida nativa `totalCost` de la vista de observaciones | `start_time` de la observación |

## Widgets descartados

- **Operación: tareas atascadas.** Una tarea atascada es la que *no* tiene score de cierre:
  su ausencia no es una fila que una vista pueda contar, y ninguna vista cuenta "tareas sin
  X". No se reemplaza con un visual inventado.
- **División por hora de los fallos `account_limit`.** El catálogo no tiene dimensión de
  hora (`timestampDay` es el grano más fino; la granularidad de la UI no se guarda). Queda
  por día, y el título de la fila lo dice ("por día").
- **Línea vertical de cambio de versión (Panorama).** No hay anotaciones; el marcador es
  `traceRelease` como desglose de la serie semanal.
- **Semana calendario y "últimos 7 días" como propiedades del widget o del dashboard.** La
  fila no guarda ni granularidad ni rango (sección 3): son instrucciones del README, no
  datos de la definición.
- **"% del gasto en follow-ups"** como cociente: un widget no divide; se muestra el gasto
  de `cost_usd.origin.original` y `cost_usd.origin.followup` en una sola serie.

## Adenda (2026-10-17, GH-283): «Costo por modelo» lee un score por familia de modelo

Tras el backfill y `agentic dashboards apply --verify` del 2026-10-17, `money/cost_by_model`
daba 0 filas en los dos productos. Esta adenda no edita el texto anterior: reemplaza una
fila de la tabla de «Qué lee cada widget».

- **La fila nueva.** «Costo por modelo» pasa a `SCORES_NUMERIC`, `value` · `sum`, desglose
  `name`, con `environment` = el producto y `name` `any of` la familia
  `cost_usd.model.<familia>`; se ubica por `task_time.end`, como los demás.
- **Por qué.** Las generaciones de Claude Code (`claude_code.llm_request`) llevan
  `environment = 'default'`: no se atribuyen a ningún producto, así que el filtro por
  producto nunca las alcanza. Además cubren unos 3.384 USD de los unos 9.200 USD que
  registran los documentos de tarea. El sistema ya registra el costo por modelo de cada
  corrida (`runs[].cost_by_model`); ahora ese dato se escribe como score, con la identidad
  y el tiempo de siempre (`identity(product, task_id, start)`, `task_time.end`).
- **La vista OBSERVATIONS deja de usarse.** Ningún widget la lee. Se quitan su consulta
  de conteo en `widget_data.py` y el brazo `start_time` de `WidgetSpec.placed_by`, y el
  tipo `WidgetSpec.view` se estrecha a las tres vistas de scores; construir un widget con
  otra vista se rechaza. `golden.json` y el catálogo de enums de Postgres no cambian:
  describen el almacén, no nuestros widgets.
- **Contra qué se verifica la cifra.** Contra Σ `cost_by_model` de los documentos, no
  contra `cost_usd`: el score es el costo del intento que entregó y queda por debajo de
  `cost_usd` por el costo de los intentos fallidos (`failed_cost_usd`) y por el residuo que
  ninguna corrida registra.
- **Granularidad.** La familia es `opus`/`sonnet`/`haiku`/`other`, con
  `task_scores.model_family` como único normalizador (el segmento que sigue a `claude-`);
  los ids exactos quedan en el documento. Agregar un miembro está permitido; renombrarlo
  no (ADR-0008).
- **El backfill no necesita código.** `cli._backfill_one` solo salta los documentos
  `not_finished` y `no_time` (y `_scores_backfill`, los `unreadable`); todo otro documento
  terminado re-deriva el conjunto completo de scores con los mismos ids y el mismo
  `task_time.end`. La familia nueva llega a la historia con ids deterministas y sin
  partir el día de ordenamiento.
- **Orden posterior al merge, a cargo de una persona.** (1) Re-fijar stage0. (2)
  `agentic scores backfill --product-dir <dir>` en los dos productos (ADR-0017 lo rechaza
  desde este checkout). (3) `agentic dashboards apply --verify` en los dos productos; su
  salida es el criterio de hecho.
- **Lo que no se toca.** Las generaciones `default` existentes quedan como están: no se
  reescriben ni se reetiquetan, y el colector no cambia.
- **Riesgos.** Un proveedor o familia nuevos caen en `other` hasta que se agregue un
  miembro. Los documentos anteriores al 2026-09-14, que no tienen `cost_by_model`, no
  aportan nada: ese score se omite, nunca se escribe en 0.
