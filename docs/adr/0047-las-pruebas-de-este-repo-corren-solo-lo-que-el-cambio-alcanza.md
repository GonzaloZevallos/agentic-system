---
status: accepted
date: 2026-10-08
---

# Las pruebas de este repo corren solo lo que el cambio alcanza

GH-224 declara en `.agentic/stack.yaml` la forma affected-only del chequeo `tests`
(ADR-0046): `affected_checks.tests: uv run python scripts/affected_tests.py {base}` y
`affected_fallback` con los globs que siempre corren completo. `lint`, `format` y `types`
siguen completos (rápidos). La puerta de esta propia tarea corre el stack viejo, leído en el
base; la primera puerta que ejerce esta declaración es la de la tarea siguiente al merge.

## La regla de selección

`scripts/affected_tests.py <base>` junta el conjunto cambiado (`git diff --name-status -M -z
<base>` contra el árbol de trabajo, más `git ls-files --others --exclude-standard -z`, con
los dos lados de cada renombre) y construye con `ast` el grafo de `agentic/**/*.py` y
`tests/**/*.py`, sin `agentic/plugins/` ni `tests/llm/`. Cada `import` y cada string que
empieza con `agentic.` (un `patch(...)`, un `setattr`) es una arista. Se seleccionan los
`tests/**/test_*.py` cuya clausura transitiva inversa alcanza un módulo cambiado, **por
nombre**: un test que importa un módulo borrado se selecciona y falla a la vista.

Una **tabla de entradas** (`TABLE`) mapea rutas que un test lee sin importarlas a los tests
que las leen, con una razón por fila; una fila puede apuntar a `module:<nombre>` (los tests
que alcanzan ese módulo). Una **lista de ignorados** (`IGNORE`: `.agentic/tasks/*`,
`.agentic/playbooks/*`, `docs/*`) nombra lo que ningún test lee. Los caminantes
(`test_architecture`, `test_language`, `test_semconv`, `test_pause_log_guard`) y el guardia
miran `agentic/*.py`; un test que carga código por un nombre calculado se suma a ese
disparador. La lista de ignorados se derivó de `git log --name-only` sobre los últimos 400
commits: la única ruta no `.py` frecuente que ningún test lee es `docs/**`
(ADR, `docs/agents`, `docs/product`); `.agentic/tasks/` y `.agentic/playbooks/` no están
versionados en este repo y se ignoran por previsión (el documento de tarea, ADR-0001).

### Derivación de la lista de ignorados

`git log --name-only --format= -n 400 | grep -v '\.py$'`, con las rutas distintas
agrupadas y la fila que recibió cada una (F = `affected_fallback`, T = fila de `TABLE`,
I = `IGNORE`, R = sin fila: la corrida suelta la rechaza con 9):

| Ruta (apariciones en 400 commits) | Fila |
|---|---|
| `README.md` (19), `BRIEF.md`, `.gitignore`, `.gitmodules`, `langgraph.json`, `untitled.md`, `CLAUDE.md` | R |
| `agentic/core/*/CONTEXT.md` (≈30), `CONTEXT-MAP.md` | F (`*CONTEXT*.md`) |
| `agentic/core/process/domain/prompts/*.md` (≈60) | F (`agentic/*/prompts/*`) |
| `agentic/core/knowledge/domain/{CODING_STANDARDS,brief}.md`, `stack.yaml`, `flags.yaml` | T (`module:` de `knowledge`, `stack`, `flags`) |
| `CODING_STANDARDS.md` (2) | T (`tests/test_stack.py`) |
| `observability/README.md` (11) | T (`module:tests.readme_metrics`) |
| `observability/collector/otel-collector.yaml` (2) | T (`tests/test_observability_config.py`) |
| `observability/grafana/*`, `observability/langfuse/*` (≈14) | R |
| `pyproject.toml` (8), `uv.lock` (2), `.env.example` (7) | F |
| `.agentic/stack.yaml` (4), `.github/**` (5), `scripts/*` (5) | F (el dominio agrega `.agentic/stack.yaml`) |
| `docs/adr/*.md` (≈90), `docs/agents/*`, `docs/product/*` | I (`docs/*`; el guardia no halla ninguna lectura) |
| `.agentic/tasks/*`, `.agentic/playbooks/*` | I (sin apariciones en el historial: se ignoran por previsión) |

