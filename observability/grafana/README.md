# Grafana Cloud

Métricas y trazas del sistema, a nivel negocio: qué cuesta una tarea terminada y cuánto de
eso es retrabajo, comparado entre versiones del sistema (ADR-0008). Instancia:
https://heartyfjord3250.grafana.net/. Las trazas duran 30 días; las métricas, 13 meses.

## Conectar

Tres valores, en `observability/langfuse/.env` (no versionado), que lee el collector:

```
GRAFANA_OTLP_ENDPOINT=https://otlp-gateway-<region>.grafana.net/otlp
GRAFANA_OTLP_INSTANCE_ID=<número>
GRAFANA_OTLP_TOKEN=<token>
```

Se sacan en el portal de Grafana Cloud (https://grafana.com/orgs/<org>), no en la instancia:
**Stack → Configure → OpenTelemetry**. Ahí están el endpoint y el instance ID, y el botón
**Generate now** para un token con el scope `metrics:write` y `traces:write`. Después:

```bash
cd observability/langfuse && docker compose up -d --force-recreate otel-collector
```

## Dashboard

El dashboard arranca con **un solo producto** seleccionado en la variable `Producto`:
desde ADR-0017 hay dos y sumarlos no significa nada. Elegir "All" los compara, no los suma,
porque todo panel abre por `product` (issue #47).

`dashboard.json` es el dashboard "Sistema agéntico" y, a la vez, el cuerpo del POST que
lo publica: la variable de plantilla `DS_PROMETHEUS` (oculta, `hide: 2`) resuelve su
propio `${DS_PROMETHEUS}` en todo panel y variable. Pegar este JSON en
**Dashboards → New → Import** **no funciona**: el archivo ya no lleva `__inputs` (se
retiró con ADR-0032) y la variable está oculta, así que el importador no pregunta nada
y el dashboard queda publicado sin datasource — la v8 (GH-123). El único camino de
publicación es el script:

```bash
uv run scripts/publish_dashboard.py
```

Lee tres variables de entorno, nombradas acá sin valor (nunca en este repo, y no en
`.env.example`: ese archivo es lo que el runtime bajo `agentic/` lee, y esto no lo lee):
`GRAFANA_URL` (la instancia, `https://heartyfjord3250.grafana.net`), `GRAFANA_TOKEN`
(un token de esa instancia con permiso de publicar dashboards) y `GRAFANA_DS_UID` (el
uid del datasource Prometheus del stack). Antes de publicar comprueba `GET
/api/datasources/uid/$GRAFANA_DS_UID` — corta sin publicar nada si no es 200 — y de ahí
saca el nombre visible del datasource; publica con `POST /api/dashboards/db`, cuerpo
`{"dashboard": <json sin "id">, "overwrite": true}`; después vuelve a leer el dashboard
publicado y corta si queda alguna referencia a `${DS_PROMETHEUS}` sin resolver. Ninguna
ruta del script termina en 0 sin las dos verificaciones.

`editable: false` hace del archivo la fuente de verdad: el único loop es editar
`dashboard.json` y correr el script, nunca editar el dashboard vivo; la exploración ad
hoc va a **Explore**, no a los paneles. Elegir un producto sin línea de base (hoy
`gym-saas`) deja vacía, a propósito, toda fila con eje de línea de base: ese producto no
tiene ninguna serie con `system_baseline`; la lectura de la historia pre-baseline es
`agentic eval compare`, no este dashboard.

Los nombres en Prometheus son los de OpenTelemetry con puntos por guiones bajos y el sufijo
de unidad: `agentic.task.cost_usd` es `agentic_task_cost_usd`, `agentic.task.duration` es
`agentic_task_duration_seconds`, `agentic.task.finished` es `agentic_task_finished`. Labels:
`product` en todas, `task_type`, `task_status`, `system_version`, `prompts_fingerprint`,
`role`, `role_outcome`, `model`, `rework`. Las series anteriores a #47 no llevan `product`
y ningún panel las ve: se agregan labels, no se reescribe la historia (ADR-0008).

Los paneles de "Entregas por resultado" y "Líneas humanas por versión del sistema"
(`agentic.delivery.*`, emitidas por `agentic close --issue N`) leen `system_version`
del documento de la tarea, no de la corrida que está mirando el dashboard: un
documento escrito antes de esta tarea no tiene esa clave, y `close` lo normaliza al
único literal `"unknown"`. La variable `$version` sale de `label_values(agentic_role_
cost_usd, system_version)`, que nunca ofrece `"unknown"` porque esa métrica siempre
trae una versión real — esas muestras solo aparecen eligiendo `All`.

## Cómo viajan las métricas

Son eventos, no tasas (ADR-0008, adenda): cada tarea terminada y cada corrida de rol deja
**una muestra** por métrica, con su valor, exportada en el momento. Nada se acumula entre
corridas, ni en el sistema ni en el collector. Los paneles suman muestras en el rango
(`sum_over_time`, `count_over_time`), así que la primera tarea de una versión ya cuenta.
Nunca usar `increase()` o `rate()` sobre estas series: no cuentan la primera muestra y
extrapolan entre muestras aisladas.

`task_status=crashed` existe solo en telemetría: la corrida rompió fuera del manejo de
fallo. Los paneles de "terminadas" lo excluyen junto con `waiting_on_human`.

## Ver si llega

**Explore → Metrics**, consulta `agentic_task_finished`: aparece un punto por tarea
corrida desde que el collector tiene las credenciales. **Explore → Traces**, filtro
`service.name = agentic-system`: un trace por tarea, con los roles anidados y sin los spans
de Claude Code, que se quedan en Langfuse.
