---
status: accepted
date: 2026-11-14
---

# El poller sigue la rotación de stage0 con un re-exec, y el rotador revierte lo que nadie adoptó

GH-622 parte 3 (GH-633, GH-736). La rotación (`agentic stage0 rotate`) mueve `<root>/current` a un
stage nuevo; un poller que sigue corriendo el código viejo no se entera. Este documento fija cómo
el poller se pasa al stage vigente y cómo el rotador deshace un cambio que ningún poller adoptó.

## Decisión

- **El poller se reemplaza a sí mismo con `execve`.** Al empezar cada ronda, `_reexec` lee
  `current` del store, lo resuelve como `(root/sha).resolve()` y llama a `reexec_due`. Ninguna
  ruta de lanzamiento, `chdir` o exec pasa por el enlace `current`: el argv, el entorno y el
  destino salen de los constructores puros (`reexec_argv`, `reexec_env`) con rutas absolutas de
  la `Configuration`.
- **El lock se suelta antes del exec.** El pid se conserva a través de `execve`: un lock que
  siguiera escrito parecería tomado por un proceso vivo que ya no es el poller, y el poller nuevo
  se negaría a sí mismo. Orden: verificaciones previas (ejecutable, `getcwd`, constructores; sin
  efecto), flush de stdout, stderr y telemetría en un hilo con tope, `release`, `chdir`, `execve`.
  Ante un `OSError` se vuelve al cwd guardado, se retoma el lock por el mismo camino que `_locked`
  y la ronda sigue en el código viejo; la siguiente reintenta. Si el lock lo tiene ya un poller
  vivo, el proceso termina con 0 sin soltarlo ni escribir latidos (`_PollHold.held` es falso).
- **`AGENTIC_REEXEC_FROM`.** El proceso nuevo recibe el stage destino (el del exec); si `current` apunta ahí
  otra vez (`reexec_blocked`) no vuelve a hacer exec: dice `reexec_failed` una vez y sigue. Solo
  un proceso con esa marca hereda hijos: lee una vez `/proc/self/task/*/children` y en cada ronda
  recoge con `waitpid(pid, WNOHANG)` los que pasaron a `Z`. Nunca `waitpid(-1)`.
- **Solo una parada señalada escribe `stop`.** Una cancelación de anyio escribe el latido `stop`
  dentro del lock y antes de soltarlo, y ese poller deja de ser esperado. Un crash o un exec dejan
  el último latido. El poller no tiene plazo duro: `main` no le da `StopCause`.
- **Orden del rollback: rechazar, intercambiar, escalar.** Si pasada la gracia un poller esperado
  no muestra el checkout nuevo, el rotador primero rechaza el sha, después vuelve `current` al
  anterior y solo si eso no basta escala a una persona.

## Riesgos conocidos

- La primera rotación después de este merge no puede revertirse: los pollers viejos no escriben
  latidos ni hacen re-exec.
- Un poller matado durante la gracia hace rechazar un sha bueno; el costo es un comentario y una
  liberación manual.
- Un reinicio de rutina durante una ronda más larga que el timeout de parada del servicio no deja
  latido `stop`, y dentro de la gracia puede rechazar un sha bueno.
- Una ronda más larga que la gracia parece colgada.
- Un crash al arrancar el stage nuevo espera a la próxima `rotate`.
- Los spans de la ronda anterior al exec se pierden cuando el flush con tope vence.
