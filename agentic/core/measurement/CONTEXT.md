# Medición

Las métricas del sistema sobre las corridas y las trazas que las llevan. Lee lo que el
proceso registra; el proceso no la conoce.

## Language

**Retrabajo**:
Corrida de rol que repite trabajo ya hecho en la tarea, sea por devolución o por fallo de rol. Medido en plata: todo el costo de una corrida posterior a una devolución, y de una corrida de primera entrega sólo el costo de sus intentos fallidos, más la plata de intentos fallidos que ninguna corrida contabiliza. Cada dólar cuenta una sola vez, y el valor de la tarea es la suma de los de sus corridas. Las devoluciones internas y los Developers en paralelo no son retrabajo.
_Avoid_: reintento (el reintento es un hecho; el retrabajo es lo que cuesta)
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

**Resultado de entrega**:
El veredicto del humano sobre el pull request de una tarea, leído por `agentic close` y no
por ningún revisor del sistema: si fusionó, si lo corrigió antes de fusionar, o si lo cerró
sin fusionar. Es la métrica de calidad que falta después de `ready_for_review`, donde hoy se
corta toda medición.
_Campo_: outcome — el vecino más cercano es change failure rate (DORA), que mide si un deploy
rompió algo, no si el humano corrigió el trabajo antes de aceptarlo

**Fusionado** (`merged`):
El pull request se fusionó sin ningún commit humano posterior al último commit del sistema:
el humano aceptó la entrega tal cual.
_Campo_: merged — GitHub, para el estado de un pull request fusionado

**Fusionado con cambios** (`merged_with_changes`):
El pull request se fusionó, pero con al menos un commit humano, que no sea de fusión,
posterior al último commit del sistema: el humano corrigió antes de aceptar.
_Campo_: sin término de arte — GitHub no distingue este caso de una fusión limpia

**Cerrado** (`closed`):
El pull request se cerró sin fusionarse: el humano descartó la entrega.
_Campo_: closed — GitHub, para un pull request que se cierra sin fusionar
