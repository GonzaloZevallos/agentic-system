---
status: accepted
date: 2026-10-27
---

# Una corrida de `PM: final validation` asienta sus propias filas, y el aterrizaje no reinicia un rechazo que sólo es del libro

GH-374. Una tarea aprobada por PM, con CI verde, fue rechazada cuatro veces por
`open_findings_blocking` y el diagnosticador de aterrizaje eligió `restart`. Los bloqueos
eran unas 20 filas `blocking | plan` de `PM: final validation`: cada ronda reescribe sus
criterios de aceptación con otras palabras, porque PM no es incremental, y ninguna fila se
cerraba porque nadie las daba por saldadas. Ningún cambio de código arregla eso, así que
reiniciar tira una cabeza aprobada para volver a chocar con lo mismo.

Enmienda ADR-0015 (una cuarta excepción a "una fila se cierra sólo porque un revisor la dio
por saldada") y ADR-0048 (el aterrizaje no reinicia un rechazo que sólo es del libro).

## Decisión

1. **Lector único.** `rules.pm_final_approved_version(state)` devuelve la `version` de la
   última corrida de `PM: final validation` mientras las devoluciones PM→Tech Lead de
   `change_history` no la alcanzan, y `0` si no. Es el único lugar que cuenta corridas de PM
   contra devoluciones; `final_validation_due` lo lee con el mismo comportamiento.
2. **Escritora.** `domain/plan_settlement.py` es el único lugar que decide qué filas de ese
   lugar se asientan. Una corrida aprobatoria en V asienta toda fila abierta de versión < V
   (`by: "approved"`). Una corrida que devuelve en V asienta, como `superseded`, las filas
   anteriores que no vuelve a levantar: la última palabra de PM prevalece. El resultado va al
   parche de la corrida en `plan_settled: list[PlanSettledRow]` y nunca se deriva al leer.
3. **Pliegue.** `rules.findings_rows` cierra un registro cuando un `PlanSettledRow` con la
   misma clave normalizada tiene versión mayor, registro por registro, junto a `_covered`,
   `qa_settles` y `full_checks_settles`, que no cambian. Un documento sin `plan_settled` se
   pliega igual que antes.
4. **Aterrizaje.** Con `ledger_only` (el rechazo es `open_findings_blocking`, la unidad
   revisada está aprobada con CI verde en la cabeza y toda fila bloqueante abierta es `plan`
   de `PM: final validation`) el paso es `settle` si `plan_settlement.pending` no está vacío,
   y escribe exactamente esa lista; si está vacío hay una escalación `questions` y luego
   `none`. `exhausted()` y `after_diagnosis` cambian `land_slice`/`restart` por escalación
   sólo bajo ese predicado. No hay una novena clase de escalación.
5. **Relleno.** En un documento anterior, `pending` produce las filas desde
   `pm_final_approved_version`, de modo que `settle` hace el relleno.

## Alternativas rechazadas

- Atar la aprobación a una cabeza y arrastrarla con `review_carries`: PM no revalida tras una
  sincronización limpia, así que la cabeza no dice nada nuevo y la cuenta sería mayor.
- Una bandera `resume --settle`: abre un canal humano para cerrar filas.
- Detectar reformulaciones de un criterio: heurística sin dueño; la ronda completa ya sustituye.

## Riesgos aceptados

- Una sincronización limpia del aterrizaje después de que PM aprueba no vuelve a correr PM:
  la aprobación vale sin revalidar.
- Sustituir por ronda completa supone que PM no omite un criterio por descuido; es el riesgo
  que ADR-0015 ya aceptó.
- La lectura literal de "nunca reiniciar con cabeza aprobada y verde" se acota a su cláusula
  `open_findings_blocking`: un conflicto real sobre una cabeza aprobada conserva la salida de
  ADR-0048.
- Ninguna ruta de asentamiento lee texto libre del humano ni de un rol para decidir qué
  filas se cierran.