## Códigos de salida

| Resultado | Código | Línea única en stderr |
|---|---|---|
| Hay selección | el de pytest, sin cambios | — |
| No selecciona nada | 8 | `affected-tests: empty: <razón>` |
| No puede decidir | 9 | `affected-tests: refused: <razón>` |

Rechaza con 9: un `__init__.py` o `conftest.py` cambiado, una ruta que no está en el grafo,
en la tabla ni en los ignorados (incluye `langgraph.json` y toda ruta de
`affected_fallback` en una corrida suelta), un import dinámico con argumento no constante
bajo `agentic/`, un fallo de git (`affected-tests: refused: git`) y un `{base}` que empieza
con `-`. Nunca imprime selección en 8 ni 9, nunca corre la suite completa (el reintento de la
puerta, ADR-0046, es el único camino a completo) y corre pytest como lista argv con
`-m "not llm"`. El `{base}` llega a git como un único elemento argv.

## El guardia

`tests/test_affected_tests.py` corre sobre el árbol real. Recoge de cada `tests/**/*.py`
(sin `tests/llm`): literales con forma de ruta del repo, operandos constantes de `/` y
argumentos de `Path`, `open`, `read_text`, `read_bytes`, `glob`, `rglob` e `iterdir`, y
recorridos anclados en `__file__` o en un `ROOT`/`REPO`/`CORE`/`PACKAGE`. Cada archivo del
árbol que esas lecturas alcanzan debe estar cubierto por una fila de `TABLE` que incluya al
test, por un glob de `affected_fallback` (leído con `agentic.core.knowledge.domain.stack`),
por la clausura de imports o por una excepción nombrada con razón (`ALLOWANCES`, falsos
positivos de repos creados bajo `tmp_path`). También afirma, con una comprobación que recibe sus entradas y
tiene un caso negativo, que ningún glob de la tabla, de los ignorados ni de las excepciones
calza una ruta que el fallback también calza (salvo `agentic/*.py` y `tests/*.py`, exentos por
nombre y con razón), que ningún ignorado es leído por un test y que todo glob del fallback
calza con una ruta real. Es heurístico: una ruta armada en el helper de
otro módulo, o leída por una constante de módulo de `agentic/` (`stack.TEMPLATE`), no se ve;
esas tres últimas están cubiertas a mano en `TABLE` con `module:`. `docs/*` quedó fuera del
fallback porque el guardia no encuentra ninguna lectura de `docs/`.

La fila `*CONTEXT*.md -> test_language` que el plan sugería no está en la tabla: ese glob ya
es de `affected_fallback` y la tabla no puede repetirlo; en la puerta lo decide el fallback y
en una corrida suelta el archivo se rechaza con 9.

## Costo y límites

Un rechazo, una selección vacía o un test rojo real cuestan el arranque affected más una
corrida completa; `check_durations` los suma en la duración de `tests`. Un cambio a
`process/domain/rules.py` o `task.py` selecciona casi todo y no ahorra. `scripts/` queda
fuera del alcance de mypy (ADR-0021); el script va anotado entero, sin `cast`, `type: ignore`
ni `noqa`, y sus funciones puras las prueban los fixtures. El revisor gana
`Bash(uv run python scripts/affected_tests.py:*)` por derivación (ADR-0046).

## Aceptación

Tres corridas reales contra `HEAD` de la rama (cache caliente, `uv run` ya resuelto), contra
la suite completa `uv run pytest -m "not llm"` en el mismo checkout: 2704 pasan, 1 omitido,
140,6 s (142,6 s de pared).

1. Un archivo de test hoja tocado (`tests/core/process/domain/test_signals.py`): selecciona 103
   tests, 9,4 s de pared, salida 0. Un adaptador hoja (`.../shell/inhibitor.py`): 262 tests,
   30,5 s, salida 0.
2. Un `.agentic/tasks/GH-0.md` sin versionar: `affected-tests: empty: the change reaches no
   test`, salida 8, 1,1 s.
3. `pyproject.toml` tocado: `affected-tests: refused: pyproject.toml maps to no test,
   input-table row or ignore entry`, salida 9, 1,8 s; sigue la forma completa de arriba.

