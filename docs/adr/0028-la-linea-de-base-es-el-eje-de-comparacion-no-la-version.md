---
status: accepted
date: 2026-09-23
---

# La línea de base es el eje de comparación, no la versión del sistema

El programa de mejora iterativa (2026-09-17 a 2026-09-23) cerró trece tareas del sistema
sobre sí mismo y diez de gym-saas, y al final no pudo decir con las métricas si el sistema
había mejorado. No por falta de datos: por falta de eje. Cada tarea corrió sobre su propio
`system_commit`, así que en Grafana hay 33 valores de `system_version` con una tarea cada
uno y `prompts_fingerprint` está igual de fragmentado (32 valores para ~50 tareas). El
noveno addendum de ADR-0008 hizo que toda métrica dijera qué versión la corrió; eso traza
una corrida, pero agrupar por versión da n=1 y las "medianas por versión" son el único
valor. La única comparación con muestra que salió del programa fue por cohortes
cronológicas hechas a mano: fallos por tarea 3.6 → 2.5 sobre 29 tareas, costo y retrabajo
sin cambio.

## Decisión

1. **La línea de base es un hito declarado por el humano con un tag de git en master**, con
   la forma `bl-YYYY-MM-DD`. `stage0.sh` lee `git describe --tags --abbrev=0` del ref que
   pinnea y lo pasa como `AGENTIC_SYSTEM_BASELINE`; sin tag, el valor es `untagged` y la
   corrida sigue. Toda familia de métricas que lleva `system.version` lleva también
   `system.baseline`. La versión sigue existiendo para trazar; la línea de base es el eje de
   todo panel que compara.

2. **El brazo del experimento es un label**, `arm`, con la forma canónica
   `review_mode=light,skip_ephemeral=true` y el literal `default` cuando ningún interruptor
   se movió. Va en las mismas familias que `system.baseline`. Hasta hoy los brazos de #28
   vivían sólo en el doc de tarea y en el JSONL de `agentic eval score`; Grafana no veía el
   experimento.

3. **El veredicto es un scorecard de cuatro métricas en orden**: autonomía (entregas
   `merged` limpias sobre tareas terminadas), fallos propios por tarea (excluyendo
   `reason=limit`), costo por entrega y retrabajo. Una línea de base mejora si la primera
   no baja y la segunda baja; las otras dos desempatan. Es una consulta sobre las muestras
   (PromQL en Grafana, lectura de docs en `agentic eval compare`), nunca una métrica
   emitida: ADR-0008 dice que una métrica es un evento y el scorecard es una lectura.

4. **La lectura es manual por hito.** Con una a cuatro tareas por línea de base una alerta
   dispara por ruido; entra cuando una línea de base junte diez tareas terminadas.

## Lo que no se hace

- No se rellena la telemetría ya emitida (ADR-0008, noveno addendum): en Grafana la
  comparación por línea de base arranca en `bl-2026-09-23`. La historia anterior se compara
  desde los docs, donde `agentic eval compare` asigna `pre-baseline` a todo `system_commit`
  anterior al primer tag.
- No se reemplaza `baseline_id` del set de evaluación: es más fino (incluye la declaración
  y el brazo) y sirve para otra pregunta.
- No se hace la línea de base obligatoria: negarse a pinnear sin tag frena el programa por
  un olvido, y `untagged` es visible en el panel de Control.

## Consecuencias

- Dos labels nuevos entran al contrato de ADR-0008 ("se agregan, nunca se renombran"), con
  cardinalidad acotada por definición: la línea de base la crea el humano, el brazo es el
  producto de tres enums.
- El dashboard se reorganiza en cuatro filas: Scorecard, Experimentos, Diagnóstico,
  Control. Cuatro métricas que hoy nadie lee ganan panel porque responden una pregunta del
  scorecard: `delivery.time_to_merge`, `delivery.human_commits`, `role.failure_cost_usd`
  por motivo, `role.turns` sobre `role.max_turns`.
- `agentic close` deja de ser un paso manual por PR: `agentic close --all` barre los PRs
  cerrados sin veredicto, y `time_to_merge_seconds` se persiste en el doc como los otros
  cuatro campos del cierre.
- La clasificación de fallos tiene un bug que el scorecard hace visible: 49 fallos `sdk`
  y cero `limit` en catorce días, cuando el vocabulario ya tiene `limit`. Hasta que se
  arregle, "fallos propios por tarea" mide la cuota de Anthropic.

El glosario de Medición (`agentic/core/measurement/CONTEXT.md`) incorpora Versión del
sistema, Línea de base, Scorecard y Brazo en este mismo cambio.
