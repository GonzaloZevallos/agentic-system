# Devoluciones: la vuelta al mismo rol

Qué pasa cuando un validador devuelve trabajo: a quién vuelve, con qué contexto vuelve ese
rol, y qué recibe como entrada nueva.

## Jobs

- **Cuando** un Tech Lead me devuelve una subtarea con hallazgos concretos, **quiero** que el
  Developer que la escribió retome desde donde estaba en vez de empezar de cero, **para** que
  la corrección cueste lo que cuesta corregir y no lo que costó construir.
  _Me salió bien si_: la segunda corrida del rol devuelto no vuelve a leer el worktree entero
  ni a redescubrir sus propias decisiones, y su primera llamada no repite el contexto que ya
  había cargado.

- **Cuando** miro el gasto de una tarea con devoluciones, **quiero** que el retrabajo se
  parezca al tamaño del arreglo y no al de la tarea, **para** decidir si el loop de revisión
  sale a cuenta.
  _Me salió bien si_: el costo de una corrida de rol marcada como retrabajo es una fracción
  de la corrida original, no un múltiplo de ella.

- **Cuando** un rol vuelve sobre trabajo que ya le devolvieron, **quiero** que recuerde lo
  que hizo **y** lo que le objetaron, **para** que no reintente el mismo camino que acaba de
  ser rechazado.
  _Me salió bien si_: la segunda entrega no repite la solución que el hallazgo bloqueante
  nombró.

## Comportamiento

### Una devolución continúa la sesión del rol que la recibe

Una devolución es un rechazo **con entrega hacia atrás**: el trabajo vuelve al mismo rol, no
empieza de nuevo. Eso vale también para el contexto con que ese rol trabaja.

Cuando un rol vuelve a correr sobre una devolución, el sistema **reanuda la sesión de su
corrida anterior sobre esa misma sección** y le manda como entrada nueva únicamente lo que la
devolución agrega: el motivo, los hallazgos, la sección de la revisión. No le vuelve a mandar
el contexto que esa sesión ya tiene adentro.

La sesión a reanudar es la última corrida de ese rol que escribió esa sección
(`rules.last_session(state, role_name, section)`). Vale para todo rol que reciba
devoluciones, sin excepción: Product Analyst, PM, Tech Lead y **Developer**, que es el que
hoy no lo cumple y el que más caro lo paga, porque su sesión es la que tiene el worktree
leído.

Para el Developer, además, el worktree es el mismo entre una entrega y la siguiente: la
sesión reanudada sigue mirando exactamente el código que ella escribió.

### Sin sesión previa, sesión nueva

Si no hay sesión registrada para ese rol y esa sección, abrir una nueva es lo correcto y no
es un defecto. Pasa al reanudar desde el documento, cuando el documento fue resembrado, o
cuando la corrida anterior no llegó a dejar `session_id`. El sistema no falla ni escala por
eso: arranca en frío con el prompt completo, como la primera vez.

### Reanudar no es olvidar la objeción

Un rol que conserva su memoria conserva también la razón por la que eligió lo que le
objetaron. El prompt del rol devuelto tiene que decirle explícitamente que **no repita el
camino que la revisión rechazó**: la memoria sirve para no releer, no para insistir. El daño
está acotado por el límite del par: una segunda devolución del mismo par escala la tarea.

### Distinta del continuación tras un fallo de rol

Que un reintento continúe la sesión del intento fallido (ADR-0004, adenda 2026-09-14) es otra
cosa y ya está resuelto: ahí el rol se rompió y no entregó. Acá el rol entregó y la entrega
fue rechazada. Son dos caminos distintos en el código y arreglar uno no arregla el otro.

### Lo que esta definición no cambia

- **El límite de devoluciones por par** sigue en 2, y una devolución sigue contando aunque la
  sesión se reanude.
- **La revisión sigue siendo incremental** (ADR-0015): reanudar la sesión del Developer no le
  da memoria de sesión al revisor, que sigue siendo una función sin estado.
- **El ruteo no cambia**: la vuelta de una devolución sigue pasando por el fan-in, no por
  adentro de la rama (ADR-0004, adenda quinta).
- **`ROLE_RETRIES` y los topes** no se tocan.

## Métricas

- **Costo de una corrida de retrabajo contra la original del mismo rol y subtarea**:
  `agentic.role.cost_usd` con `rework="true"` sobre el mismo `role.slug`. Objetivo: que la
  corrida devuelta cueste una fracción de la primera. Hoy, con el Developer arrancando en
  frío, es del mismo orden (~$18 por devolución medidos en GH-12, $23.59 de retrabajo de
  Developer sobre tres devoluciones).
- **Tokens de la primera llamada de una corrida de retrabajo**: una corrida que reanuda no
  vuelve a cargar el contexto entero. Hoy el Developer lee ~184k tokens cacheados por llamada
  también en la corrida devuelta.
- **Devoluciones que reanudan sesión**: proporción de corridas de rol posteriores a una
  devolución que corrieron con `resume`, sobre las que tenían sesión previa registrada.
  Objetivo: 100%.
