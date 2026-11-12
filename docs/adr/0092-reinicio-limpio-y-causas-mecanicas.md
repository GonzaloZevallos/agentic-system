---
status: accepted
date: 2026-11-12
---

# Reinicio limpio, causas mecánicas y escalados de una palabra

GH-593. En apostrophe GH-407 hubo 24 escalados y la tarea esperó unas 120 h una decisión; casi
todos eran mecánicos (15 colisiones de número de ADR, un fallo de DNS, dos e2e locales bajo carga,
tres "el CI no dio veredicto") y la respuesta también ("aceptá el número que eligió la regla").
Reglas del dueño: solo se le pregunta lo que es de juicio, y tras dos devoluciones del mismo par
el sistema reinicia la subtarea en limpio y escala recién si el intento reiniciado también llega
al límite.

Fuentes del pedido: la guía de Claude Code (tras dos correcciones fallidas, limpiar el contexto y
escribir un mejor prompt), Cursor (revertir y relanzar suele ser más rápido que arreglar un agente
a medias), Laban et al. 2025 (arXiv 2505.06120: los modelos que se pierden en varios turnos "no se
recuperan") y el equipo de Codex de OpenAI (el agente escala "solo por juicio").

## Decisión

- **Reinicio limpio.** `rules.on_return_limit` (definida en `domain/restart.py`) es la única
  decisión de límite: `count`, `restart` o `escalate`. Reinicia solo si `restart.eligible`: el par
  es el Tech Lead→Developer de la subtarea, nunca fue aprobada, la devolvió solo la revisión, no
  hay pull request y `restarts[par] < 1`. El reinicio archiva la rama en `archive/restart<k>-…`,
  salda las filas abiertas como descartadas, escribe `restarts` en el mismo parche
  (`restart.restart_patch`) y arma la sección `Restart <subtarea>` (brief original, hallazgos de
  las dos pasadas, nota de qué no funcionó). Siguen escalando al llegar al límite los pares
  PM→Product Analyst, UX Lead, UI Lead, PM→Tech Lead, QA Performance→Tech Lead, la integración y
  el par de los chequeos completos.
- **Causas mecánicas.** `MechanicalCause` es una lista cerrada (`ci_gh_unavailable`,
  `ci_gh_unreadable`, `ci_run_unfinished`, `delivery_transient`), decidida donde se levanta el
  escalado por la clase del error, nunca por el texto. `rules.auto_resumable` y `rules.pause_kind`
  son los únicos que dicen si una pausa es mecánica; el poller la retoma con `RETAKE_SCHEDULE` y el
  tope `MECHANICAL_RETAKE_MAX` (3), y gastado escala con la evidencia.
- **Renumeración automática de ADR** (D-be76583a). Si los lugares a editar caben en
  `ADR_EDIT_BOUNDS` y el motivo no es `duplicate_own_number`, se aplica sola, gasta el mismo tope
  por colisión que una aplicada con decisión del dueño y, si falla, vuelve a la pregunta de hoy
  sin reintentar.
- **Escalado de una palabra.** El comentario trae una pregunta, la respuesta recomendada y las
  opciones, cada una un comando del CLI, de una tabla cerrada por (clase, causa). Es solo
  presentación.
- **Medición.** `rules.pause_kind` es el clasificador único y la medición lo usa;
  `agentic eval compare` imprime los escalados por causa y tipo y la mediana de horas de espera
  humana por causa, fuera del veredicto. Sin atributo de span ni backend nuevo (ADR-0037).

## Consecuencias

- No enmienda D-03258899, que trata de reiniciar una tarea entera, no una subtarea.
- Las horas de pausa humana y externa no se comparan de un lado al otro de este cambio: una espera
  de `ci` mecánica pasó de humana a externa.
- Los conflictos en archivos generados todavía no tienen una causa tipada (hallazgo `outside`): hoy
  siguen siendo de juicio.
- Un `GitError` de push no tiene tipo transitorio, así que sigue siendo de juicio (clasificar el
  stderr rompería la regla de no leer texto).
