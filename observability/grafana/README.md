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

`dashboard.json` es el dashboard "Sistema agéntico", exportado con el datasource como
input. Importarlo desde **Dashboards → New → Import**, pegando el JSON y eligiendo el
datasource Prometheus del stack (`grafanacloud-<stack>-prom`). Re-importar pisa el anterior:
comparten `uid`.

Los nombres en Prometheus son los de OpenTelemetry con puntos por guiones bajos y el sufijo
de unidad: `agentic.task.cost_usd` es `agentic_task_cost_usd`, `agentic.task.duration` es
`agentic_task_duration_seconds`, `agentic.task.finished` es `agentic_task_finished`. Labels:
`task_type`, `task_status`, `system_version`, `prompts_fingerprint`, `role`, `role_outcome`,
`model`, `rework`.

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
