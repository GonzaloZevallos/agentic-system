# Retrabajo: qué plata repite trabajo y cómo se cuenta

Qué cuenta como retrabajo, cómo se reparte entre la vista de rol y la de tarea, y cuántas
muestras deja una tarea que se pausó y se reanudó.

## Jobs

- **Cuando** miro el gasto de una tarea, **quiero** saber cuánta de esa plata se fue en
  repetir trabajo ya hecho, **para** decidir si el loop de revisión y los reintentos salen a
  cuenta.
  _Me salió bien si_: el número de retrabajo de la tarea es exactamente la suma de los
  números de retrabajo de sus corridas de rol, y puedo abrir uno en el otro sin conciliar
  nada a mano.

- **Cuando** comparo el retrabajo entre versiones de prompts o entre productos, **quiero**
  que una tarea que se pausó y se reanudó pese lo mismo que una que corrió de un tirón,
  **para** que la comparación signifique algo.
  _Me salió bien si_: una tarea con dos pausas suma su retrabajo una sola vez en
  `sum_over_time`, sin que el panel tenga que filtrar por estado.

- **Cuando** una tarea tuvo una devolución o un intento fallido, **quiero** verla en el
  panel con retrabajo mayor que cero, **para** confiar en que el cero significa "no hubo"
  y no "no llegó".
  _Me salió bien si_: toda tarea con al menos una devolución o un fallo de rol deja una
  muestra de retrabajo distinta de cero.

## Comportamiento

### Una sola definición de retrabajo

Retrabajo es el **costo** de toda corrida de rol que repite trabajo ya hecho en la tarea:

- toda corrida posterior a una devolución (`version > 1`), entera; y
- todo **intento fallido**, se haya reintentado con éxito o haya escalado.

Los intentos fallidos entran porque son dinero que no produjo entrega, y porque la entrada
**Retrabajo** del glosario de Medición ya los nombra ("sea por devolución o por fallo de
rol"). No es una decisión nueva de esta tarea: es la que estaba escrita y que la
implementación no seguía.

No son retrabajo las devoluciones internas ni los Developers que corren en paralelo.

### Cada dólar se cuenta una vez

El costo de un intento fallido **ya viaja embebido** en el `cost_usd` de la corrida que
finalmente entrega: `run_role` le suma lo gastado en los intentos fallidos antes de
devolver la corrida, y además lo deja en `failures`. Por eso la cuenta de tarea no puede
ser "corridas con `version > 1` más todo `failures`": la plata de una falla dentro de una
corrida devuelta quedaría contada dos veces, y ése es el defecto que produjo
`task.rework_usd=25.64` contra 12.34 en los spans de rol de la corrida 4 de GH-12.

La cuenta correcta, corrida por corrida:

- corrida con `version > 1`: todo su costo es retrabajo (sus intentos fallidos incluidos,
  que ya están adentro);
- corrida con `version == 1`: es retrabajo **sólo** el costo de sus intentos fallidos;
- plata de intentos fallidos que ninguna corrida contabiliza (la escalada de `run_role`, un
  nodo que cerró sin patch): es retrabajo, y es la muestra residual que ADR-0008 ya define.

### La vista de rol y la de tarea dicen el mismo número

`agentic.task.rework_usd` de una tarea es **exactamente igual** a la suma de las muestras de
grano de rol marcadas como retrabajo de esa misma tarea, con el piso de medio centavo que
ya usa `rules.spent_by_phase` para las diferencias de redondeo. Que las dos vistas puedan
discrepar es el bug, no una diferencia de altitud.

Eso obliga a que la parte de retrabajo de una corrida con `version == 1` sea visible en el
grano de rol: hoy esa corrida sale con `rework="false"` sobre su costo completo, así que su
plata de falla no aparece en ninguna muestra marcada y la suma nunca puede cerrar. Cómo se
emite es del plan; la regla es que ningún dólar quede sin marcar y ninguno quede marcado
dos veces.

### Una Corrida, una muestra de tarea

Las métricas de tarea (`agentic.task.rework_usd`, `.cost_usd`, `.duration`, `.returns`,
`.finished`) dejan **una muestra por Corrida**, como dice la entrada **Muestra** del
glosario: una corrida, una muestra; nada se acumula entre corridas.

Hoy se emiten una vez por **Invocación**: la CLI llama a `TaskRun.finish` con el estado
acumulado en cada `run` y en cada `resume`, así que una tarea pausada dos veces deja tres
muestras con el total entero y `sum_over_time` la cuenta tres veces. Lo correcto es que la
muestra de tarea salga cuando la Corrida termina —entregada, cerrada o descartada— y que
una invocación que sólo vuelve a pausar no deje ninguna. Lo que esa invocación gastó ella
sola es de la familia `agentic.invocation.*`, que ADR-0008 ya define y que esta definición
no toca.

Un panel suma `sum_over_time` sin filtrar por estado y obtiene el valor de la tarea.

### Lo que esta definición no cambia

- **Ningún nombre de serie ni de label se renombra** (ADR-0008: se agregan, no se
  renombran). Lo que cambia es qué valor lleva cada muestra y cuántas hay, no cómo se
  llaman.
- **Los deltas por invocación siguen siendo de su propia familia** y de su propia tarea: no
  son la corrección de este bug ni la reemplazan.
- **`ROLE_RETRIES`, el tope por intento y el tope de tarea** no se tocan.
- **La definición de Retrabajo del glosario no se amplía**: se precisa que lo que se mide es
  el costo, y cómo se reparte entre corridas.

## Métricas

- **Discrepancia entre la vista de tarea y la de rol**: `agentic.task.rework_usd` de una
  tarea menos la suma de las muestras de rol marcadas `rework="true"` de esa tarea. Es la
  métrica de cabecera. Objetivo: 0, con piso de medio centavo. Hoy, en la corrida 4 de
  GH-12, 13.30.
- **Muestras de tarea por Corrida**: cantidad de muestras de `agentic.task.rework_usd` que
  deja una tarea, sobre las Corridas que tuvo. Objetivo: 1, sin importar cuántas
  Invocaciones. Hoy es igual al número de invocaciones.
- **Tareas con retrabajo visible**: proporción de tareas con al menos una devolución o un
  intento fallido cuyo `rework_usd` es mayor que cero. Objetivo: 100%. Hoy GH-11, con una
  devolución, da 0.
- **Retrabajo por tarea** (`agentic.task.rework_usd`) y **costo de corridas de retrabajo**
  (`agentic.role.cost_usd` con `rework="true"`) siguen siendo las series de lectura; lo que
  esta definición asegura es que se puedan restar.
