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
