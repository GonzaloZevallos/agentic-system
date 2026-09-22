---
status: accepted
date: 2026-09-22
---

# La puerta de entrega verifica contra qué código corrieron los chequeos, con un testigo que declara el producto

Desde #37 cada checkout prepara su propio entorno y los chequeos deberían medir el código de
la rama que se entrega. Nada lo verifica. Antes de #37 un `pytest` en un worktree importaba
`app` desde el checkout principal y la suite verde no decía nada sobre la rama: ADR-0011
(tercera adenda) lo midió en GH-12 — 18 `ModuleNotFoundError`, 110 apariciones de
`VIRTUAL_ENV`, y un `uv pip install -e .` adentro de un worktree reescribiendo el editable
install compartido, de modo que las corridas 2, 3, 4 y el rescate corrieron con la suite de
las subtareas hermanas importando el código de otra rama.

## La forma obvia no prueba nada

`CheckoutPresubmit._run` lanza cada chequeo como su propio `bash -c`. Una sonda corrida en
otro proceso prueba que **la sonda** resolvió adentro del checkout, no que lo haya hecho el
proceso que corrió `pytest` diez segundos antes. Y peor: `ShellCommands.run` le da a todo lo
que lanza el entorno de `without_own_toolchain(without_own_configuration(...))`, el mismo a
los chequeos y a la sonda, así que una sonda que el sistema componga está de acuerdo con los
chequeos **por construcción**. Un verificador que no puede fallar por el motivo para el que
existe es lo que ADR-0023 llama `spec-missing-test`.

De ahí que el comando lo escriba el producto y que la afirmación esté acotada por escrito.

## La afirmación, acotada

Lo que la puerta afirma, literal, y lo que va en la sección del Developer: **el testigo
declarado, corrido en el mismo checkout y con el mismo entorno que reciben los chequeos,
resolvió adentro del checkout.** Es una medición correlacionada, tan ajustada como el modelo
de procesos permite; no es una garantía sobre el `bash -c` que corrió la suite. Deducir la
ruta efectiva de la salida de cada chequeo queda descartado: `domain/presubmit.py` y el
`errors="replace"` de `ShellCommands` son el mismo compromiso de que la salida de un chequeo
se lee y no se parsea.

## Una clave opcional, un comando, un veredicto

`witness` en `.agentic/stack.yaml`: **una cadena**, no un mapeo `nombre -> comando` como
`checks` y `setup` — un testigo, un veredicto, una línea, ningún juego de nombres nuevo que
etiquetar o tabular. Entra en `stack.KEYS`, con `stack.witness(revision) -> str | None` en el
port `ProductStack` y en `GitStack`, leído en la base de la tarea por el mismo `_declaration`
que `checks` y `setup`. Clave ausente ⇒ `None`, el producto no declara ninguno. Clave presente
con valor en blanco o que no es cadena ⇒ `ValueError` al cargar, la misma regla de "declarado
pero vacío es un archivo a medio escribir" que `setup` aplica; `""` nunca es un valor que
`stack.witness` devuelva, así que `None` es inequívoco. Es un escalar y tiene su propia
validación de dos líneas: `_commands` está hecho para mapeos.

El sistema no compone nunca el comando ni busca un directorio. Un heurístico que olfatee
`.venv` metería uv y Python adentro de `core/process`, sería falso para gym-saas —cuyos
chequeos son `uv run --no-project …` (ADR-0016, adenda)— y contradiría la tercera adenda de
ADR-0011, donde `setup` es opcional a propósito. El sistema impone la práctica; la herramienta
la elige el producto.

La regla que el sistema sí posee vive en `domain/presubmit.py`: la **última línea no vacía**
de la salida, stripeada, tiene que ser una ruta absoluta que, resuelta, caiga bajo el
checkout. La última línea y no "alguna línea que resuelva adentro", porque `ShellCommands`
mezcla stderr en stdout a propósito y un testigo que imprimiera una ruta adentro y otra afuera
pasaría. Los dos `.resolve()` son de `CheckoutPresubmit`, la capa que tiene filesystem; el
dominio recibe la salida, el estado de salida y un checkout ya resuelto, y hace álgebra pura
de texto y `is_relative_to`. Así la costura A queda sin infraestructura (estándares §5) y la
evidencia del symlink queda en la costura B, donde hay un worktree de verdad.

