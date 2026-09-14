---
status: accepted
date: 2026-09-13
---

# El sistema se divide en módulos bajo `core/`, cada uno hexagonal, con `process` como dominio central

ADR-0007 dejó tres capas planas (`domain/`, `application/`, `adapters/`) y ADR-0009
descartó dividir `domain/` en subdominios "hasta que el `domain/` único duela". Esta
decisión revierte ese punto antes de que duela, a propósito: el paquete pasa a
`agentic/core/<módulo>/` con tres módulos, cada uno con las capas de ADR-0007 adentro y
solo las que tiene contenido, más `agentic/shared/` para lo que todos leen y nadie posee.

| Módulo | Qué es | Importa |
|---|---|---|
| `shared/` | `Configuration` | Nada del proyecto |
| `core/process/` | Roles y prompts, la tarea y su documento, reglas, casos de uso, ports, grafo, harness, GitHub | `shared` |
| `core/knowledge/` | Conocimiento de producto y vocabulario de flags; implementa `ProductKnowledge` y `FlagVocabulary` | `shared`, `process` |
| `core/measurement/` | Métricas del sistema y spans OTel; implementa `Telemetry` | `shared`, `process` |
| raíz | `composition.py`, `cli.py` | Todo |

Los motivos, en orden: que los agentes que trabajan sobre este repo naveguen un árbol donde
cada rol nuevo ya sabe dónde cae, y que los doce roles que faltan no crezcan sobre un
`domain/` de un solo directorio. El tamaño de hoy (716 líneas de dominio, 39 de medición)
no lo justifica solo; se acepta que `knowledge` y `measurement` sean módulos chicos por un
tiempo.

Tres decisiones dentro de esta:

**`process` es el dominio central y no importa a nadie.** `knowledge` y `measurement`
son de soporte: pueden importar el dominio y la aplicación de `process`, nunca sus
adaptadores, y no se conocen entre sí. Lo que `process` necesitaba de los otros pasa por
ports que `process` define y ellos implementan. Eso movió tres cosas: `approval_check` y
`distillation` dejan de ser funciones que la aplicación importa de `knowledge` y entran al
port `ProductKnowledge` como `approval_check(task_type)` y `distill(verdict)`;
`describe_flags` (cómo se ve el vocabulario en un prompt) es de `process.domain.roles`, y
el vocabulario en sí de `knowledge`; la telemetría entra al grafo y al runner del SDK por
un port `Telemetry` en `process/adapters/telemetry.py`, con `NoTelemetry` por defecto,
que `measurement/adapters/otel` implementa y `composition` inyecta. El mismo movimiento
que ADR-0007 hizo con LangGraph y el SDK.

**`Configuration` va a `shared/`; `TaskState` se queda en `process`.** La configuración
la leen los tres módulos y ninguno es su dueño. El estado de la tarea es el agregado del
proceso (ADR-0009); `measurement` lo importa para calcular retrabajo, no lo comparte.

**Un glosario por módulo.** `CONTEXT.md` de la raíz se parte en
`agentic/core/<módulo>/CONTEXT.md` y `CONTEXT-MAP.md` en la raíz los enumera y dice cómo se
relacionan. Corrida y Corrida de rol quedan en Proceso, porque son registros del estado de
la tarea; Retrabajo, Métrica del sistema y Set de evaluación en Medición; Destilado,
Capacidad y el nuevo **Vocabulario de flags** en Conocimiento de producto.
`tests/test_language.py` exige que cada término mapeado exista en el glosario del módulo
donde vive su identificador. Los ADRs siguen en `docs/adr/` como decisiones del sistema;
un módulo tendrá `docs/adr/` propio cuando tenga una decisión que solo le importe a él.

## Opciones descartadas

- **Esperar a que duela** (lo que ADR-0009 decidió): con QA Lead y `Route` el `domain/`
  plano se llena de una vez de roles, tabla de devoluciones y ruteo; partirlo en ese
  momento es partir código que recién se escribió.
- **Módulos por rol o por tramo del camino**: los roles comparten `TaskState`, `rules` y
  el documento; cortar ahí duplica el núcleo.
- **Módulos más finos** (`intake` para la bandeja, `execution` para el harness): recién
  cuando la bandeja o el harness tengan una segunda implementación.
- **Dependencias libres entre dominios de módulos**: `process ↔ knowledge` era un ciclo
  desde el día uno; la regla con dirección es lo que hace que el corte signifique algo.
- **Excepción para que los adaptadores importen adaptadores ajenos** (el grafo importando
  `otel`): más simple, pero la regla queda con asterisco de entrada.
- **`telemetry` en `shared/`**: deja a `measurement` con 39 líneas y convierte OTel en
  infraestructura común cuando es la implementación de un port.
- **Un solo `CONTEXT.md` con un `CONTEXT-MAP.md` que apunte a secciones**: si los
  módulos valen, vale que cada uno tenga su lenguaje.
- **Editar ADR-0009**: los ADRs aceptados no se editan; se superseden.

## Consecuencias

- Reemplaza el "Paquete" de README §14 y el punto "Dividir `domain/` en subdominios" de
  las opciones descartadas de ADR-0009. README §13 lo registra.
- `tests/test_architecture.py` vigila las dos reglas sobre los imports: la de capas dentro
  de cada módulo (ADR-0007) y la de dirección entre módulos. Los tests espejan el árbol:
  `tests/core/<módulo>/<capa>/`, `tests/shared/`, `tests/llm/`.
- Cada rol nuevo suma su `Role`, prompt y esquema a `process/domain`, su caso de uso a
  `process/application` y un nodo al grafo; nada en `knowledge` ni `measurement` cambia
  por un rol más.
- `ADR-0007` sigue vigente, ahora dentro de cada módulo. `langgraph.json` sigue exportando
  `agentic.composition:graph`.
