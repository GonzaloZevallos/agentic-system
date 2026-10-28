# Referencia de `.agentic/stack.yaml`

Generada por `scripts/docs/generate.py` desde `KEY_REFERENCE` en `agentic/core/knowledge/domain/stack.py`. Lista cada clave que el sistema lee, con el tipo con que la lee la función lectora y qué significa que falte. No se edita a mano.

## `product`

- **Presencia**: obligatoria
- **Se lee como**: `str`
- **Si falta**: no tiene valor por defecto: falta la clave y el stack no carga
- **Qué es**: string, a slug: the name the product goes by, the label of every span and metric
- **Lector**: `agentic.core.knowledge.domain.stack.product`

## `setup`

- **Presencia**: opcional
- **Se lee como**: `dict[str, str]`
- **Si falta**: mapa vacío (`{}`)
- **Qué es**: mapping of step name to one-line command, run once when a checkout is born
- **Lector**: `agentic.core.knowledge.domain.stack.setup`

## `checks`

- **Presencia**: obligatoria
- **Se lee como**: `dict[str, str]`
- **Si falta**: no tiene valor por defecto: falta la clave y el stack no carga
- **Qué es**: mapping of check name to one-line command: how the product says its code is sound
- **Lector**: `agentic.core.knowledge.domain.stack.checks`

## `witness`

- **Presencia**: opcional
- **Se lee como**: `str | None`
- **Si falta**: ausente = desactivado (el lector devuelve `None`)
- **Qué es**: string, one command whose last printed line is the path of the code the checks measured
- **Lector**: `agentic.core.knowledge.domain.stack.witness`

## `parallel_checks`

- **Presencia**: opcional
- **Se lee como**: `bool`
- **Si falta**: `false`
- **Qué es**: boolean: true lets the delivery gate run the declared checks concurrently
- **Lector**: `agentic.core.knowledge.domain.stack.parallel_checks`

## `affected_checks`

- **Presencia**: opcional
- **Se lee como**: `dict[str, str]`
- **Si falta**: mapa vacío (`{}`)
- **Qué es**: mapping of check name to its affected-only command, which carries the token `{base}`
- **Lector**: `agentic.core.knowledge.domain.stack.affected_checks`

## `affected_fallback`

- **Presencia**: opcional
- **Se lee como**: `tuple[str, ...]`
- **Si falta**: lista vacía (`()`)
- **Qué es**: list of globs whose change forces the full form of every check
- **Lector**: `agentic.core.knowledge.domain.stack.affected_fallback`

## `delivery_checks`

- **Presencia**: opcional
- **Se lee como**: `tuple[str, ...]`
- **Si falta**: lista vacía (`()`)
- **Qué es**: list of check names the delivery gate runs at every subtask delivery; absent means no tiers: every delivery runs all of `checks`
- **Lector**: `agentic.core.knowledge.domain.stack.delivery_checks`

## `forbidden`

- **Presencia**: opcional
- **Se lee como**: `tuple[Forbidden, ...]`
- **Si falta**: lista vacía (`()`)
- **Qué es**: list of mappings of `pattern`, `extensions` and `reason`: suppressions a Developer may not add
- **Lector**: `agentic.core.knowledge.domain.stack.forbidden`

## `design`

- **Presencia**: opcional
- **Se lee como**: `Design | None`
- **Si falta**: ausente = desactivado (el lector devuelve `None`)
- **Subclaves**: `prototype`, `entry`, `sample_app`, `compare`
- **Qué es**: mapping of `prototype`, `entry`, `sample_app` and `compare`: the static prototype
- **Lector**: `agentic.core.knowledge.domain.stack.design`

## `ci`

- **Presencia**: opcional
- **Se lee como**: `tuple[str, ...]`
- **Si falta**: lista vacía (`()`)
- **Qué es**: list of GitHub check-run names that gate a merge when the product trusts its CI
- **Lector**: `agentic.core.knowledge.domain.stack.ci`

## `performance`

- **Presencia**: opcional
- **Se lee como**: `dict[str, perf.Scenario]`
- **Si falta**: mapa vacío (`{}`)
- **Subclaves**: `seed`, `measure`, `volume`, `volume_unit`, `limits`
- **Qué es**: mapping of scenario name to a mapping of `seed`, `measure`, `volume`, `volume_unit` and `limits`, read only by `agentic perf`
- **Lector**: `agentic.core.knowledge.domain.stack.performance`

## `rerun_checks`

- **Presencia**: opcional
- **Se lee como**: `dict[str, RerunForm]`
- **Si falta**: mapa vacío (`{}`)
- **Subclaves**: `command`, `test_id`
- **Qué es**: mapping of check name to a mapping of `command` and `test_id`: the failed-tests-only form of the check
- **Lector**: `agentic.core.knowledge.domain.stack.rerun_checks`

## `check_budgets`

- **Presencia**: opcional
- **Se lee como**: `dict[str, float]`
- **Si falta**: mapa vacío (`{}`)
- **Qué es**: mapping of check name to a positive number of seconds, compared on the full run
- **Lector**: `agentic.core.knowledge.domain.stack.check_budgets`

## `merge_inert`

- **Presencia**: opcional
- **Se lee como**: `tuple[str, ...]`
- **Si falta**: lista vacía (`()`)
- **Qué es**: list of path globs none of the checks reads, so master's gain there keeps a check pass
- **Lector**: `agentic.core.knowledge.domain.stack.merge_inert`

## `docs`

- **Presencia**: opcional
- **Se lee como**: `Docs | None`
- **Si falta**: ausente = desactivado (el lector devuelve `None`)
- **Subclaves**: `generate`, `paths`
- **Qué es**: mapping of `generate` and `paths`: the command that rewrites generated docs and the globs it owns
- **Lector**: `agentic.core.knowledge.domain.stack.docs`
