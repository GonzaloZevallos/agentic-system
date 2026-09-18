# Fallos de rol y lo que el humano lee

Qué queda escrito cuando un intento de un rol falla, y qué llega al humano cuando esos
fallos escalan la tarea.

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

### Lo que esta definición no cambia

Se dice explícito porque el pedido que la originó lo incluía y es comportamiento nuevo, no
un defecto de lo escrito:

- **No hay espera ni backoff entre intentos.** Un fallo se reintenta una vez, en el acto,
  como define **Fallo de rol**. Agregar una espera es una tarea aparte.
- **Un corte por límite de cuenta sigue siendo un fallo de rol.** No hay clase aparte, ni
  valor nuevo de `role.outcome`, ni escalada de otro tipo: la tarea queda
  `waiting_on_human` y se reanuda con `agentic resume`, que es lo que corresponde. Lo único
  que cambia es que el humano pueda saber que era eso.
- **`ROLE_RETRIES` y el tope por intento no se tocan** (ADR-0004).

## Métricas

- **Escalados con motivo diagnosticable**: proporción de escalados por fallo de rol cuyo
  motivo nombra una causa (no vacía, no solo el conteo). Objetivo: 100%. Hoy, el caso del
  2026-09-17 la deja por debajo.
- `agentic.role.failures` y `agentic.role.failure_cost_usd`, con su label `reason`
  (ADR-0008, adenda 2026-09-18): no cambian de nombre ni de juego de valores. Lo que esta
  definición asegura es que el texto que acompaña a esa muestra sea legible.
