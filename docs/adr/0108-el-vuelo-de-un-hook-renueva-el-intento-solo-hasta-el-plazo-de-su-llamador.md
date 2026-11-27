---
status: accepted
date: 2026-11-27
---

# El vuelo de un hook renueva el intento solo hasta el plazo de su llamador

Enmienda el ADR-0057. El vigilante de intentos estancados, el veredicto puro de
`domain/stall.py` y el reap por marca del intento no cambian. Cambian dos cosas.

## Decisión

- **Umbral.** `stall_timeout_s` vale 1800 s por defecto (antes 2400 s), tanto en
  `Configuration` como en el constructor de `ClaudeRunner`. El piso de 600 s no cambia.
- **Tope del vuelo.** Un hook en vuelo ya no renueva la marca para siempre. Cada entrada de
  `HookActivity.running(ceiling_s=...)` guarda su vencimiento (`entered + ceiling_s`, en
  el reloj del intento) y `HookActivity.expires` expone el más tardío entre las entradas
  vivas, o `None` si no hay ninguna o alguna no declaró tope. `judge` renueva por un hook
  en vuelo solo mientras `hook_expires` es `None` o `ahora <= hook_expires`; pasado el
  vencimiento deja de renovar y el umbral entero corre desde el último tic renovado.
- **El tope es el plazo de su llamador, no una fórmula nueva.** Para la puerta de entrega
  es el `timeout` del `HookMatcher` (`GUARD_TIMEOUT_S + timeout_s + wait_max_s`, un único
  valor local en `hooks_for`); `CheckoutPresubmit.timeout_s` ya cubre la llamada entera:
  primeras corridas, formas afectadas, reintentos de flake y testigo. Para `run_check` es
  `check.timeout_s`, el mismo atributo de `tool_timeout_ms`. Quien llama a `running()` sin
  `ceiling_s` sigue dando actividad sin tope, como hoy.
- **Solo la cláusula del hook tiene tope.** La cláusula de hijos de `judge` (herramienta
  pendiente con un hijo trabajando o ilegible) no cambia: con una herramienta pendiente,
  un chequeo que gira o cuyo hijo no se puede leer sigue manteniendo vivo el intento más
  allá del tope. El tope solo pesa cuando los hijos están quietos y legibles, o cuando no
  hay herramienta pendiente. Este ADR no promete que el tope muerda en la puerta real más
  de lo que muestra el test del runner.
- **Momento del corte.** Un hook que pasó su tope se juzga estancado en tope + umbral, con
  una diferencia de un `CHECK_EVERY_S` hacia cada lado: hacia atrás, porque la última
  renovación puede caer hasta un tic antes del vencimiento; hacia adelante, por la
  cadencia del vigilante.
- **Señal.** `Stall` gana `in_hook` y la línea de stderr del estancamiento lo escribe junto
  a los segundos y al tipo del último evento; nunca el texto de un mensaje. No cambia
  ninguna métrica, etiqueta ni atributo de span.

## Lo que este cambio no arregla

No habría acortado la reintegración del 2026-11-18. Su silencio fue una puerta dentro de
su espera de cupo y de su timeout de 1500 s: el hook estaba dentro de su plazo, así que el
tope no habría mordido. La pregunta de por qué la suite completa se cuelga en
`tests/test_docs.py`, y si una puerta debe repetir la suite completa tras un `timed_out`
sobre una cabeza sin cambios, queda abierta como seguimiento.

## Consecuencias

- Un hook que sobrepasa su propio plazo (el del SDK o el de MCP) ya no retiene el cupo
  indefinidamente: el intento falla como `stall` a lo sumo un umbral después.
- El costo es O(hooks vivos) por tic, a lo sumo dos.
