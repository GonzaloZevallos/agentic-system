---
status: accepted
date: 2026-09-13
---

# Los estándares de ingeniería son conocimiento con default del sistema, y los hace cumplir un test

El humano pide que el código que el sistema produce siga arquitectura hexagonal, DDD
táctico, SOLID y patrones de diseño. El sistema en sí ya lo hace (ADR-0007, 0009, 0010);
lo que faltaba era que Tech Lead y Developer lo exijan en el producto. Y el v0 de gym-saas
es plano: routers, models, schemas y db sin capa de dominio ni ports.

**Los estándares viven donde vive el conocimiento.** Mismo patrón que el vocabulario de
flags: un default del sistema en `core/knowledge/` y un archivo del producto que lo
reemplaza entero, `CODING_STANDARDS.md` en la raíz, junto a `CONTEXT.md`. Ese nombre no es
capricho: la skill `code-review` (ADR-0013) busca estándares documentados en el repo y lo
nombra explícitamente. Tech Lead y Developer lo leen entero, como PM lee el conocimiento
de producto. El prompt dice "cumplí los estándares"; el archivo dice cuáles, así un
producto en otro lenguaje cambia el archivo y no el sistema, y cambiar el estándar no
cambia la huella de prompts que compara corridas.

**El default son seis reglas.** Hexagonal con test de arquitectura; DDD táctico con el
lenguaje de `CONTEXT.md` obligatorio en nombres de código y tests; SOLID; patrones solo
cuando nombran un problema real, y Tech Lead nombra el patrón y el problema en el plan;
tests unitarios del dominio sin infraestructura más tests de comportamiento en la costura
HTTP; tipado estricto y linter limpios. Nada de CQRS ni event sourcing hasta que una tarea
lo pida.

**Aplican al código nuevo y al que la tarea toca.** El resto del v0 queda plano hasta que
un platform enhancement lo migre. Es medible (§9 mide tareas, no refactors) y es la regla
que vale para cualquier producto que ya exista. Deja una deuda explícita, el dashboard
hexagonal conviviendo con routers planos, y Tech Lead escribe el ADR del producto que la
asuma.

**Tres capas de cumplimiento, una dura.** Developer lo aplica; Tech Lead lo revisa y una
violación es devolución técnica, que cuenta para el límite; y el test de arquitectura del
producto es la única regla que no depende del humor del modelo. Si el producto no lo
tiene, la primera subtarea de Tech Lead es crearlo.

## Opciones descartadas

- **Solo en los prompts**: un estándar que vive en un prompt se erosiona y no se puede
  cambiar por producto.
- **Refactorizar el v0 antes de la primera corrida**, a mano o por el sistema: a mano
  cuesta tiempo humano que el sistema existe para ahorrar; por el sistema no se puede,
  el camino de platform enhancement escala en `classify`. Y mezclar refactor con feature
  contamina la medición.
- **Solo revisión, sin test**: la revisión es un juicio; el test es un hecho.