## El orden del veredicto

1. **Timeout** ⇒ rechaza con el motivo del timeout, sin leer el texto.
2. **Salida distinta de cero** ⇒ rechaza con el motivo del exit, sin leer el texto.
3. Recién entonces, la última línea no vacía de la salida **cruda**, antes de que `said` la
   anote.

Sin este orden el veredicto intentaría resolver como ruta el `"(timed out after N s)"` que
`presubmit.said` agrega: un verificador tropezando con su propia nota.

## Dónde corre, y qué no puede pasar

El testigo corre **adentro** de la rama `if moved and clean` de `CheckoutPresubmit.__call__`,
primero, antes de los chequeos, y **los chequeos corren igual**. Adentro de esa rama porque
`test_a_call_before_a_commit_leaves_no_invocation` fija que la puerta no lanza nada antes de
que haya algo que medir, y esa propiedad tiene que cubrir también al comando nuevo. Y los
chequeos corren igual porque saltearlos dejaría `self.results` en `None`, o sea `markdown()`
en `None`: la entrega pasaría, agotados los rechazos, **sin tabla `### Checks` en la sección
del Developer** y sin `presubmit_checks` en la patch, ni muestra de
`agentic.presubmit.checks`. ADR-0016 (adenda) hizo de esa tabla la palabra del sistema en vez
de la del Developer; cambiarla por unos minutos no es un ahorro.

`presubmit.refusal` **no cambia de firma**. El testigo tiene su propio predicado puro,
`witness_refusal(...) -> str | None`, consultado antes. El motivo es concreto:
`develop.committed` llama a `presubmit.refusal(moved, is_clean, [])` después de la corrida y
hace `raise ValueError(reason)`, que es un **fallo de rol** con reintento y escalada al
segundo. Un desacuerdo de testigo no puede convertirse en eso.

El rechazo del testigo tiene **su propio contador, tope 1**; `REFUSALS = 2` y `self.refusals`
quedan intactos. El día malo más probable de un mecanismo nuevo es una mala declaración, y que
eso se coma el presupuesto de la puerta que ya funciona haría de esta tarea una pérdida neta.
Un rechazo le da al Developer la chance de re-sincronizar su worktree; un segundo no dice nada
nuevo y solo gasta un turno. **Para un producto que declara testigo, el máximo de rechazos por
corrida de Developer pasa de 2 a 3**, dicho acá para que un tercer rechazo en una transcripción
se lea como diseñado.

## Ni chequeo, ni métrica

El testigo no es un chequeo: fuera de `self.checks`, fuera de `self.invocations`, fuera del
juego de valores del label `check` — ningún valor nuevo en `agentic.presubmit.checks`, ningún
nombre de métrica, label o clave de documento renombrado (ADR-0008). `timeout_s` pasa a
`check_timeout_s * max(len(checks), 1) + (check_timeout_s si hay testigo)`: un techo, no un
gasto, y el sumando existe solo cuando hay testigo declarado.

Aparece como su propia línea bajo `### Checks` en `checks_markdown`, escrita por el sistema,
**siempre que un testigo haya corrido**, esté de acuerdo o no: legible para el revisor y para
el humano, invisible para un panel. Nadie edita el dashboard en este commit.

La consecuencia se acepta a sabiendas: un desacuerdo persistente **no deja serie**. Se ve como
rechazo en el turno y como línea en la sección, y agotado el tope la entrega pasa con esa
línea y nada en Grafana. Si alguna vez necesita panel, es una familia nueva
(`agentic.presubmit.witness`), nunca un cuarto valor de `check` sobre una serie que ADR-0021
ya movió de 3 a 4.

Como ningún prompt se toca (`prompts.fingerprint` no se mueve), el Developer no lee en su
prompt que existe un testigo. Una obligación que el rol no puede leer tiene que explicarse
sola en el momento en que dispara: el texto del rechazo es una constante del dominio que
nombra el comando declarado, lo que imprimió, cuál es el checkout y qué hacer —apuntar el
toolchain a este checkout, no subir al entorno del padre—, nunca un "witness mismatch" pelado.

