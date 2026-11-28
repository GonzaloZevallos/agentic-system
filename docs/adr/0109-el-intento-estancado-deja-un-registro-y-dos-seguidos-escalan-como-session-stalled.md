---
status: accepted
date: 2026-11-27
---

# El intento estancado deja un registro y dos seguidos escalan como `session_stalled`

Enmienda el ADR-0057 (y el ADR-0108, que añadió `in_hook` al `Stall`). El vigilante, el
veredicto puro de `domain/stall.py` y el reintento no cambian. El estancamiento sigue siendo
un fallo propio del sistema. Cambia qué queda escrito de él y qué ve el humano cuando dos
intentos seguidos se estancan.

## Decisión

- **Registro.** `Failure` gana el campo opcional `stall: StallRecord`, un `TypedDict` de
  `domain/task.py` con `seconds: int`, `last_event: LastEvent` (el conjunto cerrado del
  runner) e `in_hook: bool`. Es `NotRequired`: una fila escrita antes de este cambio no lo
  tiene y se lee como desconocido, nunca como 0. Solo guarda un entero, un valor cerrado y un
  booleano, nunca texto de mensaje ni de log. `FailurePauseEntry` no cambia.
- **Quién lo escribe.** `AttemptFailed` gana `stall: Stall | None`; `_stall_failure` del runner
  le pasa el `Stall` que ya construye y `run_role` lo copia a `Failure.stall` solo cuando la
  razón registrada, ya acotada, sigue siendo `stall`: un estancamiento acotado a `disk_full`
  no lo lleva. La misma forma viaja en `RoleRun.failures` cuando el reintento sale bien y en
  los `failures` de la corrida cuando escala.
- **Atributo de span.** `agentic.role.stall_s` (entero) en el span del rol, con los segundos
  del último fallo con `stall`, tanto si el reintento sale bien como si escala; ausente si
  ningún fallo lo tiene. Es una señal nueva: no hay métrica ni etiqueta nuevas.
- **Lote.** `escalation_prompts.stalled_batch(pause_log)` es una función pura. Ancla en la
  última entrada `escalation` de todo el `pause_log` y recorre hacia atrás solo entradas
  `failure`, sin mirar la sección. Vale cuando el lote tiene al menos dos entradas y las dos
  últimas son `stall`. No usa `current_epoch` ni `terminal_failures`: un `resumed` o una
  `escalation` anterior cortan el lote, y un `lapse` posterior a la escalación no lo afecta.
- **Causa y fila.** `StallCause = Literal["session_stalled"]` se une a `PromptCause`; nunca se
  guarda. La fila `("failure", "session_stalled")` (`SESSION_STALLED_PROMPT`) pregunta si
  reanudar, con la recomendación `agentic resume` y las opciones reanudar o cerrar como no
  planeada. Su texto manda al humano a mirar la comprobación cuando había un hook en vuelo.
  `escalate.comment` evalúa el selector una sola vez, solo para la clase `failure`, y pasa el
  resultado a la primera línea y al bloque. La precedencia es: causa de formato, luego
  `session_stalled`, luego la causa guardada.
- **Sufijo.** La primera línea del comentario queda `Escalated: <motivo> (session_stalled)`.
  Todo lector de `ESCALATED` usa `startswith`. `state["escalated"]` no cambia.

## Lo que no cambia

`MechanicalCause`, `escalation_cause`, `rules.auto_resumable`, `rules.pause_kind`,
`EXTERNAL_REASONS`, `carve_out.py`, `split.py`, `system_metrics`, `stages.py` y `eval compare`.
Con dos estancamientos `auto_resumable` da `None` y `pause_kind` da `human` sin tocar esas
funciones: la fila es solo presentación.

## Riesgos conocidos

- El costo de un intento estancado sigue siendo 0.0: el runner no lo mide.
- El conteo de fallos propios del scorecard no es comparable a través de este cambio: las
  filas nuevas llevan `stall`, las viejas no.
- El comando colgado no se registra a propósito: el humano ve solo los segundos, el último
  evento e `in_hook`.
- Un lector futuro que compare la línea `Escalated:` por igualdad exacta se rompería con el
  sufijo.
