---
status: accepted
date: 2026-10-22
---

# Un documento de criterios por familia de roles

Los criterios con que un rol trabaja estaban repartidos: los estándares de ingeniería en
`CODING_STANDARDS.md`, el producto en el Brief y `docs/product/`, y las reglas de diseño
dispersas en `docs/product/` (en apostrophe, `icon-and-text-rule.md` y `design-capture.md`).
UX Lead y UI Lead no leían conocimiento del producto, así que no tenían fuente de criterios.
Decisión del dueño (2026-10-21): un documento de criterios por familia de roles, en el
repo del producto; una decisión humana o un aprendizaje va al documento de la familia a la
que sirve, no a un archivo compartido; tope de unas 300 líneas por documento.

## Decisión

- **Tres familias.** `Role.criteria` declara las familias de cada rol:
  - `product`: el lado PM (PM y Product Analyst), más los diseñadores, que conservan el
    conocimiento del producto. Brief y `docs/product/`. El PjM no lee criterios.
  - `engineering`: Developer, Tech Lead y el integrador. `CODING_STANDARDS.md`.
  - `design`: UX Designer, UX Lead, UI Designer y UI Lead. `DESIGN_STANDARDS.md`, que se
    entrega entero.
- **La lista vive en `process/domain/roles.py`.** El módulo de conocimiento importa `roles`
  (para validar las etiquetas `roles:` del índice); el camino inverso cerraría un ciclo. La
  decisión de entero o por índice (`whole_for`, `reads_by_index`) vive ahí también.
- **El tope.** `knowledge.CRITERIA_MAX_LINES = 300` y `knowledge.criteria_over_cap`, que
  cuenta las líneas de cada documento por separado (nunca suma) y devuelve un mensaje por
  cada uno que lo pasa. Es una advertencia: nunca rechaza una corrida ni reescribe un
  archivo. Se mide en la cabeza de la rama de la tarea (cuerpo del PR y documento de la
  tarea) y en `HEAD` en el preflight; nunca sobre un archivo ausente, borrado o que sea el
  default del sistema.
- **Producto o sistema.** Un criterio que vale para cualquier producto ("partir las tareas
  grandes") va a los prompts del sistema, por issue. Uno propio del producto va a sus
  documentos.
- **Sin escritor del sistema.** Ningún código escribe `CODING_STANDARDS.md` ni
  `DESIGN_STANDARDS.md`: los escribe el dueño del producto. Pasado el tope, el sistema avisa;
  dividir el documento por áreas o recortarlo lo decide el dueño.
- **Sin default de diseño.** A diferencia de los estándares de ingeniería, el sistema no trae
  un `DESIGN_STANDARDS.md`: un producto sin el archivo tiene un bloque de diseño vacío y sus
  bytes de contexto no cambian.

## Desvío respecto de la decisión del dueño

La decisión dice "indexado por área, por el índice de conocimiento". El diseño tiene su
área (`design` en `AREAS`, y `area_of` le asigna el archivo) pero se entrega **entero**, sin
partir en un índice: ningún rol lo leería por archivos, y el tope de 300 líneas acota su
costo. Si la entrega entera deja de caber, partir el archivo en `.agentic/index/design/` es
lo primero que hay que sumar. `build_index` solo lista áreas con entradas, así que el área
vacía no cambia ninguna salida.

## Acciones humanas

- Mover las reglas de diseño de apostrophe a su `DESIGN_STANDARDS.md`, con un issue de
  apostrophe. Este repo no lo hace.
- Extender `rules.guarded_path` a `DESIGN_STANDARDS.md`: issue de seguimiento.