## Adenda (GH-301): ADRs de módulo

GH-292 incorporó `agentic/core/knowledge/docs/adr/0001-el-conocimiento-se-lee-por-indice.md`,
una ruta que no cubría ninguna fila de `TABLE` ni entrada de `IGNORE`: cualquier diff que la
tocara terminaba en `Refusal` y la forma affected de `tests` salía REFUSED, aunque la suite
completa pasara. Se decide:

- Los directorios de ADR de módulo (`agentic/core/*/docs/adr/`) van en `IGNORE`, porque ningún
  test los lee. La fila `docs/adr/*` sigue apuntando solo a `tests/test_adr_numbers.py`, que
  lista el directorio raíz.
- Los ADR de módulo se numeran por módulo. Quedan fuera de `tests/test_adr_numbers.py` y de
  `renumber` (ADR-0049), que ven solo el `docs/adr` raíz; el `0001` de un módulo no choca con
  el `0001` raíz.
- Un `Allowance` en la guarda de `tests/test_affected_tests.py` se agrega solo para una ruta que
  el plan fija, y en la misma subtarea que la fija. La comprobación de `IGNORE` respeta esos
  `Allowance`: un test que solo nombra una ruta ignorada no cuenta como que la lee.
- Un golden que fija la forma del prompt de un rol pertenece a la costura que cambia lo que
  devuelve `knowledge.read()`, y se mueve en esa misma subtarea.

## Adenda (GH-1068): se acota `affected_fallback` por clausura medida

`affected_fallback` obligaba a la suite completa ante cualquier cambio en doce globs. Esta adenda
mide cada uno y saca de la lista solo lo que tiene un mapeo confiable a los tests que puede
romper. Un cambio chico de la lista es un resultado válido; no se afirma ninguna mejora de tiempo.

### La regla del 50 %

Una entrada sale de la lista solo si se cumplen las tres condiciones:

1. su **clausura** (`closure_fraction` de `scripts/affected_tests.py`: la parte de los archivos de
   test que `reaching` selecciona para el peor archivo del glob) es menor que el 50 %;
2. su **selección efectiva** (`select([archivo], graph, TABLE, IGNORE)`, que suma los loaders y las
   filas; se toma el archivo del glob que más selecciona) es menor que el 50 %;
3. la nota de confiabilidad de abajo se confirmó: los loaders del grafo y la búsqueda con `grep`
   no dejan ninguna lectura por una ruta construida que ni el grafo ni la guardia vean.

Si falta una, la entrada se queda. Una fila nueva que selecciona el 50 % o más se borra y la entrada
vuelve a la lista. Siempre quedan: `pyproject.toml`, `uv.lock`, `*conftest.py`, `.env.example`,
`agentic/plugins/*` y `scripts/affected_tests.py`, el corredor del check. Ningún test afirma un valor
de fracción: son una medición de esta adenda, no un contrato.

### Cómo se reproduce

Base: `28485351dac3bc95e9018a97f89beb15e12bb386` (el `HEAD` de master del que parte la rama). La
clausura se mide en ese sha; la selección efectiva, sobre el árbol de este diff. El snippet lee el
árbol de `git archive <sha>` si recibe un sha, y el árbol de trabajo si no; no es un modo del script.

```
uv run python -c '
import pathlib, subprocess, sys, tempfile
sys.path.insert(0, "scripts")
import affected_tests as a
GLOBS = ["pyproject.toml", "uv.lock", "*conftest.py", "agentic/shared/*", "agentic/composition.py", "agentic/cli.py", "agentic/*/prompts/*", "agentic/plugins/*", "*CONTEXT*.md", ".github/*", "scripts/*", "scripts/affected_tests.py", ".env.example"]
sha = sys.argv[1] if len(sys.argv) > 1 else None
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp if sha else ".")
    if sha:
        tar = subprocess.run(["git", "archive", sha], capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", tmp], input=tar, check=True)
    g = a.graph(a.read_sources(root))
    tests = sum(1 for p in g.paths.values() if a.is_test(p))
    files = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and ".git/" not in str(p) and ".venv/" not in str(p) and "__pycache__" not in str(p) and ".agentic/worktrees" not in str(p)]
    print("tests", tests, "loaders", len(g.loaders), *sorted(g.loaders))
    for glob in GLOBS:
        hit = [p for p in files if a.matches(glob, p)]
        picks = [a.select([p], g, a.TABLE, a.IGNORE) for p in hit]
        best = max([len(r.files) for r in picks if isinstance(r, a.Selection)], default=0)
        print(f"{glob:24} files={len(hit):3} closure={a.closure_fraction(hit, g):.1%} effective={best / tests:.1%}")
' 28485351dac3bc95e9018a97f89beb15e12bb386
```

