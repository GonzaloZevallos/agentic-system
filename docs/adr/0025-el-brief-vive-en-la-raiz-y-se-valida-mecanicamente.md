---
status: accepted
date: 2026-09-22
---

# El Brief vive en la raíz del producto y se valida solo en su forma, nunca en su contenido

Product Analyst define comportamiento y Tech Lead decide un stack sin ningún norte escrito:
qué es el producto, para quién y con qué restricciones. Un gimnasio de 20 socios y uno de
200 mil piden cosas distintas y eso no vive en ningún lado (grill del 2026-09-15). Entra el
**Brief**: lo que el humano escribe una vez por producto, antes de la primera tarea, con el
problema, los jobs y las restricciones. No es un PRD — no describe comportamiento.

## Por qué la raíz y no `docs/product/`

La primera forma obvia, `docs/product/BRIEF.md`, rompe por construcción, no por descuido del
prompt:

- `FilesystemKnowledge._read_dir(PRODUCT_SUBDIR)` lee todo `.md` de ese directorio como una
  **capacidad**. `BRIEF.md` entraría al mismo diccionario que `check-ins.md`.
- `write_definitions` reemplaza cada capacidad entera con lo que PM aprueba. Un slug
  `brief` pasa `check_slug` (es un slug válido), así que la primera tarea que PM apruebe
  con una definición de esa capacidad **borra el Brief del humano** sin que nada lo avise.
- `check_definitions` exige que cada capacidad tenga un `## Jobs`. El Brief lo tiene por
  diseño (una de sus cuatro secciones se llama justo así): sería un casi-acierto que pasa
  la validación de PM por la razón equivocada.

Puesto en la raíz del producto — junto a `CONTEXT.md`, `CONTEXT-MAP.md` y
`CODING_STANDARDS.md`, donde ya vive todo artefacto humano de reemplazo entero (ADR-0012:
`standards()` reemplaza el default del sistema con lo que encuentra en la raíz) — las tres
rutas de escritura automática (`consolidate`, `write_definitions`, los entrevistadores, que
solo tocan `docs/product/`, `CONTEXT.md` y `docs/adr/`) no tienen forma de alcanzarlo. Lo
que el sistema garantiza es que ningún escritor automático llega a `BRIEF.md`; lo que
mantiene a un futuro Developer afuera de todos modos es el prompt y la revisión, no la
jaula — el mismo registro que la primera adenda de ADR-0011 ya usó para la identidad de git:
una regla de dependencias impide una clase de error, no todas.

## Las cuatro secciones, el tope, y el conjunto de aceptación

`BRIEF_HEADINGS` es su propia tupla en `core/knowledge/domain/knowledge.py` — **Problema y
quién lo tiene**, **Jobs**, **Restricciones**, **Fuera de alcance** —, deliberadamente sin
importar `JOBS_HEADING`: la coincidencia de `## Jobs` con la sección de una definición es a
propósito (mismo formato de job story) pero los dos contratos quedan desacoplados, así que
un PE que mueva uno no mueve el otro por accidente.

`brief_refusal(text: str) -> str | None` es una función pura sobre texto, sin filesystem ni
port, con el conjunto de aceptación/rechazo enumerado caso por caso en
`tests/core/knowledge/domain/test_knowledge.py`:

- Las cuatro secciones tienen que aparecer como líneas `##` de **primer nivel**, en
  cualquier orden; secciones `##` extra se permiten e ignoran; un `###` con el mismo título
  no cuenta — la regex solo calza un `##` justo al principio de una línea, seguido de
  espacio, así que un tercer `#` la rompe por construcción, sin lista de exclusión.
- El calce de título recorta espacio final y **a lo sumo un** `:` final, sin distinguir
  mayúsculas: "## Jobs:" y "## JOBS" valen lo mismo que "## Jobs".
- Cada sección necesita **al menos una línea no vacía** antes del próximo `##` o el fin del
  archivo — comparación por línea, no por substring, que dejaría pasar el caso
  presente-y-vacío que el pedido explícitamente rechaza.
