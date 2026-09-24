---
status: accepted
date: 2026-09-24
---

# El CLI empaquetado decide el bump del SDK, no la versión de pip

#128/#130 pedían subir `claude-agent-sdk` para que el verificador acepte
`claude-opus-5-5`. El PR #130 corrió `tests/llm/test_verifier_model.py` contra el SDK
fijado (`0.2.152`) y salió rojo por `[claude-code:unrecognized_model]` — no por el
modelo, por el CLI. Esta tarea (GH-131, subtask `harness`) lee la causa antes de
gastar una sola llamada facturable: `claude_agent_sdk/_bundled/claude` es un binario
propio de cada wheel, y `subprocess_cli._find_cli()` lo devuelve antes de llegar a
`shutil.which("claude")` (`options_for` nunca fija `cli_path`). El `claude` de la
máquina (2.1.281) nunca ejecutó un rol de este sistema; el que importa es el que trae
el wheel.

## La regla de decisión es una comparación offline, gratis, antes de cualquier llamada

`claude_agent_sdk._cli_version.__cli_version__` en el venv sincronizado, comparado
contra el CLI que empaquetaba `0.2.152` (`2.1.259`). Si el candidato no es
estrictamente más nuevo, o si el nuevo `ClaudeAgentOptions` trae un campo con default
no inerte, el bump se abandona sin gastar un dólar. Ninguna de las dos condiciones se
dio aquí: el candidato bumpea el CLI y el único campo nuevo es inerte. Ver
`## Gate result` abajo para los números y la evidencia pegada.

## El guardia piso-vs-lock cierra un agujero de forma ADR-0027

`setup: deps: uv sync --locked` corre una sola vez, al nacer el checkout, contra el
`pyproject.toml` BASE (ADR-0011, tercera adenda); ninguno de los cuatro checks
declarados vuelve a lockear. Así que un piso movido por encima de un `uv.lock`
desactualizado entrega verde acá y rojo en CI — la misma clase de defecto que
`unknown_pins` cierra para un literal de `.github/workflows/**`, pero para un literal
que vive en `pyproject.toml` y `uv.lock`, que `unknown_pins` no cubre.
`floor_lock_disagreements` en `tests/test_stack.py` lo cierra: PURO, TOML ya parseado
adentro, una lista de violaciones afuera, sin subproceso. Cubre las tres dependencias
que ADR-0022 nombra como no agrupadas — `claude-agent-sdk`, `langgraph`,
`langgraph-checkpoint-sqlite` — y exige que el `uv.lock` SATISFAGA el piso para las
tres, más igualdad exacta sólo para `claude-agent-sdk`.

La asimetría es deliberada, no un descuido: `claude-agent-sdk` está pineado por el CLI
que su wheel empaqueta (la sección anterior), así que el piso y el lock tienen que
nombrar el mismo release exacto. `langgraph` (`>=1.0`) y `langgraph-checkpoint-sqlite`
(`>=3.1.1`) no pinean ningún artefacto empaquetado específico — son pisos abiertos que
este repo no necesita fijar a un release exacto, y exigirles igualdad rompería la
próxima vez que `uv lock` resuelva un patch por encima del piso (`langgraph` ya
resuelve `1.2.11` hoy, muy por encima de `1.0`).

## El pin de opciones re-pineado: un solo campo nuevo, inerte

`dataclasses.fields(ClaudeAgentOptions)` entre `0.2.152` y `0.2.159` agrega exactamente
un campo: `verbatim_prompts`, default `False`. `options_for` no lo fija, así que fluye
como `False` — uno de los cuatro defaults inertes que el plan nombra (`None`, `False`,
`{}`, o el valor que `options_for` ya pasa). Ningún campo se removió. El diff completo,
pegado en la sección del Developer, es la enumeración offline y gratuita que el plan
pedía en vez de descubrir esto con cinco corridas facturables.

## La compuerta de cinco archivos: dos verdes limpios, uno verde con hallazgo, dos con un bug propio no relacionado con el SDK

