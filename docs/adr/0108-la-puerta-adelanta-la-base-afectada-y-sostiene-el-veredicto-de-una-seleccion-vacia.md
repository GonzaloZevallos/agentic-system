---
status: accepted
date: 2026-11-26
---

# La puerta adelanta la base afectada y sostiene el veredicto de una selección vacía

GH-1069 y GH-1125. Enmienda el ADR-0046. En la puerta de entrega del Developer, la forma afectada
de un chequeo se medía siempre contra la base de la tarea; si `scripts/affected_tests.py` no
elegía nada (salida 8), la puerta corría el chequeo completo, hasta 1500 s, aunque el chequeo ya
hubiera pasado sobre casi todo ese árbol en la llamada anterior.

## Decisión

**1. Base adelantada.** Bajo `advance_base` (solo la `CheckoutPresubmit` de entrega que arma
`develop()`, nunca `run_check`, el merge ni el aterrizaje), la base de un chequeo es G, la
cabeza en que ese chequeo pasó por última vez en esa instancia, cuando existe, es ancestro de la
cabeza y `G..cabeza` cambió algo; si no, la de la tarea. Lo decide solo `forward_base.base_for`,
una vez por chequeo y por llamada; las rutas que lee `plan_form` y el `{base}` del comando salen
de ese mismo valor. Un `GitError` al leer G cuenta como ilegible y cae a la base de la tarea. Así
un archivo de `fallback` tocado antes de G ya no manda el chequeo a completo.

**2. Fuente verde.** G pasa a la cabeza solo si la fila final del chequeo pasó, no es un
timeout, un truncado, un `not_ready`, un reintento ni una fila sostenida, y el chequeo no está
`stuck_full`. Se actualiza bajo la misma condición de testigo que `_store_passes`: un testigo en
desacuerdo no mueve G.

**3. Veredicto sostenido.** Una forma afectada contra G que sale 8, sin timeout, con `G..cabeza`
no vacío, da una sola fila `reused_from` (G) con `passed` verdadero que no corrió nada: sin
reintento ni completo, y `settle_form` da `kept_green`. La salida 9, cualquier otra salida y un
timeout siguen el camino de antes (después corre el completo). La fila sostenida no se guarda en
el almacén (ADR-0065) ni mueve G. `reused_from` y `affected_base` solo los lee `kept_row`,
`settle_form` y la tabla de salida: ninguna decisión del sistema depende de ellos.

**4. Telemetría.** `decided_durations` salta solo `reused_from`: un chequeo sostenido no tiene
entrada, nunca 0, y no cambia `budget_breaches` de nadie. La celda `reused` de la puerta cuenta
ahora también las filas sostenidas.

**5. Contrato.** El veredicto confía en que `affected_tests.select` da `Empty` (8) solo si
clasificó todas las rutas; una que no se mapea da `Refusal` (9) y va a completo. Lo hace cumplir
`tests/test_affected_tests.py`.

## Consecuencias

- Lint, format y types no tienen forma afectada: solo sostienen veredicto con el árbol idéntico
  (ADR-0065). El reuso por entradas aplica a los chequeos con `affected_checks`.
- Un error de selección de `scripts/affected_tests.py` ya no se ve en la puerta sino en
  `full_checks`, como retrabajo.
- En esta parte G vive solo entre llamadas a la puerta dentro de una invocación del Developer
  (rechazo de lint o de testigo, reintento). Tras una devolución del Tech Lead vuelve a estar
  vacío; ese ahorro es de la parte 2 de GH-1069, que llena `green_heads` desde `RoleRun`.
- Por llamada se suma, a lo sumo, un `is_ancestor` y un `changed_paths` por cada G distinto.
