---
status: accepted
date: 2026-09-22
---

# El sistema se aplica a sí mismo un type checker, con el alcance en la configuración y no en el comando

`CODING_STANDARDS.md` §6 exige tipado estricto y un type checker en verde a todo producto,
y el sistema —que desde ADR-0017 es su propio segundo producto— no se lo aplicaba: CI corre
ruff y pytest, y `.agentic/stack.yaml` declara esos tres chequeos y ninguno de tipos. El
código, sin embargo, está escrito contra un checker que nunca se declaró: 598 `def`s con 21
sin anotación de retorno, 26 menciones de `Any` en 8 archivos, y tres `# type: ignore` en
`task.py` (503, 504, 546) **con código de error**, que sólo escribe alguien que corrió mypy.

Nadie en el camino de esta tarea puede correr mypy para medir: el Tech Lead planifica con
`allowed=READ_CODE_RULES` (`roles.py:246`) y `check_rules` sólo extiende `reviewer_for` y
`diagnoser_for` con los chequeos **declarados**, que todavía no incluyen tipos. Así que el
alcance no se elige adivinando una lista de paquetes: se fija una lista ordenada de unidades
y el Developer, que es el único rol que ejecuta, reporta los conteos.

## El chequeo se declara sin rutas y el alcance vive en `[tool.mypy] files`

`checks.types` es `uv run mypy`, sin rutas, y el alcance es la lista `files` de
`[tool.mypy]` en `pyproject.toml`. Tres razones, y la tercera es la que importa:

- El comando es una cadena **byte a byte idéntica** en `.agentic/stack.yaml` y en
  `.github/workflows/ci.yml`, para siempre: ningún PE futuro que amplíe el alcance tiene que
  tocar dos archivos, así que la deriva entre ellos no puede ocurrir por construcción.
- La regla de permiso que `check_rules` le da al revisor y al Product Analyst de bug pasa a
  ser `Bash(uv run mypy:*)`, un prefijo que un revisor extiende con una ruta para reproducir
  una objeción, que es para lo que ese mecanismo existe (ADR-0016, adenda).
- El registro de **qué falta por chequear** es una lista revisable en un archivo, no una
  línea de comando que nadie lee.

## La regla del commit base fija qué comando corre, nunca cómo se configura la herramienta

Hay que decirlo con precisión porque un lector de ADR-0016 va a trazar el borde en el lugar
equivocado en las dos direcciones. ADR-0016 fija la vara en el **commit base de la tarea**
—`declared_checks` es `ports.stack.checks(task_base(state, ports))` (`presubmit.py:21-24`),
y de ahí leen la puerta de entrega (`develop.py:72`), el revisor (`review.py:45`) y el
diagnóstico (`diagnose.py:33`)— y eso vale para **el comando**. Nunca valió para la
configuración de la herramienta, y no podía: `uv run ruff check .` ya lee `[tool.ruff]`
(`line-length`, `select`, `extend-exclude`) de la rama, y `uv run pytest -m "not llm"` lee
`[tool.pytest.ini_options]` de la rama. Una subtarea que hoy tocara `[tool.ruff.lint] select`
cambiaría su propia vara a mitad de tarea exactamente igual.

La consecuencia, escrita para que no se descubra en una devolución: **desde este commit, lo
que `types` mide es una propiedad de la rama y no del base.** La inmunidad que daba tener las
rutas en el comando no era general; era de la única dimensión que mypy expone dos veces.

De ahí la **regla permanente para todo PE que amplíe este alcance**: `files` se amplía
únicamente en el mismo diff que deja en verde las unidades que agrega. Nunca como trabajo de
configuración suelto, nunca en una ola anterior a los arreglos. Si no, ampliar en la subtarea
A pone en rojo la entrega de la subtarea B por código que B no tocó: un problema que cruza
subtareas en un plan que no declaró ninguno.

## El dial de estrictez, y cuál queda para después

`[tool.mypy]` lleva `python_version = "3.13"`, `files`, `disallow_untyped_defs`,
`disallow_incomplete_defs`, `check_untyped_defs`, `disallow_any_generics`,
`no_implicit_optional`, `warn_return_any`, `warn_redundant_casts`, `warn_unused_ignores` y
`warn_unused_configs`. Es `strict` menos `disallow_untyped_calls`,
`disallow_untyped_decorators` y `strict_equality`.

