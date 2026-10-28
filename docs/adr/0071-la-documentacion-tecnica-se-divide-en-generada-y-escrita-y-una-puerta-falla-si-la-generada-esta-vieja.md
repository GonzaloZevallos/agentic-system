---
status: accepted
date: 2026-10-28
---

# La documentación técnica se divide en generada y escrita, y una puerta falla si la generada está vieja

GH-398. Hoy el Tech Lead sólo planifica ADRs y ningún repo tiene un diagrama. El dueño quiere
que cada producto lleve documentación técnica con UML que explique cómo funciona, y que el
sistema la mantenga al día a medida que se cierran los issues. La investigación del
2026-10-27 (DeepWiki, Google Code Wiki, Factory Wiki, Mintlify Autopilot, Swimm,
claude-code-action) apunta a lo mismo: generación determinista para lo que se deriva del
código, prosa revisada para el resto, y una puerta que falla cuando la doc está vieja.

## Decisión

- **Dos clases de documento, ambas en Mermaid, en Markdown, en el repo del producto.** GitHub
  renderiza Mermaid, así que no hay sitio todavía. Todo aterriza en el pull request de la
  tarea y se revisa con él: nada generado por un LLM llega a master sin revisión.
  - **Generada**: se deriva del código con el comando `generate` del producto, nunca se edita
    a mano y vive bajo `docs/architecture/generated/`.
  - **Escrita**: vistas C4 de contexto, de contenedores y de componentes, y diagramas de
    secuencia de los flujos clave, bajo `docs/architecture/`. La planifica el Tech Lead y la
    escribe un Developer en la subtarea que cambia el límite, igual que hoy con los ADR. El
    Tech Lead nunca escribe archivos.
- **El contrato `docs:` del stack.** Un bloque opcional de `.agentic/stack.yaml` con
  `generate` (el comando que regenera todo artefacto generado) y `paths` (las rutas que ese
  comando posee, como globs). Declararlo agrega el chequeo reservado `docs`, que el sistema
  construye y que ningún chequeo del producto puede llamarse igual (falla al cargar). El
  chequeo corre `generate`, luego `git status --porcelain --untracked-files=all` sobre las
  rutas, y falla con salida 1 si `generate` sale distinto de cero o si queda cualquier
  cambio o archivo sin seguimiento: un archivo sin seguimiento bajo una ruta poseída cuenta
  como viejo. En toda salida, pase o falle, el propio chequeo deja las rutas como las tiene
  HEAD (restaura lo seguido y borra lo sin seguimiento). Se suma a `checks` y, cuando el
  producto declara `delivery_checks`, también al nivel de la puerta de entrega, así que una
  doc generada vieja bloquea la entrega como un test que falla. Sólo
  `agentic/core/knowledge/domain/stack.py` construye el nombre y el comando.
- **Qué significa «se suma a `ci:`».** Los nombres de `ci:` son ids de jobs de GitHub que
  ninguna clave del stack extiende. El chequeo es local y de la puerta; el producto que lo
  quiere en CI agrega un paso al workflow en su propio issue de adopción.
- **Validación de `paths` (estándar 8).** `generate` no alcanza nada nuevo: se lee en la
  base, como `checks`, y corre en la misma jaula. `paths` sí es nuevo, y la restauración es
  destructiva dentro de esas rutas, así que cada entrada se valida al cargar. Se rechaza la
  que es absoluta, tiene un segmento `..`, empieza con `:`, tiene prefijo literal vacío (lo
  anterior al primer `*?[`) o tiene un prefijo literal que se superpone con `docs/adr`,
  `.agentic` o `.git` en cualquiera de los dos sentidos (por eso `docs/` y `docs/**` se
  rechazan). Las vistas escritas y los ADR quedan fuera de las rutas poseídas por
  construcción, así que la restauración nunca los toca.
- **Rechazo con `parallel_checks`.** El cargador falla cuando `parallel_checks: true` y algún
  comando declarado de `checks`, `affected_checks` o `rerun_checks` contiene el prefijo
  literal de una ruta poseída: ese chequeo leería las rutas mientras `docs` las restaura.
- **Todo el texto en español rioplatense; el código, los identificadores y los nombres dentro
  de los diagramas, en inglés**, como en el código. **Cada afirmación de la doc técnica cita
  el archivo o el símbolo en que se apoya.**
- **Planificación.** Cuando la tarea cambia la arquitectura (un contenedor, un componente, un
  contrato entre módulos, el modelo de datos o un flujo clave), el plan incluye actualizar
  `docs/architecture/` y las secciones técnicas de `docs/README.md` en la subtarea que hace
  el cambio. Sólo cuando el producto declara `docs:`.
- **Revisión y severidades.**
  - Un límite cambiado en el código sin cambio bajo `docs/architecture/` o `docs/README.md`
    en la misma subtarea es **bloqueante**; la evidencia es la línea citada más un
    `git diff --stat <base> -- docs/architecture docs/README.md` vacío.
  - Una afirmación sin referencia a un archivo o símbolo, y la prosa que no está en español,
    son `important`, y se juzgan sólo sobre las líneas que el diff agrega o cambia.
  - Todo lo demás de un documento que el diff toca es `outside`.
  - Que falte `docs/README.md` nunca es bloqueante por sí solo.
- **`docs/README.md` («Cómo funciona») es el índice de la documentación** de un producto: qué
  hace, capacidades, arquitectura, referencia de API o CLI, ADR. El Tech Lead mantiene al día
  sus secciones técnicas.
- **Conflictos.** Todo texto que manda a un rol a resolver conflictos en archivos bajo
  `docs.paths` dice que corra `generate` y haga commit de su salida, nunca que los fusione a
  mano.
- **Alcance de esta tarea.** Sólo el contrato: no hay generador, ni dependencia nueva, y este
  repo no crea `docs/README.md` ni `docs/architecture/`. Los productos lo adoptan en sus
  propios issues, después del merge (apostrophe y este mismo repo). Esta tarea no se rige por
  su propio contrato, porque el stack se lee en la base.
- **Sin `docs:` nada cambia**: los chequeos, los prompts renderizados, el texto de
  integración y el de conflicto de aterrizaje quedan idénticos byte a byte.

## Riesgos conocidos

- **El generador debe ser determinista, y eso es deber del producto**: el sistema no puede
  comprobarlo. Un Flake del chequeo `docs` significa un generador no determinista.
- **Una carrera con `parallel_checks`.** Un chequeo que lee las rutas poseídas sin nombrarlas
  en su comando no se detecta al cargar y puede correr contra la restauración.
- **Un override de prompt del producto que quita los marcadores** pierde las reglas de la doc
  escrita, pero no la puerta sobre lo generado, que sigue aplicando.
- **La doc escrita sólo está tan al día como la mantiene la revisión.** No hay una puerta que
  compare un diagrama escrito con el código.
