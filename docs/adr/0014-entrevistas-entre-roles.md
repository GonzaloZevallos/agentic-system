---
status: accepted
date: 2026-09-13
---

# La definición y la solución técnica salen de una entrevista entre dos roles, no de un tiro

El humano decide diseño con `grill-with-docs`: rondas de preguntas con recomendación, cada
respuesta abre la siguiente ronda, y el glosario y los ADRs se escriben sobre la marcha.
Quiere esa entrevista en el sistema aunque la responda un agente. Hoy Product Analyst
escribe la definición en un tiro y PM la valida en otro; el pingpong del Tech Lead estaba
descrito (README §4) pero sin forma.

**Product Analyst entrevista a PM.** El entrevistador pregunta y recomienda (Sonnet); el
entrevistado decide (Opus), con el conocimiento de producto como fuente: es la asimetría
de §10 puesta a trabajar. El issue es el plan a afinar. Después Product Analyst hace
`to-spec` y PM valida el texto: chequea fidelidad a lo que decidió, no ratifica desde
cero. El loop de devoluciones PM→Product Analyst sigue igual sobre ese texto.

**Lo que PM no puede decidir va al humano.** Ni el issue ni el conocimiento lo resuelven:
PM lo marca como pregunta para el humano y la tarea escala con la lista, etiqueta
`needs-human` y comentario en el issue. Hasta acá el sistema escalaba solo por límite o
por fallo; nunca por "no sé", que es el motivo más honesto.

**El Tech Lead efímero entra ahora, como entrevistador.** Entrevista a Tech Lead sobre la
solución con prompt de adversario y modelo más capaz; de ahí salen la solución, los ADRs
del producto y las subtareas con sus costuras. Es el pingpong del README con la forma de
la skill. Esta sesión lo había pospuesto para medirlo aparte; lo adelanta porque es la
entrevista que más cambia el resultado: los errores de solución técnica se componen por
toda la cadena. La regla de "cada rol se gana el lugar" se aplica igual: se mide con y
sin entrevista.

**Tope de cuatro rondas.** Al llegar, el entrevistador cierra con lo que tiene. La
transcripción entera va al documento de tarea como sección `Entrevista: A→B`: es el
registro crudo. El entrevistador corre en una sesión multi-turno del SDK; el entrevistado
también, para que recuerde lo que ya decidió.

**La entrevista escribe directo en la rama de tarea.** `grill-with-docs` edita
`CONTEXT.md` y crea ADRs mientras entrevista; pelear contra eso sale caro en prompt y en
fallos. El entrevistador tiene escritura solo sobre `CONTEXT.md` y `docs/`, en la rama de
tarea que ADR-0011 crea al arrancar. Todo termina en el PR: un término o ADR que no
sobrevive la revisión humana no se mergea. El destilado de PM a `docs/product/`
(ADR-0006) sigue como está.

## Opciones descartadas

- **Product Analyst se entrevista solo**: sin segundo agente no hay fuente nueva; es el
  tiro único con más pasos.
- **PM entrevista a Product Analyst**: las decisiones son del entrevistado, y el dueño
  de la definición es PM.
- **Sin tope de rondas, solo tope de costo**: una entrevista sin tope no termina, y el
  costo no dice cuándo se dejó de aprender.
- **El entrevistador solo propone términos y el sistema los escribe al aprobar**: obliga
  a un prompt que contradiga la skill y a un esquema de salida que la duplique.