`test_delivery_rejection_visibility.py` y `test_trace_propagation.py` corrieron limpio
contra el SDK nuevo. `test_token_usage.py` corrió limpio y midió costo real. Pero
`test_denial_routing.py` y `test_turn_notice.py` (3 tests en total) fallan con
`TypeError: options_for() missing 1 required keyword-only argument:
'resource_attributes'` — **antes de llegar a construir `ClaudeAgentOptions` o de
tocar la red**. Esto no es evidencia sobre el SDK: `resource_attributes` ya era un
kwarg requerido de `options_for` en el `HEAD` de esta tarea, sin que este subtask haya
tocado `runner.py` (`git diff --stat` contra ese archivo es vacío). El bug es previo a
GH-131, vive en dos archivos que este subtask no declaró como costura propia
(`tests/test_stack.py`, `test_runner.py`, `test_verifier_model.py` son las tres
costuras de este subtask) y por eso no se corrige acá.

**Nombrado explícitamente, como pide el invariante:** las costuras que
`test_denial_routing.py` y `test_turn_notice.py` iban a ejercitar contra el SDK nuevo
— el ruteo `can_use_tool`/`PermissionResultDeny` bajo `permission_mode="default"`
(ADR-0011, cuarta adenda) y el conteo de turnos del hilo principal (ADR-0031) — **no
fueron ejercitadas** por esta corrida. Quedan declaradas, no verificadas contra el
`claude-agent-sdk` nuevo. Ningún dólar se gastó en esas 3 pruebas: el `TypeError`
ocurre antes de cualquier `query()`.

## El candidato: verde

`Configuration.model_fields["verifier_model"].default` (`claude-opus-5`) y el literal
`CANDIDATE = "claude-opus-5-5"` (declarado en `tests/llm/test_verifier_model.py`, nunca
en el entorno) corrieron ambos, cada uno en su propia función de test, y los dos
facturaron y devolvieron `is_error=False`. El subtask `verifier-model` — que no es
este — es quien mueve `Configuration.verifier_model` y escribe ADR-0036 a partir de
este resultado.

## Esta ADR reemplaza la lista de dos archivos de ADR-0022, no la corrige

ADR-0022 es aceptada y no se edita (ADR-0010). Su comando de compuerta manual nombraba
sólo `test_delivery_rejection_visibility.py` y `test_trace_propagation.py`; es
anterior a la costura `can_use_tool`/`PermissionResultDeny` (ADR-0011, cuarta adenda),
al conteo de turnos del hilo principal (ADR-0031) y a la lectura de
`ResultMessage.usage` (adenda de ADR-0008 del 2026-09-18). La lista de cinco archivos
de esta ADR sustituye esa de dos, sin editar ADR-0022.

## Gate result

- Bundled CLI: `2.1.259` (base, `claude-agent-sdk==0.2.152`) → `2.1.281` (candidato,
  `claude-agent-sdk==0.2.159`) — estrictamente más nuevo.
- `test_delivery_rejection_visibility.py`: verde (2/2).
- `test_trace_propagation.py`: verde (1/1).
- `test_denial_routing.py`: no concluyente — `TypeError` propio del archivo
  (`resource_attributes` faltante), previo a esta tarea, sin llamada facturable;
  ver la sección de arriba.
- `test_turn_notice.py`: no concluyente — mismo `TypeError`, mismo motivo, sin
  llamada facturable (2/2).
- `test_token_usage.py`: verde (1/1).
- Candidato (`claude-opus-5-5`): verde, facturado (`total_cost_usd=0.010849`).
- Default (`claude-opus-5`): verde, facturado (`total_cost_usd=0.013571`).

Decisión: bump ships. `pyproject.toml` y `uv.lock` quedan en `claude-agent-sdk 0.2.159`.
`Configuration.verifier_model` NO se toca en esta ADR ni en este subtask — eso y
ADR-0036 son del subtask `verifier-model`, que lee este resultado.
