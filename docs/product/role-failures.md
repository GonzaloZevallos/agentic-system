# Fallos de rol y lo que el humano lee

Qué queda escrito cuando un intento de un rol falla, qué presupuesto tiene un rol para
entregar antes de que falle, y qué llega al humano cuando esos fallos escalan la tarea.

## Jobs

- **Cuando** el sistema me escala una tarea porque un rol falló dos veces, **quiero** leer
  en el issue qué falló de verdad, **para** decidir sin abrir logs ni reproducir nada si
  reanudo, arreglo algo o simplemente espero.
  _Me salió bien si_: con el motivo solo distingo una corrida rota (salida estructurada
  inválida, turnos agotados) de una cortada por algo externo al trabajo (límite de cuenta,
  red, tope de gasto), y sé cuál es mi próximo paso.

- **Cuando** un rol falla y el sistema lo reintenta con el error como entrada, **quiero**
  que ese error diga qué pasó, **para** que el segundo intento tenga una chance distinta
  del primero en vez de repetirlo a ciegas.
  _Me salió bien si_: el reintento nunca recibe un mensaje que no nombra ninguna causa.

- **Cuando** un rol tiene que leer varios módulos y documentos de tarea antes de poder
  entregar, **quiero** que su presupuesto de turnos le alcance para hacer ese trabajo,
  **para** que la tarea termine con lo que el rol vino a producir y no con un escalado que
  cobra el trabajo sin entregar nada.
  _Me salió bien si_: una corrida sana de ese rol entrega sin agotar sus turnos, y un
  escalado por turnos agotados es un hecho a corregir, no el final esperable de ese rol.

- **Cuando** el sistema le niega a un rol una herramienta que su rol no incluye, **quiero**
  que la negación le diga cuál es su regla, **para** que cambie de estrategia en vez de
  gastar sus turnos buscando la sintaxis que la esquive.
  _Me salió bien si_: después de la primera negación el rol no vuelve a intentar lo mismo
  con otra forma (heredoc, intérprete, codificación) y sigue con lo que su rol sí puede
  hacer.

- **Cuando** leo una corrida que salió caro o escaló, **quiero** ver en el documento que el
  rol chocó con negaciones y cuántas, **para** explicar en qué se gastó sin abrir logs.
  _Me salió bien si_: el documento distingue una corrida que peleó contra su jaula de una
  que gastó leyendo.

## Comportamiento

### El motivo de un intento fallido nombra una causa

Cada intento fallido queda registrado con un motivo en texto. El motivo se arma con lo que
el harness tenga, en este orden, y **nunca queda vacío ni tautológico**:

1. **Todo lo que el SDK reporta**, no un subconjunto elegido por el código: el subtype y
   los errores, y también la prosa del resultado y los campos que nombran la causa
   (`api_error_status`, `terminal_reason`, `exit_code`). El caso real del 2026-09-17 cae
   acá: el `ResultError` venía con `subtype="success"` y `errors` vacío, pero con el texto
   "API Error: …" del límite de sesión en el resultado. Ese texto es la causa y tiene que
   llegar al motivo; descartarlo es el defecto.
2. Cuando la prosa viene vacía, el motivo lleva igual **el código de salida y la razón de
   terminación que el SDK reporta**: alcanzan para distinguir un corte externo de una
   corrida rota.