## La puerta de arranque y la plantilla

`preflight.declared_stack` llama a `ports.stack.witness(revision)` solo para validarlo y
**sigue devolviendo `ports.stack.setup(revision)` sin cambios**, así el aviso de `no_setup`
queda byte a byte igual. Sin esa llamada, un `witness: [una, lista]` pasaría la puerta y
moriría adentro de un nodo, que es justo lo que el docstring de `declared_stack_of` dice que
esa puerta existe para evitar. Nunca lo exige: gym-saas, que no declara testigo y es el
producto donde el bug muerde, sigue corriendo igual por `preflight` y por cada entrega.
`PLACEHOLDERS` no cambia.

La `TEMPLATE` (`knowledge/domain/stack.yaml`, lo que imprime `not_declared`) suma la clave
**comentada**, con una frase de para qué es. Sin comentar, le entregaría a todo producto
futuro una clave que parece obligatoria.

## Este repo declara el suyo, y eso casi sale mal

La primera redacción de este plan decía "un `python -c` de una línea". Está mal:
`without_own_toolchain` saca `VIRTUAL_ENV` y todo `UV_*` y quita el `bin/` de ese venv del
`PATH`, así que un `python` pelado en un worktree nuevo resuelve fuera de todo entorno de
proyecto y `import agentic` levanta `ModuleNotFoundError` — salida distinta de cero, o sea dos
rechazos por corrida de Developer en **toda tarea futura sobre este repo**, por un problema de
entorno que no existe. E indetectable en esta tarea, porque el stack se lee en la base y ningún
rol de acá ejecuta la cadena que se está shippeando.

Se declara entonces con el mismo runner de proyecto que los chequeos de este repo:

```yaml
witness: uv run python -c "import agentic, pathlib; print(pathlib.Path(agentic.__file__).resolve().parent)"
```

Lo que lo hace resolver es `setup: deps: uv sync --locked`, no un accidente del `PATH`. Y es
autoconsistente con lo que mide: en modo proyecto el intérprete sale del `.venv` del worktree,
así que la ruta que imprime es la del checkout por construcción cuando el aislamiento se
sostiene, y está afuera cuando se rompió — que es la única forma en que un testigo es un
testigo.

Para que la cadena shippeada esté probada antes de que un Developer la pague,
`tests/test_stack.py` gana un caso que lee `stack.witness(...)` del archivo, la corre con
`ShellCommands` en la raíz del repo y verifica que su última línea no vacía resuelva bajo la
raíz. Correr un subproceso en la suite unitaria ya tiene precedente acá:
`test_setup_for_real.py` usa git y shell reales porque "a fake cannot show that the commands
land in the checkout git just made", y esto es el mismo argumento una capa afuera. El testigo
**no** entra en `_declared_commands()` y **no** necesita paso `run:` en `ci.yml`: no es un
chequeo, CI no entrega nada y no tiene checkout que verificar, así que el contrato de una sola
dirección que ese archivo documenta queda como está.

## Las costuras

- **A**, `tests/core/process/domain/test_presubmit.py` (editado: es la costura del código
  nuevo): el veredicto puro con sus entradas rechazadas enumeradas — una ruta en un worktree
  hermano, una en el checkout padre, una relativa, salida vacía, última línea que es ruido,
  salida distinta de cero, testigo con timeout.
- **B**, el patrón de `test_setup_for_real.py` (`GitWorkspace` + `ShellCommands` + `GitStack`
  reales sobre un commit real): un worktree cuyo testigo declarado imprime una ruta **afuera**,
  verificando que la puerta rechaza con el motivo del testigo, y su gemelo que imprime una
  adentro y no rechaza. Es la única evidencia de que el mecanismo puede fallar; ADR-0023 llama
  bloqueante a un artefacto de enforcement cuyos tests nunca ejercitan una entrada rechazada, y
  `MemoryCommands` devuelve resultados enlatados.
- **C**, `tests/core/process/application/test_presubmit_witness.py` (nuevo): la rama no-op y la
  aritmética del contador, sobre fakes.
- **D**, el caso ejecutante de `tests/test_stack.py`.

