# Medición

Las métricas del sistema sobre las corridas y las trazas que las llevan. Lee lo que el
proceso registra; el proceso no la conoce.

## Language

**Retrabajo**:
Corrida de rol que repite trabajo ya hecho en la tarea, sea por devolución o por fallo de
rol. Las devoluciones internas y los Developers en paralelo no son retrabajo.

**Métrica del sistema**:
Medida sobre las corridas: costo, duración, devoluciones, escalados. Es un evento, no una
tasa: cada corrida deja una muestra con su valor. Distinta de la métrica de producto, que
vive en cada capacidad.

**Muestra**:
El valor que una corrida de tarea o de rol deja en cada métrica del sistema al terminar.
Una corrida, una muestra; nada se acumula entre corridas.

**Set de evaluación**:
Las cinco tareas fijas que se re-corren idénticas contra cada versión de prompts.