`disallow_untyped_calls` queda afuera porque dispara **incondicionalmente en cada sitio de
llamada** contra el SDK sin stubs, decenas de sitios, y forzaría un `cast` o un ignore en cada
uno. `warn_return_any` se queda, y la asimetría es el punto: dispara **sólo** donde nuestra
propia firma promete un tipo concreto y el cuerpo devuelve un `Any` sin verificar, que es lo
más valioso que un checker puede decir sobre una frontera a un SDK sin stubs. No dispara sobre
un literal de diccionario: `dict[str, Any]` devuelto donde se declara `dict[str, int]` es
compatible, así que `_usage` (`runner.py:467`) pasa sin coerción y sin cast.

`disallow_any_generics` se queda a propósito: es lo que caza los 22 `dict`/`list`/`tuple`
pelados, y `dict[.., Any]` es un motivo de devolución que este repo ya pagó (ADR-0016).

Subir a `strict` completo es el dial de un PE posterior, y el orden es fijo: **alcance
primero, estrictez después, un dial por PE.**

## Lo que no se puede escribir para poner el checker en verde

- **Ningún `typing.cast`, de ninguna forma**, introducido para satisfacer al checker. Un
  `cast(dict[str, int], raw)` es invisible para el hook de `hooks.py:132`, invisible para
  `warn_unused_ignores`, y estrictamente peor que un ignore con código, que al menos nombra
  el error y se retira solo cuando deja de hacer falta.
- **Ningún `# type: ignore` pelado ni ningún `# noqa`.** La forma sancionada para un
  estrechamiento genuinamente inexpresable es `# type: ignore[<código>]`, con su código, como
  ya son los tres de `task.py`. El hook cuenta ocurrencias, así que reemplazar un
  estrechamiento por otro es legal; agregar uno no.
- **Ningún `[[tool.mypy.overrides]]` para tapar un error de código nuestro.** El único uso
  legítimo es `ignore_missing_imports` para módulos de terceros **nombrados** y sin stubs, una
  entrada por paquete, con el paquete en un comentario y **nunca un `*` pelado**: un `*`
  silenciaría también nuestros imports futuros, que es la misma escapatoria con otro nombre.
- **`no-any-return` en la frontera del SDK** es el único código para el que un ignore con
  código está sancionado de entrada, con el módulo y el motivo en un comentario. Todo otro
  código se arregla anotando, o la unidad sale de `files`.
- **Nada de comportamiento en runtime se mueve para satisfacer al checker**: anotaciones,
  firmas e imports sí; una rama, un valor por defecto o el orden de una llamada no. Un PE no
  cambia comportamiento, y una tarea de anotaciones que voltea un default es una feature
  entrando de contrabando.

Cuando una unidad no se puede dejar limpia, **el alcance se achica y el código nunca se
silencia**: la unidad sale de `files` y queda nombrada en el cuerpo del pull request como
insumo del PE siguiente, que es el "ampliar por PE sucesivos" del pedido usado como modo de
falla y no sólo como camino feliz.

## El piso: por debajo, se retiene el impuesto y no la señal

Declarar `types` en `.agentic/stack.yaml` **no es gratis**: la puerta de entrega corre todos
los chequeos declarados en cada entrega, cada rechazo y cada reintento de cada Developer
(`presubmit.py:65`), en un worktree nuevo sin caché; el timeout del hook de entrega pasa de
1800 s a 2400 s (`presubmit.py:56`, `check_timeout_s * len(checks)`); y
`agentic.presubmit.checks` gana un cuarto valor de `check`. Eso se paga en toda tarea de
plataforma futura, cubra el chequeo mucho o poco.

El **piso** es `agentic/shared` más los tres paquetes `domain/` (`core/process/domain`,
`core/knowledge/domain`, `core/measurement/domain`). El fundamento es arquitectónico:
`CODING_STANDARDS.md` §1 les prohíbe importar frameworks, bases de datos o HTTP y
`tests/test_architecture.py` lo verifica en cada corrida, así que son los paquetes donde el
checker no tiene superficie de terceros sin stubs contra la cual pelear —los `Any` y el
`ignore_missing_imports` viven todos afuera del piso— y son donde `rules.py`, `task.py`,
`metrics.py` y `roles.py` deciden ruteo, costo, retrabajo y escalado: donde un error de tipo
cuesta más y es más barato de atrapar.

