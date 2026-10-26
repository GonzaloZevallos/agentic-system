---
status: accepted
date: 2026-10-22
---

# El Reflector aprende de todas las fuentes, con curación y puntaje

Enmienda ADR-0043. Decisión del dueño (2026-10-21, GH-296): los hallazgos y las decisiones
humanas se vuelven lecciones puntuadas. ADR-0043 solo aprendía de las devoluciones Tech
Lead→Developer y solo escribía el playbook del producto; una decisión humana moría con la
tarea (en GH-53 la segunda corrida no vio la decisión de la primera).

## Qué frases de ADR-0043 reemplaza

1. «Un `add` se rechaza salvo que cite una tarea cerrada cuyo `return_history` tenga esa
   categoría, del conjunto cerrado `FindingCategory`, con ese `slug` como responsable
   (`playbook.accountable`).» Ahora un `add` cita una tarea cerrada con un `Backing`
   (`return`, `finding`, `design` o `human_decision`) de esa categoría para ese `slug`. Los
   `Backing` los arma el código desde el documento cerrado, nunca desde prosa. Un
   `human_decision` exige además que el `Decision N` citado exista, que el rol haya corrido en
   la tarea y que la lección comparta con la decisión un término no trivial.
2. «Una tabla única, `(tipo de tarea, categoría, ¿integración?) → slug`, alimenta tanto
   `accountable` como `PLAYBOOK_ROLES`.» Ahora la tabla única `ACCOUNTABLE` es
   `(origen, tipo de tarea, categoría, calificador) → slug`: `review` y `final_validation`
   (al Developer de la subtarea), `definition` (Product Analyst) y `design` (UX o UI
   Designer según el lead). `PLAYBOOK_ROLES` sigue derivado de ella. `accountable` conserva su
   contrato (el par de revisión); `return_backing` lee los demás pares.
3. «El playbook vive solo en el overlay del producto.» Sigue siendo el destino por defecto y
   ante la duda; se suma un destino de sistema: una lección con `scope: system` produce un
   issue en el repo del sistema, con `needs-triage` y sin `agentic`, nunca la escritura de un
   archivo en ese repo (ADR-0002).
4. «Ninguna `FindingCategory`.» Se mantiene. `LessonCategory` es un superconjunto
   (`FindingCategory` más `design`, `human-decision` y `definition`), como `ChangeCategory`.

## El curador puntúa

- `keep(confidence, seen, sources)` es la única comparación: `KEEP_CONFIDENCE = "high"` o
  `KEEP_MIN_TASKS = 2` tareas. Una lección cuyas fuentes son todas `human_decision` necesita
  las 2 tareas aunque la confianza sea alta: una escalación habla de esa tarea.
- Lo que no pasa va a `.agentic/playbooks/candidates.yaml`, fuera de todo prompt. Al
  promoverse conserva su `seen_in` y queda como `add` en el `Record`, no como ítem nuevo.
- `archive_due(item, role_tasks_since, backed)` con `ARCHIVE_AFTER = 20` tareas del rol sin
  que ninguna respalde ni cite el ítem. Archivar mueve a `archived.yaml` con motivo `stale`
  y no borra; un `add` que coincide con un archivado lo restaura con su id y su `seen_in`.
- Un ítem envejece solo desde `in_prompt_since`: la fusión del primer PR candidato fusionado
  cuyo playbook, leído en el commit de fusión, contiene su **id**. Nunca desde `added_at` ni
  desde el texto del bloque; uno que nunca se fusionó no envejece.

## Dos puertas y el ledger

La clave de `reflected.yaml` es `<id>@review` o `<id>@decision`; una sin `@` se lee como
`@review`. `review` es la de hoy (`closed_at` y entrega conservada). `decision` lee solo
decisiones humanas de tareas que terminaron sin entrega conservada: descartadas o divididas
(`finished_at`), PR cerrado, o escalada cuyo issue está cerrado. Cada entrada guarda
`decisions` y una tarea con más `### Decision` vuelve a ser elegible. Un fallo transitorio
guarda `attempts` hasta `REFLECT_ATTEMPTS = 3`. `reflected_tasks` es el único contador de
tareas reflejadas.

## Riesgos conocidos

- **Archivar una lección que funciona.** Una lección que evita el error deja de recibir
  señal, y tras 20 tareas se archiva. La recurrencia del error la restaura.
- Un stage0 viejo rechazaría las claves opcionales nuevas de un ítem; esos archivos los
  escribe solo el código nuevo.

## Adenda (2026-10-25): toda entrega que aterrizó, y una primera pasada que arranca sola

El Reflector nunca corrió en ningún producto, por dos causas, y esta adenda las corrige.

- **La puerta `review` admite toda entrega que aterrizó** (`MERGED_OUTCOMES`, incluido
  `auto_merged`), con un único predicado, `playbook_candidates.reflectable`, que leen la
  elegibilidad, `reflect_run` y `reflect`. `KEPT` no cambia: sigue siendo lo que el
  cuadro de mando cuenta como conservado por una persona (ADR-0045). La objeción de
  ADR-0045 (nadie respaldó esa entrega) la responde ADR-0043: el control humano pasa de la
  entrega a la lección, que llega a un prompt solo por un PR de playbook que una persona
  mergea.
- **Sin ledger, la primera pasada llega hasta `first_pass_floor`**: la N-ésima clave más
  reciente de las dos puertas, con N = `reflect_max_tasks_per_run`. Se cuenta, no se
  fecha: apostrophe cierra unas 3 tareas por día, y 14 días serían unas 40 reflexiones y 8
  corridas antes de que una persona vea una lección; con N = el tope, la primera pasada es
  exactamente una corrida. El ledger arranca en ese piso. El poller no cambia: lanza
  `reflect` cuando `pending` no está vacío.
- **`--since` sin ledger arranca el ledger en `since`**, nunca en `now`.
- **Un fallo del entorno** (`OSError`, `GitError`) frena el lote, escribe lo que terminó y
  no deja entrada ni consume `REFLECT_ATTEMPTS`; una línea en stderr nombra el documento.
  Cualquier otra excepción es un bug y se propaga con el ledger sin escribir. Solo un fallo
  del rol consume intentos.
- **Costo**: cerca de 1 USD por tarea cerrada por cualquiera de las dos puertas, lineal con
  el tráfico (unos 3 USD por día en apostrophe). Lo acota la corrida, no el día.
