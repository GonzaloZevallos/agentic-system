---
status: accepted
date: 2026-09-22
---

# El revisor verifica el negativo de un artefacto de enforcement; no lo escribe

La corrida 4 de GH-12 dejó dos defectos que la revisión de integración marcó como
bloqueantes y que revisiones de subtarea habían aprobado. El issue #17 proponía tres
salidas. Dos ya están decididas y una no, y eso es lo que esta decisión fija.

**La opción 1 ya está construida** (issue #42) y esta tarea no la rehace:
`SolutionTurn.invariants` (`schemas.py:159`), la siembra entre retakes (`interview.py:52`,
`rules.interview_progress`), `### Invariants` en `tech_lead.md` y `tech_lead_platform.md`,
`{{invariants}}` en `developer.md:27`, `developer_integrator.md:20` y
`tech_lead_review.md:18`, la categoría `plan-invariant`, `rules.invariants_markdown` y
`agentic.interview.invariants{state}`; y la tabla de hallazgos con `rules.finding_records`,
`findings_rows`, `open_findings`, `settled_findings`, `FINDINGS` en
`TECH_LEAD_REVIEWER.reads` (`roles.py:302`) y en `DEVELOPER.reads`, agregada en
`review.py:85` y plegada en el fan-in.

**La opción 3 está cerrada por ADR-0015**: *"El revisor no gana memoria de sesión. Se
evaluó y se descartó"*, y `tech_lead_review.md:12` se lo dice al modelo textualmente. La
memoria que esa opción pide es el contexto de *otra rama*, que es justo lo que ese ADR
argumenta que no hay que arrastrar; y la revisión se despacha dentro del subgrafo por
subtarea (ADR-0004, quinta adenda), así que una sesión compartida serializaría lo que esa
adenda midió en 693 s en serie contra 179 s en paralelo. Adoptarla pide un ADR que
supersede a ADR-0015 y otra tarea.

Queda la opción 2, y esta decisión la toma.

## El revisor es de solo lectura: el negativo se verifica, nunca se escribe

`TECH_LEAD_REVIEWER.allowed` es `READ_CODE_RULES + ("Agent",)` (`roles.py:306`) más los
chequeos declarados, y su hard limit dice "You write nothing: no file, no commit". No
puede escribir un archivo que viole la regla, ni correr Python arbitrario, ni cambiar de
commit. Toda forma de esta decisión que le pida **construir** el caso negativo es texto
muerto, y por eso la obligación está escrita como una verificación de la entrega.

Esto se deja dicho primero porque es la restricción que le da forma a todo lo demás: sin
ella, el próximo lector vuelve a proponer que el revisor escriba el test que falla.

## Un bloqueo con forma de ausencia lo baja el sistema, en silencio

La primera redacción decía: si falta el test negativo, bloqueá. No funciona.
`rules.lower_unbacked_blocks` (`rules.py:92-96`) baja a `observation` todo hallazgo
`blocking` con `evidence` vacío; después `change_category` devuelve `None` y la subtarea
queda **aprobada** con una nota bajo `Lowered by the system`. Contra un test que no
existe no hay comando que correr, y el prompt admite exactamente dos formas de evidencia:
el comando con su salida, o la línea exacta del estándar o del brief. Una obligación con
forma de ausencia produce justo la aprobación que esta tarea existe para impedir, y peor
que hoy, porque la sección diría que el revisor miró.

Por eso la afirmación es de **cobertura de costura**, no de ausencia, y las dos ramas son
exhaustivas:

- Si el artefacto está en una de las costuras declaradas de la subtarea: correr los tests
  de esa costura por nombre con un chequeo declarado; si pasan y ninguno ejercita el
  artefacto sobre una entrada que debe rechazar, es `blocking`, `scope: subtask`,
  `spec-missing-test`, con esa corrida en verde y la línea `Seams:` del brief como
  evidencia.
- Si el artefacto no está en ninguna costura declarada de esta subtarea, la edición misma
  es `blocking`, `scope: subtask`, `standard-seams` ("a declared seam bypassed"), con la
  línea `Seams:` del brief como evidencia.

La segunda rama no es un borde teórico: en GH-12 `expiring-soon` editó
`tests/test_architecture.py`, que era el entregable de `arch-test`. Un antecedente que
exigiera que el artefacto estuviera en una costura *propia* habría sido falso en el caso
exacto que originó el issue.

Toda subtarea declara al menos una costura (hard limit de `tech_lead_platform.md`) y las
costuras llegan al revisor dentro del brief proyectado (`plan_view._brief`), así que la
cita existe siempre y `lower_unbacked_blocks` no toca ninguna de las dos ramas. Ninguna
categoría nueva: las dos ya están en la lista cerrada con ese significado, así que
`FindingCategory`, `Severity` y `FindingScope` no cambian y ningún valor de label nuevo
entra a `agentic.review.findings`.

## El disparador es extensional, decidible sobre el diff

"Código cuyo propósito es rechazar otro código" es un juicio de intención, y el mismo
archivo prohíbe bloquear por un juicio suelto. El disparador que se escribe en los prompts
es enumerativo: *un test o una aserción que falla cuando el código de otro archivo está
mal; una función que devuelve o levanta sobre una lista de violaciones; un guard, un
checker o un validador invocado desde un chequeo que el producto declara.* Los dos
artefactos de GH-12 caen ahí sin que nadie razone sobre propósitos: `imports_of` y `place`
de `tests/test_architecture.py`, y el filtro `node.level` de `expiring-soon`.

El `<Hard Limits>` del revisor no se edita: "tests missing at a declared seam" ya licencia
las dos ramas, y agregarle una cuarta cláusula invita a leerla como una clase de bloqueo
más ancha que la frase que la define.

## Una obligación de revisión sin obligación de construcción es una trampa

El Developer no puede enterarse de esta regla por su brief ni por `tdd`, que le pide un
test que falla para *su* funcionalidad, no una entrada rechazada para un artefacto de
enforcement. Devolverlo por algo que nadie le pidió gasta una devolución contra un límite
de 2 y es la forma que `docs/product/role-failures.md` ya nombra como defecto. Así que la
misma frase, con el mismo disparador, entra en `developer.md` y en
`developer_integrator.md`: el integrador es el rol cuya entrega juzga la pasada de
integración, y dejarlo afuera reconstruiría la trampa una ola después, en la pasada más
cara.

## Cuándo dispara

En **toda pasada cuyo diff agrega o edita el artefacto**. En una revisión incremental el
artefacto suele quedar fuera del diff y la obligación no cuesta nada, que es la asimetría
correcta: la v1 es la puerta real, y la v1 es donde `expiring-soon` se aprobó. La revisión
de integración difea la rama entera en su primera pasada, así que vuelve a disparar sobre
cada artefacto que ve ahí por primera vez, al costo de esa pasada ($6.74 en la corrida 4).
Eso se compra a sabiendas: es la pasada que encontró los dos defectos de GH-12.

## Alcance honesto

Esto cierra el defecto 2 de GH-12 (`expiring-soon`, donde `node.level == 0` volvió
invisible la violación y la revisión citó el filtro como su propia prueba) y **no** cierra
el defecto 1 (el fold que falta en `recurring_revenue`). Ese es una regla de
comportamiento entre subtareas, que es para lo que existe el canal de invariantes ya
construido en #42. Queda escrito para que nadie lea este ADR como una promesa rota la
primera vez que reaparezca un bug con forma de fold.

## Cómo se juzga

`agentic.review.findings` deja "one sample per (severity, category, status)" con
`review.kind` y `system.version` (`telemetry.py:785-793`). Sus labels **no** incluyen
`prompts.fingerprint`, así que —a diferencia de toda serie segmentada por huella, que este
commit parte (ADR-0019)— ésta es comparable de un lado al otro del cambio. La señal es
`{severity="blocking", category="spec-missing-test"}` y `{category="standard-seams"}`. Se
escribe acá para que nadie le agregue un label de huella a esa familia sin saber qué
cuesta.

`max_turns` del revisor queda en 20 (`roles.py:308`): el prompt ya prohíbe volver a correr
un chequeo entero, así que la obligación agrega una invocación dirigida por artefacto, no
una suite. `roles.py` no entra al diff — `max_turns` es el único número de este cambio que
nada en la tarea puede testear, y moverlo sería una edición no verificable viajando con
una verificable. `agentic.role.failures{reason="max_turns", role.slug="tech_lead_review"}`
es la serie que diría que la estimación estuvo mal.

## Consecuencias

- El diff son tres archivos bajo `agentic/core/process/domain/prompts/`
  (`tech_lead_review.md`, `developer.md`, `developer_integrator.md`) más este ADR.
  `prompts.fingerprint` se mueve y la línea base anterior a este commit deja de ser
  comparable para toda serie segmentada por huella; queda dicho acá, en
  `### For the next role` y en el cuerpo del pull request.
- Ningún placeholder nuevo: la obligación viaja con `{{checks}}` y `{{invariants}}`, que el
  revisor ya recibe, así que `preflight.PLACEHOLDERS` no cambia y la puerta de arranque de
  todo producto sigue igual.
- `TECH_LEAD_REVIEWER.reads` no se ensancha y `plan_view.for_reader` no se saltea: la
  obligación es local al diff del propio revisor, así que `test_context_golden.py` queda
  verde sin tocarse y es una de las pruebas de que no cambió comportamiento.
- Ningún test se escribe contra el texto de un prompt: un test que rompe al reformular una
  frase sin cambio de comportamiento es lo que el estándar §5 llama mal. El costo aceptado
  es que una reescritura futura puede borrar la obligación en silencio, y lo único que lo
  notaría es la serie de arriba.

## Opciones descartadas

- **Que el revisor escriba el caso negativo**: no puede (arriba), y darle permiso de
  escritura sería otro rol que ejecuta, con su jaula y su riesgo sobre la rama del
  Developer, que ADR-0016 ya descartó.
- **Bloquear por la ausencia del test**: lo baja `lower_unbacked_blocks` y termina en
  aprobación.
- **Una categoría nueva de hallazgo**: `spec-missing-test` y `standard-seams` ya dicen
  exactamente esto, y un valor nuevo cambia una lista cerrada que es label de métrica.
- **Editar el `<Hard Limits>` del revisor**: la cláusula que licencia el bloqueo ya está.
- **No tocar `developer.md`**: deja una obligación de revisión sin obligación de
  construcción, que se cobra en devoluciones contra un límite de 2.