Una sola subtarea: un solo escritor (`presubmit`, `stack`, la declaración de este repo).
Partir el predicado del cableado le daría a dos Developers un mismo escritor y compraría una
barrera de fan-in para nada (ADR-0004, quinta adenda).

`CheckoutPresubmit` recibe el testigo como parámetro **keyword-only con default `None`**, así
todo sitio de construcción existente compila sin tocarse y `None` es literalmente la rama
no-op. `tests/core/process/application/test_presubmit.py`, `test_technical_stage.py` y
`test_context_golden.py` quedan byte a byte iguales en el diff, y eso es la prueba de que la
puerta de hoy no se movió; `tests/fakes.py` **sí** se edita (`MemoryStack` gana `witness`), y
eso no es una violación: el invariante es sobre los módulos que codifican comportamiento, no
sobre los dobles.

## Riesgos conocidos

- **El revisor no tiene testigo.** `review.py` corre los chequeos declarados en el mismo
  worktree del Developer (`cwd=checkout.path`) y `diagnose.py` hace lo mismo para el Product
  Analyst de bug, después de que el alrededor pudo moverse
  (`docs/product/changes-requested.md`). La verificación es de la puerta de entrega; la
  reproducción del revisor queda sin verificar, y lo único que cruza esa frontera es la línea
  del testigo en la tabla. Extenderlo costaría un comando por pase sobre un rol que no escribe
  nada y no puede arreglar un entorno (ADR-0016), y metería un segundo camino en un diff cuya
  afirmación entera es que solo se movió el del Developer.
- **Un testigo con otro runner que los chequeos es un verde falso.** Para gym-saas, cuyos
  chequeos son `uv run --no-project …`, un testigo escrito como `uv run …` resolvería en modo
  proyecto mientras cada chequeo resuelve afuera. No se fuerza la forma en código: un matcher
  de prefijo entre un testigo y N chequeos es lo que la cuarta adenda de ADR-0011 descarta —un
  carácter más estricto y rechaza una declaración legítima, y el primer producto rechazado
  sería justo gym-saas—. Se hace visible en vez de confiable: la tabla imprime el comando
  declarado **verbatim, al lado de los comandos de los chequeos**, y la línea comentada de la
  plantilla dice que se escriba con la misma forma de runner o verifica un proceso distinto del
  que midió el código.
- **El testigo no es de solo lectura.** Es un comando que escribe el producto y el sistema lo
  corre en el checkout: el de este repo (`uv run`) puede sincronizar y escribir en el `.venv`
  del worktree. No afecta el veredicto de árbol limpio —`.venv/` está en el `.gitignore` y el
  árbol ya se miró antes— pero el sistema no promete que correr el testigo no toque nada.
- **La declaración de este repo no rige en esta tarea.** El stack se lee en la base, así que el
  testigo entra en vigor desde la tarea siguiente al merge. Ninguna evidencia sale de la puerta
  de esta corrida; sale de las costuras B y D, que el Developer pega verbatim en su sección.

## Opciones descartadas

- **Verificar que el entorno de `setup` quedó adentro del checkout**, la segunda salida del
  pedido: hardcodea uv y Python en `core/process` y es falsa para un producto que legítimamente
  no declara `setup` (ADR-0011, tercera adenda).
- **Un mapeo `nombre -> comando`** como `checks`: un juego de nombres nuevo para etiquetar y
  tabular, sin nada que agregar.
- **Saltear los chequeos cuando el testigo discrepa**: deja la entrega sin tabla `### Checks` y
  sin muestra de presubmit justo en la entrega más sospechosa.
- **El testigo como un cuarto chequeo**: mueve `timeout_s`, el juego de valores de `check` y la
  tabla de una sola vez, y obliga a editar el dashboard en este commit (ADR-0008, ADR-0021).
- **Un contador de rechazos compartido con los chequeos**: el primer día malo del mecanismo
  nuevo debilitaría la puerta que ya funciona.
- **Parsear la salida de cada chequeo** para deducir la ruta efectiva: la salida de un chequeo
  se lee, no se parsea.
- **Partir predicado y cableado en dos subtareas**: un solo escritor, una barrera de fan-in
  comprada para nada.
