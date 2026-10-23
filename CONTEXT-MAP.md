# Context Map

Un equipo de desarrollo modelado como 19 roles agénticos más un humano, que mantiene y
hace crecer el producto que recibe como parámetro. El sistema se divide en tres contextos
bajo `agentic/core/`, cada uno con su glosario; el proceso está en `README.md` y las
decisiones con trade-off en `docs/adr/` (ADR-0010).

## Contexts

- [Proceso](./agentic/core/process/CONTEXT.md): los roles, la tarea y su documento, el
  camino y sus reglas, la corrida de cada rol. El dominio central.
- [Conocimiento de producto](./agentic/core/knowledge/CONTEXT.md): lo destilado y vigente
  sobre el producto, dónde vive en su repo y qué debe dejar una aprobación.
- [Medición](./agentic/core/measurement/CONTEXT.md): las métricas del sistema sobre las
  corridas y las trazas que las llevan.

## Relationships

- **Proceso → Conocimiento de producto**: el proceso define qué necesita del conocimiento
  (leerlo entero, validar una aprobación, destilar un veredicto) y el conocimiento lo
  provee. El conocimiento conoce al proceso; el proceso no conoce al conocimiento.
- **Medición → Proceso**: la medición lee las corridas de rol y el estado de la tarea que
  el proceso registra, y abre y cierra las trazas que el proceso le pide. El proceso no
  conoce la medición.
- **Conocimiento de producto ↔ Medición**: no se conocen.
- **Compartido**: la configuración del sistema (`agentic/shared/`), que los tres leen y
  ninguno posee.
