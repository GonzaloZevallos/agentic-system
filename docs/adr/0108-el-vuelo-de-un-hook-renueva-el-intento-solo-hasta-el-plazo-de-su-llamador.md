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

## Adenda (GH-1169): un intento abandonado deja de esperar cupo y de lanzar chequeos

La puerta de entrega y `run_check` corren en hilos del orquestador (`anyio.to_thread.run_sync`),
y la reaper de un estancamiento mata al CLI, no a esos hilos: el hilo de un intento ya cortado
seguía esperando cupo hasta `wait_max_s`, reservaba y lanzaba chequeos mientras el reintento
competía con él. Además `CheckoutPresubmit` y `RunCheck` se construyen una vez por `run_role`,
así que el intento 2 comparte objeto, y token de la libreta, con el hilo sobrante del 1.

- **Una bandera por intento.** `AttemptAbandon` (un `threading.Event` con `set()` e
  `is_set()`, en `adapters/claude_sdk/hooks.py`) la arma `ClaudeSdkRunner.run_once` junto a
  `HookActivity` y `TurnCount`; nunca vive a nivel de módulo ni se guarda en `Presubmit` ni en
  `RunCheck`. El runner la levanta en toda salida del intento, siempre antes de la reaper: en
  `watch` justo después de armar el `Stall` (sin condicionarla a `in_hook`, que solo queda en
  la línea de stderr), y en `run_once` ante `AttemptFailed`, ante cualquier `BaseException` y
  en la salida normal. El orden es siempre bandera, reaper, excepción.
- **El adaptador la liga, no los puertos.** Un envoltorio por intento
  (`_AbandonablePresubmit`, `_AbandonableCheck`), armado solo cuando el objeto es un
  `CheckoutPresubmit` o un `RunCheck` reales, pasa `abandoned=attempt.is_set` a cada llamada.
  Los Protocol `Presubmit`, `CheckTool` y `CheckScheduler` no cambian; los dobles de prueba
  nunca ven la palabra clave, y `develop.py` sigue leyendo los objetos reales.
- **La salida es una excepción.** `CheckAbandoned(BaseException)`, junto a `RunStopped`, para
  que ningún `except Exception` del camino la trague. La libreta la lanza desde su espera
  (`CheckRequest.cancelled`, leída en cada vuelta, antes de reservar y tras cada sueño, sin
  llegar al respaldo de vencido) y sale por el `except BaseException` que ya suelta su token.
  Con bandera, cada pedido de cupo lleva un token nuevo (conserva el `enqueued_at`, así que
  la fila de ADR-0070 no se pierde), de modo que el hilo abandonado y el reintento nunca
  comparten una reserva.
- **Respuesta fija.** La puerta contesta `the attempt was abandoned; no check was run` como
  denegación sin contarla como rechazo de la puerta (`on_presubmit_denied` no se llama) y sin
  esperar la cancelación; la herramienta `run_check` devuelve el mismo texto. Nadie lo lee:
  el intento ya fue cortado.

**Riesgo conocido.** Un chequeo ya lanzado no lo alcanza la reaper del intento: su marca es
`CHECK_TASK`, el pid del sistema y un token propio (`commands.py`, `reaping.matches`). Termina
por su propio plazo y retiene su cupo hasta entonces; nunca lo corta la bandera ni la reaper de
estancamiento. Pasado ese chequeo la bandera impide guardar su fila, su flake y su pase como
reutilizable, y no se lanza ningún otro. El reintento puede solaparse con ese único chequeo a
lo sumo un `timeout_s`. No se ensancha la reaper al alcance de la corrida, porque el pid de la
corrida lo comparten todos los intentos vivos.
