# Medición

Las métricas del sistema sobre las corridas y las trazas que las llevan. Lee lo que el
proceso registra; el proceso no la conoce.

## Language

**Retrabajo**:
Corrida de rol que repite trabajo ya hecho en la tarea, sea por devolución o por fallo de
rol. Las devoluciones internas y los Developers en paralelo no son retrabajo.
_Campo_: rework — de manufactura y de ingeniería de software clásica; DORA no lo mide, su vecino es change failure rate

**Métrica del sistema**:
Medida sobre las corridas: costo, duración, devoluciones, escalados. Es un evento, no una
tasa: cada corrida deja una muestra con su valor. Distinta de la métrica de producto, que
vive en cada capacidad.
_Campo_: sin término de arte para la distinción que nos importa (medir el sistema, no el producto)

**Muestra**:
El valor que una corrida de tarea o de rol deja en cada métrica del sistema al terminar.
Una corrida, una muestra; nada se acumula entre corridas.
_Campo_: data point — OpenTelemetry; sample en Prometheus

**Set de evaluación**:
Las cinco tareas fijas que se re-corren idénticas contra cada versión de prompts.
_Campo_: eval set — `evalset` en Google ADK, dataset en LangSmith; el benchmark del campo es SWE-bench

**Muestra residual**:
La plata de una patch que ninguna Corrida de rol contabiliza: los intentos fallidos de una
escalada, o un nodo que cerró sin patch. Una muestra más de `agentic.role.cost_usd`, en cero
para una patch vacía, para que la suma de las muestras de una patch sea siempre su costo.
_Campo_: sin término de arte — la industria mide el gasto por llamada, no el que una corrida
paga sin dejar un registro de la corrida que lo pagó
