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
de unidad: `agentic.task.cost_usd` es `agentic_task_cost_usd_sum` y `_count`,
`agentic.task.duration` es `agentic_task_duration_seconds_bucket`, el contador
`agentic.task.finished` es `agentic_task_finished_total`. Labels: `task_type`,
`task_status`, `system_version`, `prompts_fingerprint`, `role`, `role_outcome`, `model`,
`rework`.

## Cómo viajan las métricas

El sistema emite deltas: cada `agentic run` es un proceso nuevo y un contador acumulado
que arranca en cero se lee mal en Prometheus cuando una tarea cuesta más que la anterior.
El collector acumula (`deltatocumulative`, estado en memoria, `max_stale` de 90 días) y
manda una sola serie por combinación de labels. Dos consecuencias: los paneles con
`increase()` muestran valor a partir de la **segunda** tarea en el rango, y reiniciar el
collector reinicia la acumulación, lo que Prometheus lee como un reset de contador: la
primera tarea después del reinicio puede contarse de menos. No es un problema para
comparar semanas; sí lo sería para contabilidad exacta, que no es el objetivo.

## Ver si llega

**Explore → Metrics**, consulta `agentic_task_finished_total`: aparece un punto por tarea
corrida desde que el collector tiene las credenciales. **Explore → Traces**, filtro
`service.name = agentic-system`: un trace por tarea, con los roles anidados y sin los spans
de Claude Code, que se quedan en Langfuse.
