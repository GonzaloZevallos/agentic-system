---
status: accepted
date: 2026-09-22
---

# El guardia de pins verifica offline lo que ningún chequeo ejecuta

En #74 (PR #99) el Developer pinneó `astral-sh/setup-uv@v5` con `with: version: "10.2.0"`,
tomando la major de la *action* como si fuera una versión de uv. uv va por 0.12.x; el
tarball no existe y CI falló con `Unexpected HTTP response: 404`. El PR pasó la puerta de
entrega y la revisión del Tech Lead porque ninguno de los dos ejecuta el workflow ni
consulta un registro — `DEVELOPER.tools` no tiene `WebFetch`/`WebSearch` y el rol revisor
tampoco tiene `curl`/`gh`, ambos corren en `permission_mode="default"` (ADR-0011, cuarta
adenda). Lo corrigió un humano, la única intervención humana sobre código en el programa
de mejora hasta ahora.

## La clase de defecto es una sola

Un valor de versión viviendo en un archivo que ningún comando declarado bajo `checks` o
`setup` ejecuta jamás. Hoy `.github/workflows/**` es la única instancia. `uv.lock` queda
afuera: `setup: deps: uv sync --locked` ya lo valida y un setup fallido escala antes de
que corra ningún rol (ADR-0011, tercera adenda). El pin del submódulo de skills queda
afuera por decisión de ADR-0013 y ADR-0022: es un commit humano a propósito.

## Qué se construyó

Un guardia puro en `tests/test_stack.py`, corrido por `checks.tests` (`uv run pytest -m
"not llm"`), la única ruta que cubre su propio diff en la primera entrega — a diferencia
de las rutas de `stack.yaml` de ADR-0021 y ADR-0024, leídas en la base de la tarea.

- `unknown_pins(steps, table, observed=None, pinned_refs=None) -> list[str]`: función
  pura sobre YAML ya parseado. Camina **todos** los steps de `ci.yml`.
- Fail-closed por enumeración: cualquier `with:` cuya clave sea exactamente `version` o
  termine en `-version`/`_version`, cuyo prefijo de `uses:` esté ausente de `table`, falla.
  `table` es un parámetro, nunca un global del módulo, así un caso rechazante se escribe
  sin tocar la tabla real. Una entrada hoy: `astral-sh/setup-uv -> uv --version`.
- El oráculo de un step conocido es una afirmación de **espacio de nombres, no de
  igualdad**: el valor pinneado tiene que parsear como una versión literal única y
  compartir la **major** con la uv observada localmente. `10.2.0` contra `0.12.x` falla —
  la entrada de GH-74 —; `0.12.13` contra `0.12.14` pasa, así un bump de parche legítimo
  nunca rechaza una entrega correcta.
- Un valor que no es una versión literal única — `latest`, un rango, una expresión
  `${{ }}`, una cadena vacía — falla: todos reinstauran la uv móvil que `version:` existe
  para evitar (ADR-0022).
- Un `run:` con un token de versión (`==X.Y.Z`, `@N.N[...]`, un segmento de URL que
  parsea como versión) falla con "no oracle for this pin". La exención es conjuntiva:
  declarado bajo `checks`/`setup` **y** sin literal de versión. Sin la segunda mitad, un
  branch podría declarar `install: pipx install uv==10.2.0` bajo `stack.yaml` más su
  `run:` step y quedar exento por construcción — la forma exacta de GH-74 llegando por
  el otro lado.
- Un `uses:` cuyo ref cambió contra `PINNED_REFS` (`actions/checkout@v4`,
  `astral-sh/setup-uv@v5`, verbatim contra `ci.yml` de hoy) falla: un ref de `uses:` es
  un commit humano (ADR-0022); revertirlo y nombrar el bump en el cuerpo del pull request.
- Ambos parseos de versión —el valor del workflow y la línea de salida de `uv
  --version`— viven adentro de la capa pura; un único test posee el único subproceso.

## Lo que queda deliberadamente afuera

- **El revisor no verifica el pin.** Sus herramientas no tienen red ni contenedor; ver
  ADR-0011, cuarta adenda. "El revisor lo verifica" es texto muerto en este repo.
- **`act` o un dry-run del workflow**: Docker no está en `setup`, así que su primer tick
  escalaría antes de que corriera ningún rol, en cualquier tarea futura.
- **Consultar un registro**: hace una entrega correcta rechazable por el mal minuto de un
  tercero y gasta `REFUSALS = 2`, el mismo presupuesto que ADR-0024 le dio su propio
  contador al testigo para no gastar.
- **Una versión que llega por `env:`**, el interior de una *composite action*, una
  versión escrita en un archivo que el workflow sólo lee, y la **existencia** (no el
  movimiento) de un `uses:` ref: las cuatro son indecidibles sin red u otra
  infraestructura que este guardia no tiene.

## La cláusula de estándares

`CODING_STANDARDS.md` gana una sección nueva (§9) que apunta a la forma aceptada del
guardia en vez de restatearla — la misma disciplina anti-drift que el §6 ya aplica al
registro de mypy — y convierte la decisión de ADR-0022 en una obligación explícita del
Developer: un pin que ningún chequeo declarado ejecuta no se introduce ni se mueve, se
nombra en el cuerpo del pull request para el humano.

## Riesgos conocidos

- **El guardia es tautológico en CI**: la uv en `PATH` ahí es la que el pin instaló. Sus
  dientes están en el checkout de un Developer y en la máquina del humano, no en un tick
  verde de CI.
- **Un salto de major de uv** aterriza en rojo en la primera entrega de alguna tarea no
  relacionada, no en el humano; el mensaje de la aserción es el único canal de
  remediación, porque `presubmit.refusal` embarca sólo treinta líneas de pytest y ningún
  prompt menciona este guardia.
- **La cláusula de estándares es prosa no forzada** fuera de `.github/workflows/**`.
- **Un PR de Dependabot de `github-actions`** queda en rojo hasta que el humano edite los
  strings de ref pinneados en ese mismo PR — es la consecuencia esperada de este trabajo,
  no un bug.
- **`human_commits` es una observación de largo plazo**, no un criterio de aceptación:
  post-merge, depende del humano, n=1, y el conjunto de evaluación mayormente no toca CI.

## Opciones descartadas

- **Igualdad de versión en vez de major compartida**: un carácter más estricto que la
  realidad rechazaría un bump de parche legítimo, el modo de falla que ADR-0011 (cuarta
  adenda) ya nombra.
- **Una regla ciega "todo `run:` no declarado falla"**: revocaría el estado
  por-debajo-del-piso que ADR-0021 permite (mypy en CI mientras `checks.types` se retiene).
- **Verificar la existencia de un `uses:` ref**: necesita red; sólo su movimiento es
  decidible offline.

Ver ADR-0021 y ADR-0024 para los dos precedentes de forma: registro apuntado en vez de
restated, y veredicto puro más un solo borde impuro.
