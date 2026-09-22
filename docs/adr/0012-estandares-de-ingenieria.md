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

## Adenda (2026-09-17): el camino de platform enhancement existe

Desde #46 `classify` ya no escala un platform enhancement: va al plan. La mitad de la
segunda opción descartada ("por el sistema no se puede") caducó; la decisión se sostiene
por la otra mitad, que mezclar refactor con feature contamina la medición. Migrar el v0
por el sistema es ahora una tarea de plataforma como cualquier otra, medida aparte.

## Adenda (2026-09-22): el default son prácticas, no herramientas; la materialización es del producto

El default dejó de asumir Python. `agentic/core/knowledge/domain/CODING_STANDARDS.md`
ya no dice "HTTP", "throwaway database", "type hints" ni nombra `pytest`: son las ocho
prácticas (hexagonal, DDD táctico con el glosario, SOLID, patrones que nombran un
problema, tests en las costuras que el plan acuerda, tipado estricto sin silenciar,
observabilidad, seguridad y performance), y **cómo** se cumple cada una en un stack
concreto vive en el `CODING_STANDARDS.md` de ese producto, que lo reemplaza entero
(el mecanismo no cambia: sólo lo que el default trae). Un test de propiedad
(`tests/core/knowledge/domain/test_standards.py`) guarda esto sobre el texto
ensamblado, nunca sobre los bytes del archivo: un reescrito de prosa sin cambio de
comportamiento no debe romperlo (§5).

**Observabilidad entra como práctica, en dos obligaciones, nunca como afirmación de
hecho.** Primero, continuidad: código que cambia una superficie observable la deja
observable, así que una señal que un rol o un operador ya lee sigue leyendo verdad
después del cambio. Segundo, plan: una capacidad nueva dice qué va a emitir y dónde, o
dice "nada todavía" a propósito, para que el vacío sea una decisión y no un
descubrimiento tardío. Deliberadamente **no** dice "el código nuevo emite métricas":
esa forma más fuerte es la que dejó "métricas vivas el día del ship" vinculante en un
repo sin infraestructura de métricas (adenda de ADR-0014) y choca con el contrato de
cardinalidad cerrada de ADR-0008 (un conjunto cerrado de labels, nunca texto libre) que
ningún Developer de gym-saas puede leer. Un hallazgo de observabilidad es
`standard-other`: no agrega valor a la lista cerrada de `FindingCategory`
(`schemas.py`) ni un ejemplo de categoría a `tech_lead_review.md`; abrir una categoría
propia queda para su propio platform enhancement, que toque ambos archivos juntos
(ADR-0016 ya fija esos términos: `standard-other` es la salida y llenarla es lo que
mueve la huella).

**Quién cumple la práctica hoy, y cómo.** Este repo, a través de `core/measurement` y
las reglas de nombrado de ADR-0008 (labels de cardinalidad cerrada, sin texto libre);
gym-saas, por no tener telemetría todavía y por lo tanto nada que mantener observable
— la frase del plan ("nada todavía", dicha a propósito) es toda su obligación hasta que
la tenga.

**Esto reemplaza el bullet de ADR-0017** que hacía leer a este repo el default del
sistema "porque describe bien lo que este repo ya hace": era cierto mientras el default
hablaba de una costura HTTP y de tipado con "type hints", que es exactamente lo que
este repo tiene. Deja de serlo en el momento en que el default se vuelve agnóstico de
stack: un archivo que ya no nombra la costura ni el tipado de este repo no puede seguir
describiéndolo por default, así que este repo pasa a tener su propio
`CODING_STANDARDS.md` de raíz, que nombra mypy, pytest y ruff y apunta al registro
(`[tool.mypy] files`, ADR-0021) en vez de restatearlo.

**Línea de README §13, verbatim, para quien escriba `README.md`** (esta subtarea no lo
abre; la subtarea `prompts` sí, ADR-0012 §13 "Decisiones que fueron reemplazadas"):
*ADR-0012's addendum replaces the ADR-0017 bullet that had this repo reading the system
default because it described what the repo already does; from here the default is
practices and this repo materialises them in its own `CODING_STANDARDS.md`.*

**Este archivo no es parte de `prompts.fingerprint`.** Los prompts de
`agentic/core/process/domain/prompts/` no se tocan; lo que seis roles con
`reads_standards=True` leen sobre este repo cambia igual, y sólo `system.version`
marca el cambio en la medición.
