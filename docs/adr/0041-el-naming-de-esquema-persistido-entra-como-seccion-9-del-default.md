---
status: accepted
date: 2026-10-05
---

# El naming de esquema persistido entra como §9 del default, condicional en la revisión

Zeta pidió (apostrophe #75, siete excepciones encontradas por accidente) que el
sistema tenga en cuenta las convenciones de nombrado de esquema al desarrollar. La
letra del pedido dice "Database naming" como título y pide un test que compruebe que
el prompt de revisión "menciona migrations". Esta decisión sigue el fondo del pedido y
se aparta de esas dos letras, y dice por qué.

## El encabezado es `## 9. Naming of persisted schema`, no "Database naming"

`database` es un token prohibido tanto en `STACK_SPECIFIC_TOKENS`
(`tests/core/knowledge/domain/test_standards.py`) como en `STACK_TOKENS`
(`tests/core/process/application/test_prompts_stack_flavour.py`): el default de
ADR-0012 (adenda del 2026-09-22) dejó de nombrar un stack, y esta tarea no reabre esa
guardia para hacerle una excepción. "SQL" y "migration" no están en esas listas, pero
nombran un stack igual (un documento NoSQL o un documento sin motor de cambios de
esquema no tiene ninguna de las dos cosas), así que la sección tampoco los usa: dice
"persisted schema" y "schema change", que es lo que la regla necesita decir para
aplicar a cualquier producto futuro, tenga el stack que tenga.

## Dónde rige: solo el producto sin `CODING_STANDARDS.md` propio

ADR-0012 ya fijó que el archivo del producto reemplaza el default entero, no lo
extiende. §9 entra solo para un producto que no tiene su propio archivo. Este propio
repo tiene el suyo en la raíz (la adenda del 17 de septiembre de ADR-0012) y no tiene
esquema persistido, así que no lee nada de §9: ni falta ni sobra. apostrophe tiene su
propio `CODING_STANDARDS.md` desde antes de esta tarea, así que tampoco hereda la
sección: la recibe recién si un humano la copia a mano adentro de su propio archivo,
después de este merge. Esa copia es un paso humano, no una migración de datos (el
pedido excluye explícitamente renombrar el esquema de apostrophe, que es #75), y el
cuerpo del PR lo deja escrito para que no se pierda.

El revisor no tiene fallback al default: si lo tuviera, un producto con archivo propio
tendría dos fuentes de estándares a la vez, lo que rompe la regla de "reemplaza entero"
que ADR-0012 fijó para que cambiar el estándar de un producto no mueva la huella del
otro.

## La cláusula de revisión es condicional, `important`/`standard-other`

`tech_lead_review.md` gana un párrafo: si los estándares que el revisor recibió traen
una regla de nombrado para el esquema persistido, revisa cada cambio de esquema del
diff contra esa regla -- nunca contra un nombre que el plan haya listado, porque un
nombre que el plan erró sigue siendo un hallazgo real. Sin esa regla en los estándares
recibidos, la cláusula no hace nada: es extensional, igual que ADR-0023 ya pidió para
el disparador de artefactos de enforcement, nunca un juicio de intención.

La violación es `severity: important` (no bloquea, se corrige) y `category:
standard-other`: la lista cerrada ya tiene esa salida (ADR-0012, adenda de
observabilidad) y abrir una categoría propia es otra tarea que tocaría `schemas.py` y
`tech_lead_review.md` juntos, que esta no necesita.

`tech_lead_review_light.md` queda sin tocar. Reporta solo hallazgos `blocking`, y una
violación de nombrado no bloquea por diseño (es `important`); además la pasada de
integración siempre corre el revisor completo, así que el chequeo ocurre al menos una
vez por tarea en cualquier modo de revisión, con o sin el brazo liviano.

## El plan nombra tablas y columnas dentro del `brief`

`tech_lead.md` y `tech_lead_platform.md` ganan la misma instrucción: cuando la
solución agrega o cambia esquema persistido, el plan nombra cada tabla y columna
dentro del campo `brief` de la subtarea que lo escribe, y bajo `### Standards` dice
solo cómo el cambio cumple la regla de nombrado del producto (si sus estándares la
traen) y que los nombres están en esos briefs -- nunca restatea la regla misma.
`plan_view._brief` es lo que llega al Developer: la prosa libre bajo `### Subtasks` no
viaja entera, así que un nombre que solo estuviera ahí nunca llegaría a quien escribe
el código.

## Por qué ningún test afirma sobre la prosa del prompt

El estándar §5 y ADR-0023 ya fijaron que un test que rompe con una reformulación sin
cambio de comportamiento está mal. La cláusula de revisión no tiene una costura propia
honesta: ejecutarla pide una revisión real contra un diff real, que es justo lo que
`tech_lead_review.md` ya hace y ningún test unitario de este repo simula.
`test_context_golden.py` tampoco es esa costura -- fija el contexto que arma
`context_for` con estándares de fixture, nunca la plantilla del prompt, así que queda
byte-idéntico en este diff. La única señal que notaría un borrado silencioso de la
cláusula es `agentic.review.findings{severity="important",
category="standard-other"}` cayendo a cero en un producto cuyos estándares traen la
sección: esa familia no lleva label de `prompts.fingerprint` (ADR-0019), así que sigue
siendo comparable de un lado al otro de este commit, y se lee desde los documentos de
tarea y `agentic eval compare` (ADR-0037).

## La huella se mueve en tres prompts

`prompts.fingerprint` cambia para `tech_lead`, `tech_lead_platform` y
`tech_lead_review`, en todo producto. `tech_lead_review` corre en toda tarea, así que
toda serie segmentada por huella pierde comparabilidad desde este commit; el cuerpo
del PR nombra los tres. `agentic.review.findings` no lleva esa label y sigue
comparable, como dice la sección anterior.

## Riesgos conocidos

- **Una propuesta de stack-review puede heredar §9 para un producto sin esquema
  persistido.** Si se redacta a partir del default, arrastra la sección como si
  aplicara. El humano es quien la revisa antes de aceptarla (ADR-0034).
- **Un cambio de esquema solo de integración no tiene brief que lo cargue.** El
  Developer que integra no tiene un brief de plan para nombrarlo ahí; el revisor igual
  lo chequea contra el estándar, porque la cláusula de revisión no depende del plan
  para disparar.

## Opciones descartadas

- **Título "Database naming"**: el token prohibido de las dos guardias de stack
  (arriba).
- **Un fallback del revisor al default cuando el producto tiene su propio archivo**:
  reabre dos fuentes de estándares a la vez, lo que ADR-0012 ya cerró.
- **Un test sobre la prosa del prompt de revisión**: rompe con cualquier reescritura
  sin cambio de comportamiento (§5, ADR-0023).
- **Una categoría de hallazgo nueva para nombrado**: `standard-other` ya la cubre sin
  ensanchar la lista cerrada que es label de métrica.
