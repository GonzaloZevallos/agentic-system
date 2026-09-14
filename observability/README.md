# Observabilidad

Dos herramientas a distinta altitud (ADR-0008), un solo collector:

```
Claude Code ──┐                      ┌──> Langfuse local     (todas las trazas, con prompts)
              ├──> collector :4318 ──┤
sistema ──────┘                      └──> Grafana Cloud      (trazas y métricas del sistema)
```

| Directorio | Qué es |
|---|---|
| `collector/` | La config del OpenTelemetry Collector: recibe todo, filtra por `service.name` y reparte |
| `langfuse/` | El `docker-compose` de Langfuse. También levanta el collector, porque comparte su red |
| `grafana/` | El dashboard del sistema y cómo conectar la instancia de Grafana Cloud |

## Qué sale a la nube

Solo lo que emite el sistema, identificado como `service.name: agentic-system`:

- Un span por tarea y uno por rol, con metadatos: id de tarea, tipo, estado, costo,
  retrabajo, devoluciones, modelo, turnos, `session_id`, hash del prompt, versión del
  sistema. El collector borra `task.title` antes de mandarlo.
- Las métricas del sistema (CONTEXT.md, "Medición"): `agentic.task.cost_usd`,
  `agentic.task.rework_usd`, `agentic.task.duration`, `agentic.task.finished`,
  `agentic.task.returns`, `agentic.role.cost_usd`, `agentic.role.duration`. Labels:
  `task.type`, `task.status`, `system.version`, `prompts.fingerprint`, `role`,
  `role.outcome`, `model`, `rework`.

Los spans nativos de Claude Code llevan prompts, respuestas y tool calls: se quedan en
Langfuse. Si algún día hace falta verlos en Grafana, se cambia el filtro del collector, no
el sistema.

## Levantar

```bash
cd observability/langfuse
docker compose up -d
```

Requiere en `observability/langfuse/.env` (no versionado): `LANGFUSE_OTEL_BASIC` y los
tres de Grafana Cloud (`grafana/README.md`). Sin ellos el collector no arranca.

En el `.env` de la raíz, las `OTEL_*` del `.env.example` apuntan al collector. El sistema
solo exporta si `OTEL_EXPORTER_OTLP_ENDPOINT` está seteada.
