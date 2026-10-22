---
status: accepted
date: 2026-10-22
---

# Un intento de rol colgado se detecta, se mata por su marca y se reintenta

El 2026-10-21 dos corridas de apostrophe (GH-274 y GH-276, ambas en `resume`) dejaron de
producir salida durante 28 y 18 minutos. Su proceso `claude` no tenía hijos ni conexión
abierta a la API, solo la de OTLP; la CPU rondaba el 1-2 %. Estaban colgadas, no
trabajando. El `async for message in stream` de `runner.py::_session` no tiene ningún
tiempo de espera, así que un intento así retiene su cupo de concurrencia para siempre y la
cola detrás no avanza.

## Decisión

- **Intento estancado.** Hay una sola marca de actividad por intento, `last_activity`, y
  el intento está estancado cuando `ahora - last_activity > stall_timeout_s`. La marca la
  renuevan: cada mensaje del stream (del hilo principal o de un sub-agente); la salida de
  un hook de entrega del intento y cada tic en que alguno sigue en vuelo; y, solo con una
  llamada a herramienta del hilo principal pendiente, el cambio de `utime+stime` de los
  procesos marcados del intento sin contar el CLI, o que alguno esté en estado `R`/`D`.
  Un dato que no se pudo leer cuenta como actividad: nunca se mata sin evidencia.
- **Veredicto puro.** `domain/stall.py` decide sobre una `StallSnapshot`; el adaptador y
  el runner solo la alimentan y la obedecen. Así el veredicto se prueba sin `/proc`.
- **Señal sin sockets.** No se leen conexiones: el CPU y el estado de los hijos marcados
  bastan para distinguir una herramienta que trabaja de un cuelgue, y `/proc/net` por
  intento sería frágil y caro. Los hijos se identifican por la marca del intento
  (ADR-0052), excluido solo el proceso marcado cuyo `ppid` es el orquestador, que es el CLI;
  nunca por grupo de procesos ni por `ppid` directo.
- **Umbral con piso.** `stall_timeout_s` vale 2400 s por defecto y la configuración
  rechaza menos de `STALL_FLOOR_S = 600`. No hay validador cruzado con `check_timeout_s`:
  un hook de entrega, que corre los checks, mantiene el intento con su contador en vuelo
  (un objeto por intento, `HookActivity`) y el umbral entero vuelve a correr desde que el
  hook sale.
- **Orden del vigilante.** Una tarea hermana del bucle del stream escanea en un hilo y
  duerme `CHECK_EVERY_S = 30`, sin acumular tics. Al vencer: guarda el estancamiento, hace
  el reap por marca del intento (el CLI muere y el stream termina por EOF o con una
  excepción del SDK) y solo si el reap deja sobrevivientes cancela un `CancelScope` propio
  que envuelve el bucle. La cancelación del vigilante nunca escribe el aviso "interrupted"
  de la cancelación externa: el estancamiento tiene su línea (`stalled Ns, last event X`,
  sin texto de ningún mensaje) y una cancelación externa sigue sin convertirse en reintento.
- **Motivo propio.** `FailureReason` suma `stall`. No entra en `EXTERNAL_REASONS`, así que
  `auto_resumable` no lo retoma solo: el reintento es el de siempre (`role_retries`) y un
  segundo estancamiento escala con la clase `failure`. El reintento continúa la sesión del
  mensaje `init`; si el cuelgue fue antes, `_stale_session` no gasta el fallback en frío y
  se conserva el `resume` con que entró el llamador.
- **Costo del escaneo (§8).** Un escaneo de `/proc` por intento vivo cada 30 s, lineal en
  los procesos de la máquina (lee `environ` y `stat` de cada uno), sin crecer con los datos
  ni con la duración del intento. Un tic no arranca hasta que el anterior volvió.

## Riesgos conocidos

1. Un hijo bloqueado sin gastar CPU, con el CLI en silencio total durante todo el umbral
   (por ejemplo esperando un lock 40 minutos), muere.
2. Un leak en bucle más una herramienta pendiente que no vuelve sostiene el intento.
3. Reanudar una sesión cuya transcripción termina en un `tool_use` sin su `tool_result`:
   nada en el repo prueba qué hace el CLI en ese estado, y la herramienta pudo dejar
   efectos a medias en el checkout. Si el reintento falla, escala igual con `failure`. No
   se supone resuelto: queda por medir con el primer caso real.
4. El costo de un intento estancado queda en `0.0`, como en todo camino sin
   `ResultMessage`: el tope de tarea y el tope por estimación subcuentan. Los tokens vistos
   van a stderr.
5. Sin lectura de sockets, un CLI que espera una API caída más de 2400 s se mata como
   estancado.
