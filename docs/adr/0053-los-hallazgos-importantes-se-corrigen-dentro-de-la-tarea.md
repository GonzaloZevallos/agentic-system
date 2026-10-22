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
