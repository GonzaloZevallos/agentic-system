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
veredicto: devuelve si y solo si hay un bloqueante. Un bloqueante sin evidencia baja a
observación, y uno con alcance de plan baja a importante, porque el Developer no lo puede
arreglar en su subtarea. Los baja el sistema y lo anota en la sección: no es fallo de rol,
porque un campo mal llenado no puede costar un reintento ni escalar la tarea. En la
revisión de integración la rama tiene todas las subtareas, así que un problema entre ellas
es de la subtarea y puede bloquear. `reason` queda
como resumen de una línea para el Developer. La sección de la revisión lleva los hallazgos
como tabla, y el sistema le antepone `### Verdict` con el veredicto derivado y el motivo:
el revisor no escribe esa línea, así que la sección no puede contradecir lo decidido. La
tabla acumulada entre revisiones, con estado por hallazgo, es del issue #23, que lee
`scope: plan` de acá.

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
- **Bloqueante sin respaldo como fallo de rol**: lo primero que se implementó. Un segundo
  fallo escala la tarea entera por cómo el revisor llenó un campo, y cada reintento es
  otra vuelta de una revisión de 2.5 a 8.5 USD.
- **Revisor que escribe tests**: sería otro rol que ejecuta, con su jaula y su riesgo sobre
  la rama del Developer.

## Adenda (2026-09-15): el revisor corre los chequeos que el producto declara

En el rescate de GH-12 (PR gym-saas #13) el revisor aprobó dando por buenos `ruff` y `mypy`
porque lo decía la sección del Developer. Solo podía correr `uv run pytest` y `pytest`, y
tampoco esas reglas calzaban con los comandos reales de gym-saas (`uv run --no-project …`).
gym-saas no tiene `CODING_STANDARDS.md`, sus comandos están en el README y su CI solo corre
`pytest`: nada que el sistema pudiera leer (issue #22, absorbe #34).

**El producto declara sus chequeos en `.agentic/checks.yaml`**, con el mismo patrón que
`.agentic/flags.yaml`: el sistema trae un default en `core/knowledge` (un solo chequeo,
`tests: pytest`) y el producto lo reemplaza entero. Cada chequeo es un nombre y el comando
exacto. No se parsean `CODING_STANDARDS.md` ni la CI.

**Vale la versión del commit base de la tarea**, la del arranque de la corrida
(`git show <base>:.agentic/checks.yaml`), nunca la de la rama. Una tarea que cambia el
archivo no cambia la vara con que se la mide: el revisor lo anota como observación y el
cambio rige desde la tarea siguiente, después del merge. Si la tarea trae un chequeo nuevo,
como `ruff` y `mypy` en GH-12, es el plan del Tech Lead el que pide dejar el código en
verde con ese chequeo.

**La puerta de entrega corre todos los chequeos** en el checkout del Developer y escribe el
resultado al principio de su sección (ADR-0011, adenda segunda). Lo escribe el sistema:
el Developer ya no copia las últimas líneas de la suite.

**El revisor corre los chequeos declarados.** El sistema convierte cada comando en una regla
`Bash(<comando>:*)`, que reemplaza las dos reglas fijas de `pytest`. El prompt los lista y
le pide empezar el comando tal como está declarado y agregarle el archivo o el test. Los usa
para reproducir una objeción puntual. Un chequeo entero no lo repite: ya lo corrió la
puerta. Correr chequeos sin escribir sigue sin ser ejecutar.
