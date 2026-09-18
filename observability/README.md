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

- Un span por tarea y uno por rol. El span de rol sigue las convenciones semánticas GenAI
  de OpenTelemetry (ADR-0008, adenda): se llama `invoke_agent <rol>`, con
  `gen_ai.operation.name=invoke_agent`, `gen_ai.agent.name` (`Role.name`) y
  `gen_ai.agent.id` (`Role.slug`). Lo que la convención no nombra queda bajo `agentic.`:
  costo, retrabajo, versión de la entrega, modelo, turnos, `session_id`, hash del
  prompt y categorías de devolución (`agentic.role.cost_usd`, `.rework`, `.version`,
  `.model`, `.turns`, `.session_id`, `.prompt_hash`, `.return_categories`). `product`,
  `role` y `role.outcome` quedan sin prefijo: ya son el mismo nombre en la métrica. El
  collector borra `task.title` antes de mandarlo.
- Las métricas del sistema (`agentic/core/measurement/CONTEXT.md`): `agentic.task.cost_usd`,
  `agentic.task.rework_usd`, `agentic.task.duration`, `agentic.task.finished`,
  `agentic.task.returns`, `agentic.role.cost_usd`, `agentic.role.duration`. Labels:
  `product` en todas, `task.type`, `task.status`, `system.version`, `prompts.fingerprint`,
  `role`, `role.outcome`, `model`, `rework`, y `return.category` en `agentic.task.returns`,
  que deja una muestra por categoría de devolución (`none` con 0 si no hubo; ADR-0016). Son
  eventos: una muestra por tarea terminada y por corrida de rol, exportada al cerrar
  (ADR-0008, adenda), nada se acumula entre corridas.
  Un span siempre cierra con sus atributos: si un
  nodo lanza fuera del manejo de fallo del caso de uso, el rol queda `role.outcome=failed`
  y la tarea `task.status=crashed`, un valor que solo existe en telemetría, nunca en el
  estado.

El `product` sale de lo que el producto declara en su `.agentic/stack.yaml`, no del nombre
del directorio ni del remoto de git: desde ADR-0017 hay dos productos, y un panel que los
suma compara un SaaS de gimnasios con un sistema agéntico (ADR-0008, adenda; issue #47).

Los spans nativos de Claude Code llevan prompts, respuestas y tool calls: se quedan en
Langfuse. Si algún día hace falta verlos en Grafana, se cambia el filtro del collector, no
el sistema.

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
