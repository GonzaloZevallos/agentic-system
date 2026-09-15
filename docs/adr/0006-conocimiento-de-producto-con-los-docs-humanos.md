---
status: accepted
date: 2026-09-13
---

# El conocimiento de producto vive con los docs humanos y se destila de los documentos de tarea

Lo que el sistema aprende sobre el producto (cómo se comporta cada capacidad, sus métricas,
las decisiones técnicas, el vocabulario) se guarda destilado y vigente en el repo del
producto, fuera de `.agentic/`: `docs/product/<capacidad>.md`, `docs/adr/` y `CONTEXT.md`.
Los documentos de tarea son la materia prima: registro crudo de cada tarea, con historia,
que nadie lee para saber qué está vigente.

PM hace el destilado al aprobar: devuelve en su salida estructurada el archivo completo de
cada capacidad tocada y los términos nuevos del glosario, y el grafo los escribe. PM no
necesita tools ni permisos, y el cambio al conocimiento entra en el mismo diff que el humano
revisa.

Por qué fuera de `.agentic/`: el conocimiento es del producto y lo lee cualquiera, humano o
rol. `.agentic/` es maquinaria del sistema (tareas, flags, prompts, estado) y un directorio
con punto no es donde alguien va a buscar cómo funciona el producto. Los ADRs ya vivían
afuera por la misma razón; esto extiende el criterio a todo el conocimiento.

Por qué destilado y no las tareas: ante un bug sobre check-ins tras tres features que los
tocaron, Product Analyst leería tres documentos y reconstruiría a mano qué quedó vigente. Con
un `check-ins.md` lee un archivo.

## Adenda (2026-09-14): el job primero

Cada archivo de capacidad abre con `## Jobs`: las job stories que la capacidad sirve, acumuladas tarea a tarea. Es lo primero que decide una feature (primera ronda de la entrevista de ADR-0014) y lo que PM usa para devolver comportamiento que no sirve a ningún job; sin esa sección la aprobación no cuenta. Se eligió la job story y no la persona: en un producto con un solo rol de usuario, la persona agrega ruido y la job story fuerza situación y resultado, que es lo que le faltaba a la primera definición real ("cómo va mi gimnasio" no decía cuándo ni para decidir qué).

## Adenda segunda (2026-09-15): los ADRs llegan en el prompt y cierran la pregunta

Esta decisión ya contaba los ADRs como conocimiento, pero el adaptador solo cargaba `docs/product/` y `CONTEXT.md`. En la tercera corrida de GH-12 en gym-saas, PM entrevistado preguntó al humano cómo se autentica un llamador, cosa que el ADR-0002 del producto decide (no hay identidad de llamador a propósito), y la tarea escaló por eso (issue #11). PM corre sin tools: si el ADR no le llega, no existe.

Desde acá `## Product knowledge` trae también `docs/adr/*.md`, enteros y sin filtrar por estado (un ADR reemplazado lo dice en su cabecera y sigue explicando la forma del código), después de las capacidades, para todo rol que lee conocimiento. Para PM un ADR es una decisión tomada, de Tech Lead o del humano: la aplica como está y no manda al humano lo que un ADR responde; solo un pedido que contradice un ADR de frente es algo que una persona decide. Retirar una pregunta ya registrada cuando una ronda posterior la responde es el issue #12, no este.

## Opciones descartadas

- **`.agentic/producto/`** (la decisión anterior): separa lo que escribe el sistema de lo que
  escribe el humano, pero deja ADRs y glosario afuera y las definiciones adentro: dos criterios
  para el mismo tipo de cosa. Nunca llegó a tener formato ni lector en el código.
- **Los documentos de tarea son el conocimiento**: sin paso de destilado, pero cada rol
  reconstruye lo vigente a partir de la historia. Falla en cuanto dos tareas tocan la misma
  capacidad.
- **PM con tools de escritura**: más natural para editar archivos grandes, pero abre el modo
  de permisos del SDK antes de que lo necesite Developer.

## Consecuencias

- Amplía la lista de ADR-0002: lo que el sistema escribe en el producto incluye `docs/product/`
  y `CONTEXT.md`, además de `.agentic/`.
- Las definiciones del v0 no existen: `docs/product/` arranca vacío y cada archivo nace con la
  primera tarea que toca esa capacidad, o lo escribe el humano.
- Product Analyst y PM cargan todo el conocimiento en contexto. Cuando crezca, PjM elige por
  capacidad o entra un índice; no antes.
