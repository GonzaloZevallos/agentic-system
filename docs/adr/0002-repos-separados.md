---
status: accepted
date: 2026-09-12
---

# El sistema y el producto viven en repos separados; el sistema nunca escribe en el suyo

El sistema agéntico es agnóstico al producto. Todo lo que produce al correr (documentos de
tarea, definiciones acumuladas, ADRs del producto, código, estado del checkpointer, overrides
de prompts) se escribe en el repo del producto, bajo `.agentic/` o junto al código. El repo
del sistema no cambia durante una corrida. Si no fuera así, la historia de un producto
ensuciaría el repo del sistema y un segundo producto se mezclaría con el primero.

## Consecuencias

- Lo que varía por producto entra como parámetro: el directorio de contexto de producto.
- Las mejoras de prompts que hace PjM no pueden tocar el sistema; ver ADR-0005.
- El adaptador entre sistema y producto se extrae cuando aparezca el segundo producto, no antes.

## Adenda (2026-09-16): la regla es sobre el checkout, no sobre el repo

El sistema pasa a ser su propio segundo producto (ADR-0017), y eso rompe la frase de arriba
tal como está escrita: una tarea sobre agentic-system escribe en el repo del sistema, porque
el repo del sistema *es* el repo del producto.

La regla pasa a ser: **el sistema nunca escribe en el checkout desde el que corre.** Una
corrida la ejecuta `stage0`, un checkout aparte fijado a un sha (#44), y escribe en el
contexto de producto que recibe como parámetro, que cuando el producto es el sistema resulta
ser este mismo repo en otro checkout.

Lo que el ADR protegía sigue protegido, y de eso se trata la reformulación. "Que la historia
de un producto no ensucie el repo del sistema" vale igual: la historia que queda acá es la
de este producto, que es el sistema. "Que un segundo producto no se mezcle con el primero"
vale más que antes: el gimnasio tiene su repo y el sistema el suyo, y ahora hay dos de
verdad. Lo que no se sostenía era la forma de decirlo, que confundía el repo con el código
en ejecución: lo que hay que congelar durante una corrida es el código que la ejecuta, y eso
es un checkout, no un repositorio.

La tercera consecuencia de arriba se cumple con esto: el adaptador entre sistema y producto
se extrae cuando aparezca el segundo producto, y el segundo producto apareció.

## Adenda (2026-09-17): "checkout" se dice, "repositorio" se cobra

La adenda de arriba dejó la regla escrita sobre el checkout, y al implementarla (#44) quedó
claro que así escrita permite el caso que hay que prohibir. Leída al pie de la letra, un
`git worktree` del repo de stage0 es *otro* checkout, así que correr una corrida sobre él
no la violaría. Y sí la viola: dos checkouts de un mismo repositorio comparten `.git`, o sea
las mismas ramas, los mismos objetos y el mismo índice de worktrees. Una corrida crea la
rama `agentic/<tarea>` y un worktree por subtarea; si el producto es un worktree del repo de
stage0, todo eso nace adentro del repositorio que está ejecutando la corrida, y el `git -C
stage0` con que `system_version()` saca el sha lee un HEAD que vive en ese mismo `.git`.

La regla que se cobra es entonces la más fuerte: **el sistema nunca escribe en el
repositorio desde el que corre.** "Checkout" sigue siendo la palabra con que se dice, porque
nombra la copia que ejecuta y es lo que hay que congelar; "repositorio" es la unidad con que
se verifica, porque es donde el daño ocurre. El chequeo compara el `git rev-parse
--git-common-dir` de los dos lados y no la ruta de texto, que es lo que hace que un worktree
caiga.

Esto es también lo que obliga a que stage0 sea un **clon** y no un `git worktree`: un clon
tiene su propio `.git`, sus propios objetos y sus propias ramas, así que es un repositorio
distinto aunque venga del mismo origen y comparta la historia. Un worktree no, y por eso
`scripts/stage0.sh` clona.

Lo que la regla no prohíbe, y conviene decirlo porque se parece: que el humano edite el
checkout del producto mientras una corrida trabaja sobre ese mismo repositorio. Ese es el
caso normal del sistema desarrollándose a sí mismo. Lo prohibido es que el repositorio del
producto sea el que ejecuta, no que alguien lo toque.