3. Cuando no hay absolutamente nada, el motivo lo dice en palabras ("el SDK terminó sin
   mensaje"), que es información: le dice al humano dónde mirar.

Un motivo cuya parte diagnóstica queda vacía después de los dos puntos es un defecto, no un
caso aceptable. `run failed (success): ` es el ejemplo a no repetir.

El stderr del CLI **no** es fuente de esta definición: el SDK fijado no lo entrega sin
infraestructura que el producto no tiene, y los campos de arriba cubren el job. Si alguna
vez un escalado real llega con todos ellos vacíos, ese hallazgo justifica capturarlo, y es
una tarea aparte.

### La escalada arrastra el motivo, no solo el conteo

Cuando los intentos se agotan y la tarea escala, el motivo que llega al issue lleva el del
último intento **textualmente**, además del rol y la cantidad de fallos. "X failed 2 times"
solo, sin causa detrás, no cumple lo que la entrada **Escalado** del glosario promete: que
el motivo es texto libre **para el humano**.

### El presupuesto de turnos de un rol se dimensiona por su trabajo

Cada rol corre con un tope de turnos, y agotarlo sin resultado es un fallo de rol. El tope
se dimensiona por **el trabajo que la entrega de ese rol exige**, no por lo que le alcanza
a otro rol que comparte el nombre o el prompt base.

El caso que lo fija: el Product Analyst en modo bug tiene que **reproducir** antes de
dictaminar, y reproduce leyendo el código, los documentos de tarea y los tests que ya
existen, o corriendo los chequeos declarados del producto. Eso son varias lecturas en un
repo de este tamaño. El Product Analyst de una feature, en cambio, contesta desde el pedido
y el conocimiento que recibe en el prompt. Son dos presupuestos distintos: el de bug no
puede ser el que hereda del de feature por ser un `replace(...)` de él, ni el que el Tech
Lead necesita para revisar un diff.

Lo medido, y lo que esta definición corrige: entre las corridas de #69 y #81, cuatro
intentos del Product Analyst de bug agotaron `max_turns (12)` y ninguno llegó a un
dictamen; el primero gastó $1.08 sin entregar nada. En #81 el rol leía productivamente y sin
intentar escribir: el presupuesto era el límite, no la estrategia.

**Turnos agotados sin resultado es una rotura, no el modo normal de terminar.** Un rol cuyo
trabajo sano no cabe en su tope produce escalados que cobran el trabajo y no entregan nada,
y eso es lo que el job de arriba niega. El número concreto y cómo se declara —heredado o
propio de cada rol— es calibración de ingeniería contra esta definición.

**Continuar la sesión no repone el presupuesto.** Un reintento tras `max_turns` continúa la
sesión del intento que falló (ADR-0004, adenda 2026-09-14), y eso es correcto: el rol no
vuelve a releer lo que ya leyó. Pero la memoria no compra turnos; el segundo intento arranca
con el mismo tope. De ahí que un tope corto no se arregle con el reintento: se agota dos
veces y escala igual.

**Un presupuesto agotado no se disfraza de entrega.** Un rol que se está quedando sin
turnos no puede entregar un resultado cuyo contenido real sea "no me alcanzó": el Product
Analyst de bug, en particular, no dictamina `sin definición` porque no llegó a leer lo que
faltaba. Ese valor del dictamen afirma algo sobre **el producto** —la definición vigente no
cubre el caso, y entonces PM decide y escribe la que faltaba—, y usarlo como salida de
emergencia le encarga a PM una definición que quizá ya existía y borra el fallo de
`agentic.role.failures{reason="max_turns"}`, que es la serie con la que se verifica este
comportamiento. Lo que sí se le pide es lo contrario de una escapatoria: priorizar la
lectura que decide su entrega y, si igual no llega, que el motivo del intento fallido nombre
qué le faltaba leer, que es lo que le da al reintento y al humano una chance distinta.

### Una negación le dice al rol cuál es su regla

Cuando el sistema le niega a un rol una herramienta o un comando, el motivo que el rol
recibe **nombra la regla de su rol**, no solamente la ruta, el prefijo o el comando que
quedó afuera. Un rol que solo lee tiene que enterarse de que **escribir está fuera de su
rol**, y de qué sí puede hacer en su lugar: reproducir leyendo (el código, los documentos de
tarea, los tests existentes) o corriendo los chequeos declarados del producto.

Vale para todo rol, ejecute o no. Hoy los motivos legibles se arman únicamente para los
roles que ejecutan; un rol de solo lectura recibe la negación genérica del harness, sin
motivo. Eso es el defecto.

La consecuencia medida está en la corrida de #69: el Product Analyst de bug intentó escribir
un test de reproducción, leyó la negación como un problema de sintaxis y reintentó con
heredoc, con `python3` por stdin cinco veces y con `base64` en las dos direcciones, ocho
turnos en variantes de lo mismo. Con el motivo puesto, la primera negación cierra el camino
en vez de abrir la búsqueda de la forma que lo esquive.

Esto **sube la barrera del prompt, no la cierra**: la garantía de que el rol no escribe la
sigue dando el permiso, no el texto (ADR-0011, adenda segunda). El texto existe para que el
rol no gaste su presupuesto contra una pared.

### Las negaciones quedan escritas en la sección de la corrida

Una negación le llega al rol, y además queda visible para el humano: cuando una corrida de
rol chocó con **al menos una** negación, el sistema escribe **una sola línea** en la sección
de esa corrida, con cuántas fueron y la regla que se le nombró al rol. Nada cuando no hubo
ninguna, y nunca una entrada por negación: una negación no es fallo de rol ni devolución, es
un hecho sobre cómo se gastó la corrida.

La escribe el sistema, no el rol, por lo mismo que la puerta de entrega escribe el resultado
de los chequeos: el rol es parte interesada en no reportarlo. Es la misma forma que
`changes-requested.md` ya pide para una reanudación rechazada —el hecho escrito en la sección
para que una corrida caraentre se pueda explicar sin abrir logs— y es lo que hace legible
desde el documento la métrica de turnos gastados después de una negación.

### Lo que esta definición no cambia

Se dice explícito porque los pedidos que la originaron lo incluían y es comportamiento
nuevo, no un defecto de lo escrito:

- **No hay espera ni backoff entre intentos.** Un fallo se reintenta una vez, en el acto,
  como define **Fallo de rol**. Agregar una espera es una tarea aparte.
- **Un corte por límite de cuenta sigue siendo un fallo de rol.** No hay clase aparte, ni
  valor nuevo de `role.outcome`, ni escalada de otro tipo: la tarea queda
  `waiting_on_human` y se reanuda con `agentic resume`, que es lo que corresponde. Lo único
  que cambia es que el humano pueda saber que era eso.
- **La continuación de sesión tras `max_turns` queda como está.** Ya falla con la sesión
  puesta y el reintento la reanuda; lo que esta definición cambia es el tamaño del
  presupuesto y la claridad de la negación.
- **Una negación no cuenta para ningún límite** ni cambia el veredicto de la corrida.
- **`ROLE_RETRIES`, el tope por intento y el tope de tarea no se tocan** (ADR-0004).

## Métricas

- **Escalados con motivo diagnosticable**: proporción de escalados por fallo de rol cuyo
  motivo nombra una causa (no vacía, no solo el conteo). Objetivo: 100%. Hoy, el caso del
  2026-09-17 la deja por debajo.
- **Fallos por turnos agotados**: `agentic.role.failures{reason="max_turns"}` por
  `role.slug`. Objetivo: cero sobre el set de evaluación, y en particular cero para el
  Product Analyst de bug, que hoy aporta cuatro intentos entre #69 y #81. Un dictamen
  entregado por falta de turnos, en vez de un fallo, bajaría esta serie sin arreglar nada:
  por eso la definición lo prohíbe.
- **Turnos usados contra el tope, por rol**: los turnos de una corrida que entregó, contra
  el tope de ese rol. Es la métrica con la que se calibra un tope: un rol que entrega sano
  rozando su tope está a una lectura de escalar, y uno que entrega en la mitad lo tiene de
  sobra. Objetivo: margen, no ajuste.
- **Turnos gastados después de una negación**: turnos de una corrida posteriores a la
  primera negación de herramienta que reintentan la misma acción con otra forma. Objetivo:
  cero. Hoy la corrida de #69 la deja en ocho. Se lee del documento, con la línea de
  negaciones de la sección.
- **Las negaciones no tienen métrica propia.** Una negación aislada a un rol que después
  cambia de estrategia es el sistema funcionando; contarla por rol mediría un hecho
  esperable. Lo que importa es lo que el rol hizo después, y eso es la serie de arriba.
- `agentic.role.failures` y `agentic.role.failure_cost_usd`, con su label `reason`
  (ADR-0008, adenda 2026-09-18): no cambian de nombre ni de juego de valores. Lo que esta
  definición asegura es que el texto que acompaña a esa muestra sea legible.
