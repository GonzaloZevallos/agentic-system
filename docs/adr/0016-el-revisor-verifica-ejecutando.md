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
tabla acumulada entre revisiones, con estado por hallazgo, es del issue #42 (adenda de
ADR-0015), que lee `scope: plan` de acá.

**La categoría es cerrada.** Doce valores en cuatro familias: `standard-*` (arquitectura,
tipado silenciado, costuras, otro), `spec-*` (comportamiento, test faltante, alcance),
`process-*` (sin commitear, suite en rojo), `plan-calibration`, `plan-invariant` (issue
#42) e `integration-crosscut`,
cada uno con ejemplo en el prompt del revisor. La devolución toma la categoría del primer
bloqueante y queda en `subtasks[id].return_category` y en `return_history`, el historial
de devoluciones del estado, junto con el par y el motivo. Las devoluciones de PM a Product
Analyst entran con la categoría `definition`: su esquema no cambia en este paso. Medición
emite `agentic.task.returns` con una muestra por categoría, label `return.category`, y el
span del rol lleva `agentic.role.return_categories` (ADR-0008, adenda).

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

**El producto declara sus chequeos en `.agentic/stack.yaml`**, con el mismo patrón que
`.agentic/flags.yaml`: el sistema trae un default en `core/knowledge` (un solo chequeo,
`tests: pytest`) y el producto lo reemplaza entero. Bajo la clave `checks`, cada chequeo es un
nombre y el comando exacto. El archivo se llama por el **stack del producto**, no por los
chequeos, porque es donde va a vivir todo lo que depende de la tecnología elegida: cómo se
prepara el entorno (issue #37) y qué no se puede escribir en este stack (issue #38). El sistema
impone la práctica; la herramienta la elige el producto. No se parsean `CODING_STANDARDS.md` ni
la CI.

**Vale la versión del commit base de la tarea**, la del arranque de la corrida
(`git show <base>:.agentic/stack.yaml`), nunca la de la rama. Una tarea que cambia el
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

## Adenda (2026-09-17, segunda): declarar el stack es obligatorio

El sistema traía un default (`pytest` a secas) al que caía cualquier producto que no
declarara `.agentic/stack.yaml` en el commit base. Silencioso y peligroso a la vez: un
producto que escribe `check:` en vez de `checks:` corría con ese default, y la puerta de
entrega y el revisor verificaban con un comando que no era el suyo, sin que nada lo dijera.

**`preflight` corta si el producto no declara su stack**, igual que ya cortaba con un
`flags.yaml` roto: antes de gastar un peso, no a mitad de tarea. Lo lee en el `HEAD` del
producto, que es el commit base de la próxima tarea que arranque, y carga las dos claves,
así un `setup` que no parsea también falla ahí y no cuando nace un worktree a mitad de
corrida. Falla también si el archivo está en la rama pero no en el commit: la versión que
vale sigue siendo la del base.

**El default deja de ser alcanzable por una corrida.** El archivo del paquete queda, pero
sólo como plantilla que el mensaje de error imprime, así lo que el humano copia es
exactamente un archivo válido y no un string que se desincroniza.

## Adenda (2026-09-22): el experimento de modo de revisión, protocolo y regla de decisión

El issue #28 (README §9, punto 1) prueba una segunda hipótesis de revisión: por subtarea en
Sonnet, sin `code-review`, con entrada acotada a los invariantes del plan, el brief, el
diff, el resultado de la puerta de entrega (adenda de arriba) y la tabla de hallazgos, que
solo reporta `blocking` con evidencia ejecutada — `TECH_LEAD_REVIEWER_LIGHT` y su prompt
`tech_lead_review_light.md`. `Configuration.review_mode` (`full` por default) y
`reviewer_for(checks, subtask_id, mode=...)` (`roles.py`) eligen el rol; la integración
recibe siempre el revisor completo con `code-review`, en cualquier modo, porque sin ese paso
caro el brazo B no prueba nada. Esta adenda registra el protocolo y la regla; la corrida es
un acto posterior al merge y el addendum queda vacío de resultados.

**Protocolo.** Los dos brazos corren sobre una sola base fijada, en **un clon**, en
secuencia: `--fresh` archiva el brazo anterior bajo `archive/run<N>-<sha>/` (ADR-0011,
séptima adenda). Nada se mergea durante el experimento; los pull requests del brazo A se
cierran sin mergear y se corre `agentic close` sobre cada uno, porque `_eval_run` llama
`_refuse_open_pull_request` por cada issue del set y `_close` rechaza sobre un pull request
`OPEN` — sin ese paso el brazo B no puede arrancar. `AGENTIC_REVIEW_MODE=light agentic eval
run` alcanza a cada hijo sin tocar `eval run`: `_run_eval_child` ya hace
`{**os.environ, **overlay}`.

**Regla de decisión.** El extremo es el **costo pareado por issue**; el puntaje binario de
§9 es una compuerta, no el extremo: una diferencia de una tarea sobre n=5 no decide nada
(ADR-0014, adenda 2026-09-15, corridas 2 y 3 de GH-12 divergiendo sobre la misma entrada).
`k=3` corridas de humo por brazo. B gana si iguala el puntaje binario con menos costo, o lo
mejora; si gana, pasa a default y esta adenda se actualiza con el resultado, y A queda como
opción (nunca se borra — ver adenda de ADR-0013 de hoy).

**Lo que el brazo B no puede medir.** El revisor liviano de subtarea no recibe `ORIGINAL` ni
la sección de PM (`TECH_LEAD_REVIEWER_LIGHT.reads == ("Tech Lead", FINDINGS)`): no tiene el
PRD delante, así que no puede fundar un hallazgo `spec-*` contra la definición del producto.
Una columna `spec-*` limpia en el brazo B **no es un resultado de calidad** — es la ausencia
de la entrada que haría posible encontrar algo ahí. El guardia de esta lectura es que
`agentic.review.findings{category=~"spec-.*"}` en `review.kind="integration"` **no debe
subir** en el brazo B respecto del brazo A: si sube, lo que la subtarea liviana no vio llegó
sin filtrar hasta la integración, y eso sí cuenta contra B aunque su propio puntaje binario
empate.

## Adenda (2026-10-29): la evidencia de aterrizaje baja a observación, sin devolver ni escalar

En apostrophe GH-385, GH-429 y GH-448 el revisor de subtarea pidió evidencia que solo da el
CI del pull request (línea base de cobertura, tiempo de pared, e2e en shards) como
`blocking`. Mientras se revisa una subtarea ese pull request no existe: lo abre la entrega
después de la aprobación (D-d506fe31). Ningún Developer puede arreglar el pedido, así que
tras dos devoluciones la tarea escalaba con "un humano tiene que abrir el PR". La regla del
dueño (D-7e11ef60) dice de dónde sale esa evidencia: de la primera corrida verde del pull
request.

- **El campo.** `Finding.landing_checks` es una lista de nombres de checks de CI, con
  `default_factory=list`. Un revisor la llena solo para pedir evidencia de aterrizaje y los
  prompts de revisión (`tech_lead_review`, `tech_lead_review_light`) le dan la compuerta con
  `{{landing_gate}}`: los nombres de `ci_gate_names(...)` o `none: this product declares no
  CI`. No se agrega categoría: `CATEGORIES` y las etiquetas de medición no cambian.
- **El orden de las bajadas.** `rules.lower_landing_blocks` corre primero en `review()`:
  todo hallazgo con `landing_checks` no vacío pasa a `observation`, sea cual sea su
  severidad y su alcance, con `reported_severity` igual a lo que dijo el revisor. Después
  `lower_unbacked_blocks` y, al final, `ci.ci_findings`, que por eso nunca se baja. La
  decisión lee solo `severity` y `landing_checks`, nunca el texto. La conversión no cuenta
  como devolución, no escala, no reintenta el rol y no depende de que el producto declare
  `ci:`.
- **La clave del registro.** `FindingRecord.landing_checks` se escribe solo cuando la lista
  no está vacía, con la misma regla de omisión que `reported_severity`: un documento sin el
  campo no cambia.
- **El riesgo conocido.** Un revisor que bloquea solo en prosa, sin llenar el campo, sigue
  el camino de antes y escala. Es a propósito: el sistema no lee la prosa.
- **Hash y esquema.** Cambian el hash de los dos prompts de revisión y el esquema de salida
  estructurada de `Review`: es un cambio observable de la corrida, no un cambio de formato.
