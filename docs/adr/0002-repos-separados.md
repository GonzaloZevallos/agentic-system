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