Sin el sha final, lo mismo sobre el árbol de trabajo.

### Tabla por entrada

Sobre 576 archivos de test. La columna «efectiva» es la del árbol de este diff.

| Entrada | Archivos | Clausura (base) | Efectiva (árbol de este diff) | Decisión |
| --- | --- | --- | --- | --- |
| `pyproject.toml` | 1 | 0,0 % | 0,0 % | queda: siempre completa |
| `uv.lock` | 1 | 0,0 % | 0,0 % | queda: siempre completa |
| `*conftest.py` | 1 | 0,3 % | 0,0 % | queda: siempre completa |
| `agentic/shared/*` | 6 | 90,5 % | 91,8 % | queda: clausura de 50 % o más |
| `agentic/composition.py` | 1 | 7,1 % | 9,7 % | queda: confiabilidad sin confirmar |
| `agentic/cli.py` | 1 | 3,8 % | 6,9 % | queda: confiabilidad sin confirmar |
| `agentic/*/prompts/*` | 45 | 0,0 % | 0,0 % (23,3 % con la fila probada) | queda: confiabilidad sin confirmar |
| `agentic/plugins/*` | 0 | 0,0 % | 0,0 % | queda: siempre completa |
| `*CONTEXT*.md` | 4 | 0,0 % | 0,0 % (45,0 % con la fila probada) | queda: confiabilidad sin confirmar |
| `.github/*` | 4 | 0,0 % | 0,0 % | queda: la leen más de 5 tests |
| `scripts/*` | 17 | 0,0 % | 0,3 % | **sale**, con filas |
| `scripts/affected_tests.py` | 1 | 0,0 % | 0,0 % | queda: siempre completa, es el corredor del check |
| `.env.example` | 1 | 0,0 % | 0,0 % | queda: siempre completa |

`scripts/affected_tests.py` entra como entrada explícita en lugar de `scripts/*`; está fuera del grafo (`ROOTS` es `agentic/` y `tests/`), por eso su clausura es 0,0 %, y queda por ser el corredor, no por la medición.

### Por qué quedan las que miden menos del 50 %

- **`agentic/composition.py` y `agentic/cli.py`**: la clausura es chica, pero hay lecturas por ruta
  construida (`PACKAGE / "cli.py"` y `PACKAGE / "composition.py"` en `tests/test_architecture.py`,
  `tests/test_checkout_creators.py` y `tests/test_poll_inventory.py`; las páginas de `docs/` citan
  `agentic/cli.py:símbolo` y `tests/test_docs.py` las comprueba por una ruta calculada). No se pudo
  confirmar que ningún golpe sea una lectura por ruta construida: queda completa.
- **`agentic/*/prompts/*`**: se probó una fila con los 9 tests que la guardia encuentra leyéndolos más
  `module:agentic.core.process.adapters.filesystem.prompts` (23,3 % efectiva). Se descartó: otros
  tests leen los prompts por `PROMPTS_DIR` o por `DOMAIN / "prompts"` sin que la guardia los vea
  (`test_size_budget_text.py`, `test_prompts_done_criteria.py`, `test_tracker_item_kinds_prompt.py`,
  `test_plan_shape_golden.py`, `test_design_roles.py`, entre otros), y
  `tests/core/process/domain/test_spec_brief.py` fija que el glob esté en la lista. No se confirma:
  queda completa.
- **`*CONTEXT*.md`**: se probó una fila con los 6 tests que la guardia encuentra más
  `module:agentic.core.knowledge.adapters.filesystem.knowledge` (45,0 % efectiva, la única junto al
  50 %). `tests/core/process/adapters/langgraph/test_graph.py` y
  `tests/core/process/application/test_landing_dated_rename.py` arman la ruta con `GLOSSARY` sin que
  la guardia los liste: no se confirma, queda completa y la fila se descartó.
