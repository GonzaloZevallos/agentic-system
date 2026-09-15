---
status: accepted
date: 2026-09-15
---

# El revisor verifica ejecutando: hallazgos con evidencia y tabla en el doc

En las corridas 2 y 4 de GH-12 hubo cinco devoluciones Tech Lead→Developer con motivos que
se repiten: tipado silenciado (`type: ignore`, `cast(Any)`, `dict[.., Any]`), un checker de
imports ciego a los relativos (devuelto y arreglado en la corrida 2, aprobado con el hueco
en la 4, devuelto en integración), un fix sin commitear y un tope numérico mal calibrado.
Nada de eso era medible: el motivo era un texto libre por subtarea y `returns` un contador
por par, así que README §3 ("mismo motivo entre los mismos roles") y §4 ("cantidad y motivo
de devoluciones") no se podían computar (issue #20). Y el revisor devolvía leyendo: no
corría nada, y aprobó en la corrida 4 un checker que la integración después rompió.

Cuatro búsquedas sobre sistemas con el mismo fin convergen en que la revisión de solo
lectura es el eslabón débil:

- Agentless: +6.3 pp por 0.25 USD por issue con test de regresión y de reproducción
  (arXiv 2407.01489, tabla 4).
- AgentForge: sin Tester −14 pp, sin Critic −4 pp (arXiv 2604.13120).
- Reflexion: sin tests 52%, por debajo de la base de 60% (arXiv 2303.11366, tabla 3).
- Jin & Chen 2026: los revisores LLM rechazan código correcto y empeoran al pedirles
  explicación (arXiv 2603.00539).
- El revisor de Codex ejecuta código (alignment.openai.com/scaling-code-verification).

**El revisor corre los tests que respaldan una objeción.** `TECH_LEAD_REVIEWER` suma
`Bash(uv run pytest:*)` y `Bash(pytest:*)` en su checkout y sigue sin escribir. Toda
objeción de comportamiento trae el test o comando que corrió y su salida; la que no se
reprodujo baja a observación. La suite completa no la repite: la registra el Developer, y
la puerta de entrega (issue #22) la va a escribir en el documento. Correr tests sin
escribir no es ejecutar (glosario de Proceso): el Developer sigue siendo el único rol que
ejecuta.

**La revisión entrega hallazgos, no un veredicto.** `Review.findings` es una lista de
`Finding` con `location`, `claim`, `evidence`, `severity` (`blocking`, `important`,
`observation`), `scope` (`subtask`, `plan`) y `category`. El caso de uso deriva el
veredicto: devuelve si y solo si hay un bloqueante. Un bloqueante sin evidencia, o con
alcance de plan, es salida inválida, es decir fallo de rol con reintento. `reason` queda
como resumen de una línea para el Developer. La sección de la revisión lleva los hallazgos
como tabla. La tabla acumulada entre revisiones, con estado por hallazgo, es del issue #23,
que lee `scope: plan` de acá.

**La categoría es cerrada.** Once valores en cuatro familias: `standard-*` (arquitectura,
tipado silenciado, costuras, otro), `spec-*` (comportamiento, test faltante, alcance),
`process-*` (sin commitear, suite en rojo), `plan-calibration` e `integration-crosscut`,
cada uno con ejemplo en el prompt del revisor. La devolución toma la categoría del primer
bloqueante y queda en `subtasks[id].return_category` y en `return_history`, el historial
de devoluciones del estado, junto con el par y el motivo. Las devoluciones de PM a Product
Analyst entran con la categoría `definition`: su esquema no cambia en este paso. Medición
emite `agentic.task.returns` con una muestra por categoría, label `return.category`, y el
span del rol lleva `role.return_categories`.

**El costo.** El pase de revisión corre tests: más turnos y más segundos por pase, y un
revisor con permiso de correr `pytest` puede correr la suite aunque el prompt le diga que
no. Lo aceptamos: una devolución evitada vale una revisión más un Developer (3.67 USD la de
`expiring-soon`), y el permiso sigue sin escribir. La lista cerrada puede quedarse corta:
`standard-other` es la salida, y si se llena se abre una categoría nueva, que cambia la
huella de prompts.

## Opciones descartadas

- **Categoría única por revisión, en `Review`, además del `verdict`**: lo decidido primero
  en el issue #20. Una revisión devuelve por varios motivos, y el veredicto escrito a mano
  puede contradecir los hallazgos; derivarlo lo impide.
- **Motivo libre clasificado después por un modelo**: otra llamada por devolución, y una
  clasificación que nadie verificó al momento de devolver.
- **Revisor que corre la suite completa**: la paga dos veces, con el Developer; lo que
  falta es la reproducción puntual de la objeción.
- **Revisor que escribe tests**: sería otro rol que ejecuta, con su jaula y su riesgo sobre
  la rama del Developer.
