---
status: accepted
date: 2026-10-23
---

# Tech Writer escribe y revisa los textos

Tech Writer estaba diseñado en el README (independiente, convocado por el flag "toca textos",
último de la etapa de definición) pero no existía: ni prompt ni nodo. Y la regla que el README
le daba, "no valida la implementación", dejaba los textos que Developer escribe sin nadie que
los compare con los que Tech Writer escribió ni con los criterios del producto. Decisión del
dueño (2026-10-23): el rol se implementa, escribe en la definición y revisa los textos del
diff después de la implementación, con hallazgos puntuados como los de los demás revisores;
un importante se corrige dentro de la tarea (ADR-0053). Lee los criterios de escritura del
propio producto (agentic-system#295).

## Decisión

- **Convocatoria.** No se agrega ningún flag: se usa `touches_copy`, que ya está en
  `flags.yaml`. `rules.copy_applies(state)` es su único lector y pide `type == "feature"` y el
  flag. Bug y platform enhancement quedan afuera. Un producto cuyo vocabulario de flags no
  declara `touches_copy` nunca convoca al rol, y no se avisa.
- **Definición.** `design_step` gana un último escalón, `"write_copy"`, después de la
  consolidación de PM o, si la rama de diseño no aplica, directamente. La guarda de entrada
  pasa a ser `design_applies or copy_applies`. Está pendiente si no hay una corrida de
  `Tech Writer` más nueva que la última de Product Analyst y que la última de
  `PM: consolidation`. PM no consolida los textos en el PRD: la fuente es la sección
  `Tech Writer`, que leen el plan, Developer, el integrador y la revisión de textos.
  `final_validation` no cambia.
- **Revisión.** Nodo `copy_review`, con el molde de `final_validation`. `after_integrate` lo
  elige cuando `copy_review_due`, antes de `final_validation_due`, y `after_copy_review` vuelve
  siempre a `integrate`. No devuelve ni forma par: su esquema solo admite `important` u
  `observation`, y nunca registra una devolución. Recibe el diff de la pieza integrada contra
  la base de la tarea, sus filas abiertas con su identificador y los textos vigentes.
- **Textos vigentes.** La sección `Tech Writer`, intacta (el sistema nunca la reescribe), más
  una entrada aparte que calcula el sistema: los `proposed` de las filas de textos ya
  saldadas. El revisor compara contra las dos, así que `copy-criteria-written` se mide contra
  lo que Tech Writer escribió de verdad.
- **Filas de textos.**
  - `CopyCategory` tiene tres valores: `copy-unwritten` (un texto de la persona que Tech Writer
    no escribió), `copy-criteria-written` (un texto de Tech Writer que no cumple los criterios)
    y `copy-criteria-changed` (un texto que Developer cambió y no cumple los criterios).
    `Review.category` sigue siendo `FindingCategory`; el registro de la tabla acepta
    `FindingCategory | CopyCategory`.
  - Identificador `copy:<n>.<i>:<ubicación>`: `n` es el número de la corrida de
    `Tech Writer: review` e `i` la posición del hallazgo en esa pasada; `version = n`. Solo
    `copy_location` lo construye y solo `copy_location_parts` lo desarma.
  - Los dueños los calcula el sistema: las subtareas aprobadas cuyo diff toca el archivo,
    unidas con `", "`; si ninguna lo toca, la unidad revisada.
  - Solo `copy_review` salda filas de textos, nombrando su identificador exacto. Todo
    `settled` que no sea una fila de textos abierta se descarta.
  - Un hallazgo nuevo cuya ubicación real y afirmación normalizada coinciden con una fila
    abierta se descarta: una pasada nunca duplica un defecto que sigue abierto.
- **Corrección.** Un `important` abierto lo corrige `fix_important`, a lo sumo una vez por
  tarea. El motivo que recibe Developer muestra la ruta real y el `proposed`, no el prefijo
  `copy:`. Un `important` nuevo después del pase aterriza y se lista en "Accepted without a
  fix". No hay ciclo.
- **Criterios.** Cuarta familia, `writing`: un solo archivo, `WRITING_STANDARDS.md`, entregado
  entero y sin default, que leen solo `TECH_WRITER` y `TECH_WRITER_REVIEW`. Queda fuera del
  índice de conocimiento.
  - Sin el archivo, en definición: el sistema antepone una nota (`NO_WRITING_NOTE`) a la
    sección.
  - Sin el archivo, en revisión: una fila fija `important`, `scope: outside`, ubicación
    `copy:WRITING_STANDARDS.md` y la afirmación constante `NO_WRITING_CLAIM`. Su evidencia
    lleva el documento inicial que propone el rol. Va al seguimiento `needs-triage` (ADR-0053)
    y el merge no espera al humano.
  - El catálogo de mensajes del producto (apostrophe#316) lo nombra el propio
    `WRITING_STANDARDS.md`: los textos se revisan en el catálogo del idioma de origen. El stack
    no gana ninguna clave.
- **Reflector.** `Origin` suma `"copy_review"`. `copy-criteria-written` se atribuye a
  `tech_writer`, que entra en `PLAYBOOK_ROLES`; `copy-unwritten` y `copy-criteria-changed` a
  `developer`, o a `developer_integrator` con el calificador `integration`.
- **Evaluación.** El set de cinco tareas no cambia. El README §9 anota la tarea candidata, una
  tarea de apostrophe con `touches_copy` y `WRITING_STANDARDS.md`, y un seguimiento `outside`
  le deja al humano decidir si sustituye una del set.

## Enmiendas

Sin editar los ADR citados:

- **ADR-0055.** Cuarta familia de criterios, `writing`: `WRITING_STANDARDS.md` entero, solo
  para los dos roles de Tech Writer, sin default, con el mismo aviso de 300 líneas.
- **ADR-0040.** `design_step` conserva el nombre y el contrato `str | None`, pero su último
  escalón, `write_copy`, no es de diseño.
- **ADR-0053.** Las filas de textos entran en la partición de importantes (`copy_review`
  escribe `outside` solo para la falta de criterios; el resto cae en `owned` por dueño).
- **README §13.** "UX Lead, UI Lead y Tech Writer no aprueban al cierre" sigue vigente en lo
  que dice, pero Tech Writer ya no queda sin revisar la implementación: la regla de "no
  valida la implementación" se reemplaza por la revisión de textos de arriba.

## Observabilidad (§7)

No se agrega, mueve ni renombra ninguna métrica ni atributo de span. Valores cerrados nuevos:
dos roles (`Tech Writer` y `Tech Writer: review`) con sus slugs en la etiqueta de rol, los tres
valores de `CopyCategory` y el `Origin` `copy_review`. `prompts.fingerprint` se mueve por los
dos prompts nuevos, así que hace falta un `bl-*` nuevo al mergear.

## Seguridad y costo (§8)

- **Qué se ensancha.** Nada: los dos roles solo leen (`READ_CODE` más `git diff` de solo
  lectura). Ninguna entrada alcanza más de lo que alcanzaba.
- **Costo.** Una corrida de definición y una de revisión por tarea con `touches_copy`, más a lo
  sumo una segunda revisión tras `fix_important`. Cada revisión crece con el tamaño del diff
  de la pieza integrada; la acotan `max_turns` (12) y el tope de 10 USD por intento. Una tarea
  sin `touches_copy` no paga nada.

## Riesgos conocidos

1. **Byte-igualdad sin `touches_copy`.** Que el ruteo y el contexto de cada rol no cambien
   descansa en un test nuevo.
2. **Rol sin medir.** No está en el set de evaluación hasta que el humano decida sustituir una
   tarea.
3. **Vocabulario propio sin `touches_copy`.** Nunca convoca al rol y nada avisa.
4. **`bl-*` nuevo.** Los dos prompts cambian la huella.