- **`.github/*`**: la leen 25 tests (23 solo `ci.yml`), más de los 5 que permiten filas.

### La entrada que sale: `scripts/*`

Loaders del grafo en el sha base: 8 (`tests/core/process/adapters/langgraph/test_graph_full_checks.py`,
`tests/core/process/application/test_prompt_placeholder_scan.py`,
`tests/core/process/domain/test_document_state_keys.py`, `tests/test_affected_tests.py`,
`tests/test_conformance_scope.py`, `tests/test_docs.py`, `tests/test_gate_concurrency.py`,
`tests/test_poll_waits.py`). Ningún script está en el grafo (`ROOTS` es `agentic/` y `tests/`), así
que ninguno suma loaders por sí mismo.

`grep -rln <nombre> tests agentic --include=*.py` sobre cada script con test:

- `ci_flakes`: `tests/test_ci_flakes.py` (lo corre como proceso), `tests/test_stack.py` y
  `tests/test_affected_tests.py` (solo lo nombran en un comando o un glob) y cinco módulos de
  `agentic/core/process/` que lo nombran en la línea de comando de CI que arman; ninguno abre el archivo.
- `gate_concurrency` y `poll_waits`: su test y los dos que solo los nombran.
- `install-poller`: `tests/test_install_poller.py` (corre el script y copia `scripts/`, incluidas las
  plantillas de `scripts/systemd/`), `tests/core/process/domain/test_poller_units.py` y el docstring
  de `poller_units.py`.
- `scripts/docs/*`: `tests/test_docs.py` carga `render` y `generate` y corre `generate.py` y
  `check.py`; `tests/docs_scripts.py` carga `imports`, `packages`, `classes` y `groups` con un
  nombre calculado (`SCRIPTS / f"{name}.py"`). Esa es una lectura por ruta construida que la guardia
  no ve, así que solo `check.py` y `generate.py` tienen fila; los demás módulos de `scripts/docs/`
  siguen rechazando con la salida 9, que manda a la suite completa.

Ningún golpe de los listados es una lectura por `Path(__file__)`, `importlib.resources` o un glob
recorrido fuera de esos casos: los tests de cada script usan `REPO / "scripts" / "<nombre>"`, que
la guardia ve. Un script sin fila (`scripts/stage0.sh`, `scripts/score_redate_spike.py`,
`scripts/docs/render.py` y los demás módulos de `scripts/docs/`, las unidades `stage0-rotate`)
sigue rechazando con la salida 9: no cambia nada observable, porque la compuerta ya trata ese
código como la forma completa.

### Filas de solo lectura

La guardia de `tests/test_affected_tests.py` encuentra qué test lee cada archivo. Las filas nuevas
de `TABLE`, con el test que las lee:

| Glob | Tests (los que lee la guardia o el que corre el script) |
| --- | --- |
| `scripts/ci_flakes.py` | `tests/test_ci_flakes.py` |
| `scripts/gate_concurrency.py` | `tests/test_gate_concurrency.py` |
| `scripts/poll_waits.py` | `tests/test_poll_waits.py` |
| `scripts/install-poller.sh` | `tests/test_install_poller.py` |
| `scripts/systemd/*` | `tests/core/process/domain/test_poller_units.py`, `tests/test_install_poller.py` |
| `scripts/docs/check.py`, `scripts/docs/generate.py` | `tests/test_docs.py` |

No hay fila de prompts ni de `*CONTEXT*.md`: no hay lector de runtime de los archivos de contexto
que pase la regla (el lector `agentic.core.knowledge.adapters.filesystem.knowledge` existe, pero
la fila que lo usa no se confirmó). Los tests que solo nombran un script en una cadena (`test_master_gain`,
`test_roles`, `test_stack`, `test_affected_tests`) entran como `Allowance` sobre `scripts/*`.

Un test lee la lista con `agentic.core.knowledge.domain.stack.affected_fallback` y comprueba, por cada
ruta de ejemplo, que ningún glob que queda la cubre y que `select` elige el test nombrado. Otro
test comprueba que cada destino de cada fila de `TABLE` (las 33 anteriores y las nuevas) existe,
destino por destino: un archivo de test, un directorio con al menos un test o un `module:` que
está en el grafo y alcanza algún test.