**Si el piso no se alcanza**, entran igual `mypy` en el grupo `dev`, `[tool.mypy]`,
`tests/test_stack.py` y **el paso de CI** —que no le cuesta nada al sistema por entrega,
porque corre sobre el pull request— y **no** se agrega `checks.types` a `.agentic/stack.yaml`,
con el motivo en el cuerpo del pull request. El humano sigue viendo el estado del checker en
cada PR; el sistema no empieza a pagar un chequeo por entrega antes de que el chequeo cubra
algo que valga la pena.

El piso es verificable por el único rol del loop que puede: el revisor no corre mypy, pero lee
dos archivos, así que **declarar `checks.types` con `files` por debajo del piso es un hallazgo
bloqueante** que puede respaldar con una cita de este ADR.

## El orden de las unidades

`agentic/shared` → `core/measurement/domain` → `core/knowledge/domain` → `core/process/domain`
→ `core/knowledge/adapters` → `core/measurement/adapters` → `core/process/application` →
`core/process/adapters` → `agentic/*.py`. Dirección de dependencias, con el piso como prefijo
y `process/adapters` anteúltimo porque ahí vive el SDK sin stubs, que es donde se gasta la
excepción de `no-any-return`. `agentic/plugins` queda excluido de entrada: es un submódulo
fijado y no es código nuestro, exactamente como ya lo excluye `[tool.ruff] extend-exclude`
(ADR-0013). `tests/` queda fuera del alcance de este PE y nombrado como insumo del siguiente:
chequearlo arrastra `conftest.py`, los fakes y la superficie de tipos de pytest, que es otro
cuerpo de trabajo. §6 no se debilita por escribir qué no está chequeado todavía; se debilita
por dejarlo sin decir, que es el estado de hoy.

## Ningún rol de esta tarea puede correr el checker, y el pull request es el chequeo de registro

Para toda GH-74, nada adentro del loop corre mypy: el base declara tres chequeos, así que la
puerta de entrega no lo corre, el revisor no recibe el permiso `Bash(uv run mypy:*)` y no puede
reproducir los conteos, y el veredicto sobre la única subtarea que importa lo escribe un rol
que no puede ejecutar la herramienta que la subtarea existe para instalar. La primera tarea que
se beneficia es la siguiente al merge.

En consecuencia: **la corrida de CI del pull request es el chequeo de registro de esta tarea**,
dicho acá y en el cuerpo del PR, en vez de dejar que un `Verdict: approved` insinúe una
cobertura que el revisor estructuralmente no tuvo. El Developer pega en su sección **el comando
y su salida verbatim**, por unidad, con la línea final (`Success: no issues found…` o `Found N
errors…`): es el único artefacto que alguien aguas abajo puede leer, y un conteo sin salida
pegada no es evidencia. Y el brief le dice al revisor que **no puede** reproducir esos conteos
y que esa imposibilidad no es motivo de devolución: ADR-0016 ya decide esa forma —una objeción
que no se reprodujo es una observación—. Lo que sí puede bloquear, y respaldar: la salida
pegada falta o no coincide con el diff, `files` contiene una ruta que la costura de pytest
rechaza, las dos cadenas del comando difieren, apareció un `cast` o un ignore pelado, o se movió
una rama de runtime.

La costura que **sí** está concedida en el base es `uv run pytest -m "not llm"`, y por eso
`tests/test_stack.py` (raíz, al lado de `test_dashboard.py`, que es el precedente de un test
sobre la configuración de este repo) verifica lo que se puede verificar sin correr el checker:
que todo valor de `checks` y de `setup` aparece verbatim como un paso `run:` de `ci.yml` —en esa
dirección solamente, porque un `run:` sin chequeo declarado es justamente el estado de "piso no
alcanzado", y los `uses:` no son comandos—; que `files` no está vacía, que cada entrada existe,
que cada entrada está dentro de `agentic/` y que `agentic/plugins` no está; y que
`astral-sh/setup-uv` lleva un `version:` explícito (ADR-0022). No es correr el checker, y no se
pretende que lo sea: es la diferencia entre un revisor que lee una afirmación y uno que corre
algo.

## Consecuencias

- `agentic.presubmit.checks` pasa de 3 a 4 valores de `check` por entrega, desde el commit en
  que se declare `types`. Se agrega, no se renombra (ADR-0008), y nada se rellena hacia atrás:
  un panel que cuente chequeos por entrega lee 3 antes y 4 después.
- El timeout del hook de entrega pasa a 2400 s. Es un techo, no un gasto.
- Desde el merge, el revisor y el Product Analyst de bug de toda tarea sobre este repo reciben
  `Bash(uv run mypy:*)`: el Tech Lead del PE siguiente ya no está en la posición de no poder
  medir en la que estuvo el de éste.
