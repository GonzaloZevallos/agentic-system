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
