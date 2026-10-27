---
status: accepted
date: 2026-10-22
---

# Los hallazgos importantes se corrigen dentro de la tarea

Los hallazgos `important` que quedaban abiertos al aprobar una tarea se archivaban como un
issue de seguimiento con la etiqueta `agentic`, que corría solo y archivaba los suyos
(apostrophe #274 y #276 salieron de GH-45). La cola no se achicaba. Decisión del dueño
(2026-10-21): los importantes se corrigen dentro de la misma tarea, en un pase final antes del
merge que no cuenta como devolución; los seguimientos son solo para trabajo fuera del alcance,
a lo sumo uno por tarea, con la etiqueta `needs-triage` y nunca `agentic`.

## Decisión

- **Partición.** `rules.important_partition(state)` es el único lugar que separa los
  `important` abiertos por `scope`: `owned` (`subtask`, por subtarea dueña, o la unidad
  revisada si el nombre no es una subtarea), `plan` (reabre la unidad revisada) y `outside`
  (valor nuevo de `FindingScope`: trabajo que la tarea no hizo o que el issue no pidió).
  Ningún otro módulo filtra `important` por `scope`.
- **Pase final.** `LandingStepKind` suma `fix_important`. `landing_step` lo elige cuando
  quedan importantes de la tarea (`owned` o `plan`) y no hay un pase resuelto; si no,
  `file_followups_and_land`. Su tope es `LANDING_IMPORTANT_PASSES_MAX = 1` sobre
  `LandingSpent.important_passes`, sin cargo a `LANDING_PASSES_MAX`. `landing_fix.fix_important`
  reabre con `landing_sync.reopen(..., "returned")`: la entrada del historial lleva
  `by: "landing"` y `changes_requested` no se toca, así que no hay devolución ni escalado. El
  pase cuenta solo cuando `deliver` resuelve la entrada (`resolved_at`); lo que sigue abierto
  aterriza y el comentario `Auto-merged:` lo lista en "Accepted without a fix".
- **Seguimiento.** `file_followups_and_land` considera solo las filas `outside`:
  `Tracker.ensure_label("needs-triage")`, luego `open_issue(..., label="needs-triage")`. Un
  `ValueError` (incluido `TrackerCallFailed`) de cualquiera de los dos degrada a un comentario
  en el pull request con el mismo cuerpo; si el comentario también falla, se loguea. En todos
  los casos se escribe `followups_filed: True`. Con linaje >= 1 las filas `outside` van como
  comentario, como hasta ahora. `FOLLOWUP_LABEL` desaparece; `landing_flakes.FLAKE_LABEL` no
  cambia. Ningún camino de los hallazgos abre un issue `agentic`.
- **Decisión de merge.** `MergeFacts` suma `open_important`, `open_outside` e
  `important_pass_done`. `open_findings` rechaza si hay importantes de la tarea sin pase
  resuelto y sin `followups_filed`, o `outside` sin `followups_filed`. `MergeRefusal` no gana
  ningún valor. Con cero `important` abiertos, la decisión y el paso son los de antes.
- **`reported_severity`.** Todo hallazgo que el sistema baja de severidad
  (`lower_unbacked_blocks`) conserva en el registro crudo `findings` lo que dijo el revisor;
  uno sin bajada no lleva la clave. Es la entrada del Reflector.
- **`followups_filed`** conserva el nombre y pasa a significar "se archivó o comentó el
  seguimiento de fuera de alcance" (fila `followups` de `observability/README.md`).

## Enmiendas

Sin editar los ADR citados:

- **ADR-0048.** La fila `open_findings` de la tabla del aterrizaje lleva a `fix_important` y
  después a `file_followups_and_land`, este último solo para `outside` y con `needs-triage`.
- **ADR-0045.** La condición de `open_findings` del rechazo es la de arriba.
- **ADR-0034.** El puerto `Tracker` puede crear una etiqueta si falta (`ensure_label`): lista
  con `gh label list --limit 1000 --json name` sin `--search`, compara el nombre exacto en
  local y corre `gh label create` sin `--force`. Nunca edita una etiqueta existente ni decide
  leyendo `stderr`.

## Observabilidad (§7)

No se agrega, mueve ni renombra ninguna métrica, etiqueta ni atributo de span. Valores
cerrados nuevos: `fix_important` en `LandingStepKind` (aparece en `agentic.landing.*`) y
`outside` en `FindingScope`. Clave opcional nueva del registro de hallazgo:
`reported_severity`. `prompts.fingerprint` se mueve solo para `tech_lead_review`, así que
hace falta un `bl-*` nuevo al mergear.

## Seguridad y costo (§8)

- **Qué se ensancha.** El sistema puede listar las etiquetas del repo del producto y crear
  `needs-triage` si falta, con las credenciales de `gh`. Deja de abrir issues `agentic` desde
  los hallazgos: ninguna corrida nace sola de un hallazgo.
- **Costo.** El pase corre modelo: a lo sumo un pase resuelto por tarea, hasta dos
  lanzamientos de `resume`, cada intento bajo el tope de 10 USD. Una llamada más a
  `gh label list` por seguimiento archivado, a lo sumo una por tarea. Nada escala con el
  tamaño de los datos.

## Riesgos conocidos

1. **`outside` lo elige el revisor** y nada lo contrasta con el diff. Lo acotan dos cosas: todo
   `outside` termina en un issue `needs-triage` que una persona lee, y el registro crudo
   permite auditar el abuso.
2. **Un stage0 viejo** archiva un `outside` en su seguimiento `agentic`: hay que re-fijar los
   dos stage0.
3. **`bl-*` nuevo.** El prompt de `tech_lead_review` cambia su huella.
4. **Comentario duplicado.** Si un merge reintentado falla entre el comentario y la fusión, el
   comentario `Auto-merged:` puede quedar repetido.
5. **Carrera de la etiqueta.** Si dos aterrizajes crean `needs-triage` a la vez, el segundo
   degrada a comentario. Con más de 1000 etiquetas, `create` falla y también degrada.

## Enmienda (GH-354): el pase corre antes de abrir el pull request

**Qué cambia.** El pase de `fix_important` ya no espera al pull request. Cuando la revisión
final aprueba (la de `integration`, o la de la única subtarea si no hay integración) y quedan
abiertas filas `important` de alcance `owned` o `plan`, ninguna rebajada
(`reported_severity`), y el pase de la tarea no está gastado, `after_integrate` manda a un
nodo del sistema, `fix_important`, antes de `full_checks` y de `deliver`. El nodo reabre las
unidades dueñas con `landing_sync.reopen(..., "returned")`; la siguiente ola las reconstruye,
la integración se revisa de nuevo y solo entonces corren `full_checks` y `deliver`: la suite
corre una vez, sobre el head que se entrega. Las garantías de arriba no cambian: dentro de la
tarea, un solo pase (`LANDING_IMPORTANT_PASSES_MAX = 1`, antes o después del pull request),
ningún issue de seguimiento para trabajo dentro del alcance.

**Por qué esta palanca y no un pase por subtarea.** Las filas que quedan abiertas al final son
de dos clases que una subtarea no puede ver: las de alcance `plan` y las que levanta la
revisión de `integration`. Una revisión por subtarea más estricta no las alcanza, y un pase
dentro de cada subtarea tampoco. El lector `agentic eval findings` separa el origen de cada
fila (revisión de la dueña, de la integración o re-revisión del aterrizaje) y su alcance
(`owned`/`plan`/`outside`); es la fuente de esa separación y de las cifras de éxito, que se
miden con él sobre una ventana fijada y no se citan aquí sin su salida. La cifra gruesa de
tareas que corrieron `fix_important` mezclaba `outside` con `plan` y no se usa.

**Qué lleva la entrada.** Una entrada de `landing` con `step: "fix_important"`,
`refusal: "open_findings"`, `before_pr: true`, `at`, `cost_base` y, si se conoce, `head`. La
razón y la categoría de la primera fila van al `reason` y al `change_category` de la unidad
reabierta; la entrada del historial lleva el par y `by: "landing"` (`returns.is_return` la
lee como no-devolución) y `changes_requested` no se toca, así que nunca escala. La resuelve
el fan-in (`pre_pr_fix.close_pass`) cuando las unidades reabiertas y la integración vuelven a
estar aprobadas.

**Quién cuenta el pase.** `rules.important_pass_spent` es la única respuesta: la usan la ruta
previa al pull request, `landing_plan` (`LandingSpent.important_passes`) y
`MergeFacts.important_pass_done` (con `resolved_only`: el merge lee el pase como hecho solo
cuando está resuelto). Tras un pase resuelto antes del pull request la tabla del aterrizaje
salta `fix_important` por sí sola. Las filas que la revisión posterior al pase levanta de
nuevo aterrizan en "Accepted without a fix", como hoy: por eso la proporción de PR que abren
con importantes abiertos no llega a cero.

**Lectores de `status: "returned"`** (cada uno, una línea):

- `rules.ready_subtasks` y `rules.landing_open`: solo enrutan (qué unidad se despacha, si hay
  un pase en vuelo).
- `rules.full_checks_return`, `review`, `qa_performance`, `final_validation`: escriben la
  devolución y la cuentan (`changes_requested`).
- `landing_sync.reopen` y `pre_pr_fix.fix_important`: escriben un reabrir del sistema
  (`by: "landing"`); no cuentan.
- `develop` (el delta que lee el Developer): solo enruta.
- `landing._status_of`: solo enruta (vista de `resume_owed`).
- `metrics` y `telemetry` (resultado del span): etiquetan, no cuentan devoluciones.

**Lectores de `landing`**, con la marca `before_pr`: `rules.landing_open` (una entrada previa al
pull request nunca es un pase bajo un pull request; solo enruta), `landing.resume_owed` (por
eso no se lanza un `resume` por ella; enruta), `landing_plan.spent` y
`MergeFacts.important_pass_done` (cuentan el presupuesto, antes o después), `rules.closeness`
(no lee `landing`: la cercanía sigue siendo `delivered` solo con `pull_request`),
`task_scores._followups` (una entrada previa al pull request no es un aterrizaje que corrió),
`returns.finding_origin` (solo cuentan los pasos con pull request) y `findings_report`
(distingue `fixed_before_pr` de `fixed_after_pr`).

**Ruta de las lecciones transversales.** Las lecciones de hallazgos `integration-crosscut`
van al playbook del planificador (`playbook._owner`, `_PLANNER`) y llegan al Developer solo
como invariantes del plan. No se agrega ningún almacén; el límite es el de 12 lecciones por
rol.

**Observabilidad (§7).** Sin métrica, etiqueta ni atributo de span nuevos. Clave opcional
nueva en las entradas de `landing`: `before_pr` (fila `landing` de `observability/README.md`).
**Seguridad y costo (§8).** Nada ensancha lo que una entrada puede alcanzar. Costo: a lo sumo
un pase de Developer más una revisión por tarea, bajo el tope de 10 USD por intento: el mismo
pase de arriba, ahora antes del pull request.