- El tope de palabras cuenta sobre el texto con los bloques de código (```` ``` ````)
  quitados primero, encabezados incluidos: `BRIEF_MAX_WORDS = 500`, una constante y no un
  cálculo — `preflight` no gasta nada antes de este chequeo, y cada rol `reads_knowledge`
  paga este costo en cada llamada (~2% de los ~24k palabras que ADR-0017 midió para todo el
  ensamblado).
- El rechazo imprime `N words (cap 500)` y nombra el archivo; el aviso de ausencia y el
  rechazo de forma imprimen **la plantilla completa, verbatim** — el mismo patrón
  `stack.TEMPLATE`/`not_declared` (ADR-0016, segunda adenda): lo que el humano copia es
  exactamente un archivo válido, nunca una cadena que se desincroniza de la validación real.

## La asimetría: aviso en la ausencia, rechazo en la forma

`preflight.py` **no se toca** — sigue siendo byte a byte igual en este diff (evidencia:
`git diff --stat -- agentic/core/process/application/preflight.py` sin salida). Ya codifica
esa misma distinción para `setup` y `forbidden`: `stack.checks`/`stack.product`/
`flags.load` rechazan, mientras que `no_setup` y `no_forbidden` son **hechos que imprime la
CLI**, nunca refusals de `preflight`.

Un Brief que nunca se escribió no declaró nada mal: rechazarlo pararía cada `agentic run`
sobre gym-saas — el producto sobre el que se mide el set de evaluación (README.md, 9) — el
día del merge. `no_brief(ports)` entra entonces a la lista `for warn in (unfixed_stage0,
no_setup, no_brief)` de `cli.py`, impresa en ambos comandos.

Un Brief **presente y roto** (forma mal hecha, o sobre el tope) es distinto: `run` lo
rechaza (`raise ValueError`, capturado por el mismo `except (MissingProductDir, ValueError,
OSError)` que ya cierra `main` con código 2) y `resume` solo lo imprime y sigue. La razón es
ADR-0003: un humano editando su propio archivo no puede varar **cada tarea pausada** en la
puerta de arranque sin que haya ninguna tarea nombrada ahí — el mismo fallo que ADR-0003
existe para evitar. Y en un `resume`, en el momento en que `no_forbidden(ports, base)` ya
corre, ni siquiera se estaría midiendo el texto que la corrida efectivamente va a leer (el
Brief que un checkout de tarea ya pudo haber editado, ADR-0014) — otra razón para no
convertirlo en un refusal ahí.

Las dos verdades (ausente vs. presente-y-roto) son funciones separadas en `cli.py`:
`no_brief(ports)` solo habla quando `ports.knowledge.brief()` es `None`; `brief_refusal
(ports)` solo habla cuando hay texto y `knowledge.brief_refusal(text)` lo rechaza. Ninguna
pisa el hecho de la otra.

## Dónde vive cada mitad, y por qué está forzado

ADR-0010 prohíbe que cualquier módulo bajo `core/process/` importe `core.knowledge`, y
`tests/test_architecture.py` ya lista exactamente esa violación. Un helper en
`core/process/application/brief.py` habría fallado un chequeo declarado a mitad de la
subtarea. La forma que queda:

- **El veredicto**, `brief_refusal(text) -> str | None`, es una función pura de dominio en
  `core/knowledge/domain/knowledge.py`. No toca disco, no conoce el port.
- **El port**, `ProductKnowledge.brief(root=None) -> str | None`, solo devuelve texto — nunca
  un veredicto cruza el port. `FilesystemKnowledge.brief` refleja `standards()`: lee
  `root/BRIEF.md` y devuelve `None` si no existe. A diferencia de `standards()`, no hay
  default del sistema: la ausencia es un hecho de `cli.py`, nunca un texto de reemplazo que
  una corrida termine leyendo.
- **`cli.py`** es el único módulo que une las dos mitades — el composition root, la única
  capa con permiso de importar `core.knowledge` fuera de `composition.py`. `no_brief` y
  `brief_refusal` (funciones de `cli.py`, homónimas de la de dominio pero a otro nivel: una
  junta port + veredicto, la otra es el veredicto puro) viven junto a la partición
  HEAD-vs-base que `no_forbidden` ya tiene, no una nueva.

## `assemble` recibe un cuarto parámetro, y va primero

`assemble(glossaries, capabilities, adrs, brief=None)` antepone `_fenced(BRIEF, brief)`
—`BRIEF.md`, no bajo ningún subdirectorio— antes de los glosarios, siempre que haya texto.
Sigue devolviendo `""` cuando no existe nada: el Brief es una parte más, nunca una razón para
que aparezca `## Product knowledge` con el resto vacío. `FilesystemKnowledge.read` pasa
`self.brief(root)` como ese cuarto argumento; el Brief llega así a exactamente el mismo
conjunto de roles `reads_knowledge` que ya reciben el resto del conocimiento de producto —
ningún rol nuevo, ninguna `Role` field tocada. Las restricciones de un Developer le siguen
llegando por el plan de Tech Lead, no por el Brief directamente: nada en este diff hace que
un Developer lea `BRIEF.md` distinto de como ya lee `## Product knowledge`.

## Lo que el pedido pedía y este diseño descarta

El pedido original pide una segunda capa de validación: "PM valida el contenido en la
primera tarea del producto". Es estructuralmente inalcanzable en este sistema, no una
elección de estilo:

- `graph.py` saltea la etapa de definición entera para un platform enhancement (ADR-0017);
  el Brief de este mismo repo no tiene PM en ningún momento de su ciclo.
- No hay ningún dato, en el estado ni en el documento, que diga "esta es la primera tarea
  del producto". Inventar uno sería una nueva pieza de estado, RECORDED_AS incluido, para
  una regla que nadie pidió con esa precisión.

La forma se hace cumplir mecánicamente (`preflight`/`cli.py`); el contenido lo juzga quien
lo lee en su prompt, por el canal que PM ya tiene — una pregunta para el humano (ADR-0014) —
nunca un rol nuevo. `approval_check` queda explícitamente descartado como portador: dispara
en **toda** aprobación, y convertiría un Brief sin arreglar en un fallo de rol en cada tarea
futura del producto, no solo en la primera.

## Ni prompt ni fingerprint se tocan

Este diff no toca `core/process/domain/prompts/`: ningún rol lee en su prompt que existe un
Brief distinto de como ya lee `## Product knowledge` (la misma forma que el testigo del
checkout, ADR-0024, usa para no mover `prompts.fingerprint`). `prompts.fingerprint` no se
rompe en esta subtarea — la rompe la otra, `rename`, y lo dice en su propia ADR-0026 y en el
cuerpo del pull request que la trae.

## Las costuras

- `tests/core/knowledge/domain/test_knowledge.py`: el veredicto puro, con el conjunto de
  aceptación/rechazo enumerado caso por caso (encabezado ausente, presente-y-vacío,
  `###` que no cuenta, orden libre, dos puntos y mayúsculas normalizados, tope de palabras,
  bloques de código descontados) y `assemble` con el Brief primero.
- `tests/core/process/application/test_context_golden.py`: sin cambios de producción — sigue
  byte a byte igual, porque `MemoryKnowledge.read()` devuelve texto ya armado y nunca pasa
  por `assemble`; se le agrega `.brief()` solo para cumplir el protocolo `ProductKnowledge`.
- `tests/test_cli.py` (la costura de `agentic run`/`resume`): seis casos de punta a punta
  contra un repo real — ausente en `run` y en `resume` (aviso, la corrida sigue), roto en
  `run` (código 2), roto en `resume` (aviso, la corrida sigue), sobre el tope en `run`
  (código 2), y válido (silencio).

## Opciones descartadas

- **`docs/product/BRIEF.md`**: la forma que este diseño existe para evitar — ver la primera
  sección.
- **Reusar `JOBS_HEADING`** para el encabezado `## Jobs` del Brief: acopla dos contratos que
  cambian por razones distintas; un PE que mueva la sección de una definición movería sin
  querer la del Brief.
- **Un refusal dentro de `preflight()`**: pararía cada tarea pausada de cada producto sin
  ninguna tarea nombrada ahí (ADR-0003) el día que el humano edite su propio archivo.
- **`approval_check` como portador de la validación de contenido**: dispara en toda
  aprobación, no solo en la primera tarea del producto — que además no es decidible con el
  estado de hoy.
- **Un rol nuevo que valide el Brief**: el pedido lo descarta explícitamente ("sin rol
  nuevo"); además, no hay ninguna etapa del camino platform que lo pudiera alojar.