- Ningún archivo de `domain/prompts/` se toca: `prompts.fingerprint` no se mueve y las muestras
  de este PE siguen siendo comparables con la línea de base actual.

## Opciones descartadas

- **Una subtarea `gate` pagada aparte que sólo mida**, como `gate` en ADR-0011 (quinta adenda)
  y GH-70 en ADR-0020: ahí un hecho sobre el SDK fijado decidía si otra subtarea podía
  *existir*. Acá el conteo decide cuánto hace **una** subtarea, y el gate pagaría una corrida
  de Developer más su revisión para aprender lo que el mismo Developer aprende en su primer
  turno.
- **Las rutas en el comando** (`uv run mypy agentic/core/...`): inmune a que la rama cambie su
  propia vara, pero obliga a todo PE que amplíe a editar dos archivos en sincronía, que es la
  deriva que el encabezado de `stack.yaml` afirma que no existe y que hoy nada verifica.
- **Un tope de errores como compuerta** (los 150 de la primera versión de este plan): es una
  adivinanza sobre cuántos errores entran en 80 turnos, no un hecho sobre el código, y el costo
  de errarle no es un chequeo más angosto sino perder también la configuración, el paso de CI y
  el test. Queda como umbral de reporte.
- **`strict` completo de una**: dos diales a la vez, y el que cuesta cambios de runtime es
  justamente el que se posterga.

## Adenda (2026-10-08): los dobles de los puertos entran al alcance (GH-227)

mypy no veía `tests/fakes.py`, así que un doble (`MemoryStack`, `MemoryTracker`,
`MemoryWorkspace`...) podía dejar de cumplir su puerto de `ports.py` sin que nadie lo notara
hasta que un test llamara el método. El alcance gana dos rutas bajo `tests/`, no una carpeta:

- **El conjunto de excepción es de dos rutas**: `tests/fakes.py` (los dobles) y
  `tests/conformance.py` (el módulo que los asigna a sus puertos). El resto de `tests/` sigue
  fuera: es código sin tipar a propósito y abrirlo entero sería el dial de estrictez movido sin
  decirlo. `tests/conformance.py` no empieza con `test_` para que pytest no lo recoja.
- **La guarda se enmienda**: `test_every_mypy_file_is_inside_agentic` en `tests/test_stack.py`
  admite una entrada si es `agentic`, está bajo `agentic/` o está en la constante
  `MYPY_TEST_ENTRIES`, y otro test exige que las entradas de `tests/` en `[tool.mypy] files`
  sean exactamente ese conjunto: el registro y la guarda se amplían en el mismo diff. La
  exclusión del submódulo de plugins no cambia.
- **La clausura de `follow_imports` también se chequea**: los módulos que `fakes.py` importa
  (por ejemplo `adapters/github/tracker.py`) entran al chequeo. Bajo `agentic/` sólo se permiten
  ediciones de anotación que esa clausura exija; ninguna rama, valor por defecto u orden de
  llamadas se mueve. Cuando un doble y su puerto divergen, se corrige el doble, nunca
  `ports.py`.
- **La conformidad**: `ports_of_doubles()` construye `Ports(...)` con cada campo como argumento
  con nombre y una clase doble real, de modo que un campo nuevo sin doble es un error de mypy;
  `RunRegistry` y `Launcher` se asignan en una función que nunca se llama. El inventario de
  protocolos de `ports.py` lo vigila `tests/test_conformance_scope.py` (cada uno es campo de
  `Ports`, está en `COVERED` o en `UNCOVERED` con la razón y el `archivo:línea` de sus dobles
  en línea), y una sonda de deriva (ADR-0023) prueba que el chequeo muerde: una copia de
  `ProductStack` con una firma alterada es rechazada por mypy, y la copia sin alterar no.
- **Riesgo conocido y sin chequear**: los once dobles en línea de `Presubmit` (6), `SchemaGuard`
  (2) y `RunLock` (3), listados en `UNCOVERED`, más las dos subclases de
  `MemoryWorkspace` en `tests/core/process/application/test_presubmit_affected.py:32,121`, viven
  dentro de módulos de test fuera del alcance: pueden divergir de su puerto sin que mypy lo
  diga.
- **Seguimiento**: una tarea que mueva esos dobles a un módulo chequeado y vacíe `UNCOVERED`.
