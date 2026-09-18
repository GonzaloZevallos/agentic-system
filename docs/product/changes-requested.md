# Devoluciones: la vuelta al mismo rol

Qué pasa cuando un validador devuelve trabajo: a quién vuelve, con qué contexto vuelve ese
rol, y qué recibe como entrada nueva.

## Jobs

- **Cuando** un Tech Lead me devuelve una subtarea con hallazgos concretos, **quiero** que el
  Developer que la escribió retome desde donde estaba en vez de empezar de cero, **para** que
  la corrección cueste lo que cuesta corregir y no lo que costó construir.
  _Me salió bien si_: la segunda corrida del rol devuelto no vuelve a leer el worktree entero
  ni a redescubrir sus propias decisiones.

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

### Lo que vuelve a encontrar el Developer devuelto

Lo que el sistema garantiza es **la rama y el trabajo**: la subtarea sigue en su rama, los
commits del Developer están, y los archivos que escribió están como los dejó. Eso es lo que
el job necesita y lo que hace que reanudar la sesión sirva.

Lo que el sistema **no** garantiza es que el checkout esté igual que al entregar. Entre la
entrega y la vuelta pasan cosas legítimas: el revisor corre ahí los chequeos declarados del
producto y deja cachés y artefactos sin trackear, una dependencia aprobada se re-mergea antes
de que el Developer arranque, y un worktree que hubo que recrear (rescate desde el documento,
worktree podado) vuelve con la preparación del entorno corrida de nuevo. Nada de eso le borra
trabajo al Developer; le cambia el alrededor.

De ahí la regla que el rol devuelto tiene que cumplir: **no dar por vivo su estado de shell**.
Directorio actual, variables exportadas, un entorno virtual activado, un proceso en segundo
plano: nada de eso sobrevive entre una corrida y la siguiente, aunque la conversación sí. El
prompt se lo dice explícitamente, porque un comando que asume un `cd` anterior es el modo de
fallo esperable de una sesión reanudada.

Darle al revisor un checkout propio para conseguir la garantía fuerte queda descartado: cuesta
un checkout y una preparación de entorno más por subtarea, hace que el revisor reproduzca
sobre un entorno que no es donde la entrega corrió, y compra algo que ningún job pide.

### Sin sesión previa, sesión nueva

Si no hay sesión registrada para ese rol y esa sección, abrir una nueva es lo correcto y no
es un defecto. Pasa al reanudar desde el documento, cuando el documento fue resembrado, o
cuando la corrida anterior no llegó a dejar `session_id`. El sistema no falla ni escala por
eso: arranca en frío con el prompt completo, como la primera vez.

**Una sesión registrada que ya no se puede reanudar cae en este mismo caso.** El id vive en
el store del CLI, por usuario y por máquina: una reanudación días después, en otra máquina o
con la caché podada puede ser rechazada. El sistema no falla por eso: arranca en frío con el
prompt completo y sigue. Lo único que se le pide es que el hecho quede escrito en la sección
de esa corrida —se pidió reanudar, no se pudo, se arrancó de cero—, para que un retrabajo
caro se pueda explicar sin abrir logs.

### Reanudar no es olvidar la objeción

Un rol que conserva su memoria conserva también la razón por la que eligió lo que le
objetaron. El prompt del rol devuelto tiene que decirle explícitamente que **no repita el
camino que la revisión rechazó**: la memoria sirve para no releer, no para insistir. El daño
está acotado por el límite del par: una segunda devolución del mismo par escala la tarea.

### Distinta de la continuación tras un fallo de rol

Que un reintento continúe la sesión del intento fallido (ADR-0004, adenda 2026-09-14) es otra
cosa y ya está resuelto: ahí el rol se rompió y no entregó. Acá el rol entregó y la entrega
fue rechazada. Son dos caminos distintos en el código y arreglar uno no arregla el otro.

### Lo que esta definición no cambia

- **El límite de devoluciones por par** sigue en 2, y una devolución sigue contando aunque la
  sesión se reanude.
- **La revisión sigue siendo incremental** (ADR-0015): reanudar la sesión del Developer no le
  da memoria de sesión al revisor, que sigue siendo una función sin estado.
- **El revisor sigue trabajando en el checkout de la subtarea**, con los chequeos declarados
  del producto (ADR-0016).
- **El ruteo no cambia**: la vuelta de una devolución sigue pasando por el fan-in, no por
  adentro de la rama (ADR-0004, adenda quinta).
- **`ROLE_RETRIES` y los topes** no se tocan.

## Métricas

- **Costo de una corrida de retrabajo contra la original del mismo rol y subtarea**:
  `agentic.role.cost_usd` con `rework="true"` sobre el mismo `role.slug`. Es la métrica de
  cabecera. Objetivo: que la corrida devuelta cueste una fracción de la primera. Hoy, con el
  Developer arrancando en frío, es del mismo orden (~$18 por devolución medidos en GH-12,
  $23.59 de retrabajo de Developer sobre tres devoluciones).
- **Trabajo de redescubrimiento en una corrida de retrabajo**: llamadas, turnos y tool calls
  de una corrida marcada como retrabajo, contra la corrida original del mismo rol y la misma
  subtarea. Un rol que reanuda no vuelve a listar el worktree ni a abrir los archivos que él
  mismo escribió, y eso se ve acá. Objetivo: que bajen las tres.
- **Devoluciones que piden reanudar**: proporción de corridas de rol posteriores a una
  devolución que corrieron pidiendo reanudar la sesión previa, sobre las que tenían sesión
  registrada. Objetivo: 100%. Cuenta el pedido, no el resultado: que el SDK acepte o no la
  reanudación no es del sistema.
- **Reanudaciones rechazadas**: corridas que pidieron reanudar y tuvieron que arrancar en
  frío porque el id ya no servía. No es un incumplimiento de lo anterior, es su propia serie:
  si crece, el ahorro no se está materializando y hay que conservar la conversación de otra
  manera.
- **Los tokens no son métrica de esta definición.** El sistema registra el uso de la llamada
  que entrega, no de la primera, y una sesión reanudada reenvía su conversación entera, así
  que sus tokens de lectura de caché **suben** —y son los más baratos que se facturan—. Un
  panel que los mire esperando una baja va a leer el arreglo al revés.
