---
status: accepted
date: 2026-10-05
---

# Actores y sub-agentes suben a `claude-sonnet-5-5`

ADR-0036 movió sólo la mitad cara del proceso, el verificador, a
`claude-opus-5-5`; el lado Sonnet -- `Configuration.actor_model` y
`Configuration.subagent_model` -- quedó un escalón atrás, en `claude-sonnet-5`,
mientras `claude-sonnet-5-5` ya es el Sonnet vigente. Esta tarea (subtask
`sonnet-side`) corrió el mismo gate que ADR-0035/ADR-0036 exigieron antes de
mover un default -- una llamada facturada en `tests/llm/`, contra el SDK y el
CLI pineados hoy, nunca contra la variable de entorno -- en las dos costuras
por las que ese default llega al CLI: el camino del actor (`options.model`) y
el camino del sub-agente (`CLAUDE_CODE_SUBAGENT_MODEL`/`_FORCE`). El resultado
es verde en las cuatro combinaciones (tres `effort` de actor más el camino de
sub-agente); este documento registra por qué suben los dos juntos, qué queda
fijo, la palanca de reversión y su salvedad de stage0, el tripwire tal como se
lee hoy, la ventana de precio cero y el confound de línea de base -- el mismo
inventario que ADR-0036, ampliado donde el camino del sub-agente lo exige.

## Por qué suben los dos juntos

`Configuration.actor_model` y `Configuration.subagent_model` son una sola
variable del negocio -- "el lado Sonnet" -- aunque lleguen al CLI por dos
costuras distintas (`options.model` para el actor; `role_env`'s
`CLAUDE_CODE_SUBAGENT_MODEL` más `_FORCE=1` para todo sub-agente que un rol
lance con la herramienta `Agent`, el `code-review` de las skills incluido:
ADR-0013, adenda). Esta tarea es el invariante que ADR-0036 mencionó al pasar
("un ADR, una variable"): los dos defaults se mueven en el mismo diff que la
salida verde de las dos funciones del gate, o ninguno se mueve. La tarea
original pedía exactamente este movimiento, sin A/B previo (ya se lee después,
contra una línea de base fresca).

Supera, por nombre, el default `claude-sonnet-5` que la adenda de ADR-0013
dejó anotado como el modelo de los actores y de los sub-agentes -- sin editar
ADR-0013, que queda tal como está.

## Lo que queda fijo

`Role.effort` no se tocó (invariante explícito del plan): sigue `"low"` para
PjM, `"high"` para los dos Developer (`developer`, `developer_integrator`) y
`"medium"` para el resto de los roles `kind="actor"` -- los tres valores que
el gate ejercitó, uno por caso, derivados de `ROLES` y nunca tipeados a mano
(`sorted({r.effort for r in ROLES if r.kind == "actor"})`). `verifier_model`
(`claude-opus-5-5`, ADR-0036) y los prompts de `agentic/core/process/domain/prompts/`
tampoco se tocaron: esta tarea mueve el modelo, nunca el esfuerzo ni el texto
que un rol lee. Eso dispone la lectura: si el comportamiento de un actor o de
un sub-agente cambia después de este merge, el modelo es el único grado de
libertad que se movió.

## La palanca de reversión y su salvedad de stage0

`AGENTIC_ACTOR_MODEL` y `AGENTIC_SUBAGENT_MODEL` ganan sobre el default en
`Configuration.from_sources` (`ENV_PREFIX + name.upper()`,
`configuration.py`): exportarlas vuelve cualquiera de los dos lados a
`claude-sonnet-5`, o a cualquier otro identificador, sin tocar código. A
diferencia de ADR-0036, esta palanca no depende de un CLI más nuevo: el pin
del SDK (`claude-agent-sdk` en `pyproject.toml`/`uv.lock`) no se movió en esta
tarea, así que cualquier stage0 en o después de este merge ya reconoce
`claude-sonnet-5-5` y también `claude-sonnet-5` -- la palanca revierte en
cualquiera de los dos, sin la trampa que ADR-0036 documentó para su propio
CLI. Si una futura tarea bumpea el SDK, corre este archivo
(`tests/llm/test_sonnet_side_model.py`) y `tests/llm/test_verifier_model.py`
de nuevo -- sin editar la lista de cinco archivos de ADR-0035.

## El tripwire tal como se puede leer hoy (ADR-0037, `events_only`)

Sin el dashboard que ADR-0037 retiró, el tripwire se lee de tres formas,
ninguna nueva (invariante: ningún `agentic.*` se mueve, agrega o renombra):

1. En los documentos de tarea: `runs[].cost_by_model`, por rol, y
   `failures[].reason == "limit"` -- `limit` es el propio tope de intento
   (`max_budget_usd`) después de ADR-0029, así que un actor o un sub-agente que
   se volvió más caro con `claude-sonnet-5-5` aparece ahí primero; una
   escalada de tarea es la segunda ocurrencia, nunca la primera.
2. `agentic eval compare` entre la línea de base anterior y el `bl-*` fresco
   que esta misma ADR pide (próxima sección).
3. En Langfuse, sólo como filtro manual sobre `agentic.role.model` -- no hay
   panel declarado, así que es una consulta ad hoc, no una alarma.

## La ventana de precio cero en Langfuse, más ancha que la de ADR-0036

El precio de `claude-sonnet-5-5` en Langfuse es estado de la instancia, no del
repo (ADR-0008; el mismo modo de falla que ADR-0036 documentó para
`claude-opus-5-5`): un modelo sin precio registrado factura observabilidad a
costo cero, silenciosamente, porque ningún check declarado lo verifica en cada
delivery. Esta ventana es más ancha que la de ADR-0036: el verificador es un
único rol por tarea, mientras que el lado Sonnet cubre cada actor (PjM,
Product Analyst, UX/UI Designer, los dos Developer) y cada sub-agente que
cualquiera de esos roles lance con `Agent` -- hasta que el humano registre el
precio, la mayoría de los `agentic.role.cost_usd` de una tarea puede leer en
cero sin que ningún check lo marque. Cierre: registrar `claude-sonnet-5-5` en
la UI de Langfuse (Settings > Models) y verificar con
`uv run observability/collector/probe.py`.

## El confound de línea de base

`arm` excluye el modelo (ADR-0030) y `prompts.fingerprint` no se mueve en esta
tarea (ningún prompt cambió), así que `model` y `system.baseline` son las
únicas etiquetas que separan "antes" de "después" de este merge. Sin un tag
`bl-*` fresco declarado por el humano al mergear (ADR-0030: el humano declara
la línea de base, no el sistema), una muestra de `claude-sonnet-5-5` cae bajo
la misma línea de base que las tareas ya cerradas con `claude-sonnet-5`, y
ninguna de las tres lecturas del tripwire puede distinguir "el costo cambió
porque el modelo subió" de "el costo cambió por ruido dentro de la misma línea
de base". El PR de esta tarea pide el tag fresco como precondición ordenada,
la misma que ADR-0036 pidió.

## Gate result

Comando: `uv run pytest -m llm tests/llm/test_sonnet_side_model.py -v -s`.

### Corrida de decisión (histórica)

Contra el literal `claude-sonnet-5-5` declarado en el archivo antes de mover
los defaults -- ese literal ya no existe en el diff committeado (el archivo
hoy resuelve `Configuration.model_fields[...].default`), así que este bloque
es la salida tal como se capturó en ese momento, pegada sin editar, y no algo
que un `git checkout` de este commit pueda reproducir:

```
tests/llm/test_sonnet_side_model.py::test_sonnet_5_5_is_accepted_as_actor_model[high] [claude-code:unrecognized_model] {"model":"claude-sonnet-5-5","query_source":"sdk"}
effort='high' candidate actor model: claude-sonnet-5-5
effort='high' attempt 1: green, cost 0.009656
effort='high' ResultMessage.is_error: False
effort='high' ResultMessage.total_cost_usd: 0.009656
effort='high' ResultMessage.model_usage: {'claude-sonnet-5-5': {'inputTokens': 2, 'outputTokens': 66, 'cacheReadInputTokens': 0, 'cacheCreationInputTokens': 1041, 'webSearchRequests': 0, 'costUSD': 0.009656, 'contextWindow': 200000, 'maxOutputTokens': 128000, 'thinkingTokens': 0, 'canonicalModel': 'claude-sonnet-5-5', 'provider': 'firstParty', 'costBasis': 'unknown'}}
PASSED
tests/llm/test_sonnet_side_model.py::test_sonnet_5_5_is_accepted_as_actor_model[low] [claude-code:unrecognized_model] {"model":"claude-sonnet-5-5","query_source":"sdk"}
effort='low' candidate actor model: claude-sonnet-5-5
effort='low' attempt 1: green, cost 0.0065024
effort='low' ResultMessage.is_error: False
effort='low' ResultMessage.total_cost_usd: 0.0065024
effort='low' ResultMessage.model_usage: {'claude-haiku-4-5-20251001': {'inputTokens': 936, 'outputTokens': 12, 'cacheReadInputTokens': 0, 'cacheCreationInputTokens': 0, 'webSearchRequests': 0, 'costUSD': 0.000996, 'contextWindow': 200000, 'maxOutputTokens': 32000, 'thinkingTokens': 0, 'canonicalModel': 'claude-haiku-4-5', 'provider': 'firstParty', 'costBasis': 'list'}, 'claude-sonnet-5-5': {'inputTokens': 2, 'outputTokens': 66, 'cacheReadInputTokens': 532, 'cacheCreationInputTokens': 509, 'webSearchRequests': 0, 'costUSD': 0.0055064, 'contextWindow': 200000, 'maxOutputTokens': 128000, 'thinkingTokens': 0, 'canonicalModel': 'claude-sonnet-5-5', 'provider': 'firstParty', 'costBasis': 'unknown'}}
PASSED
tests/llm/test_sonnet_side_model.py::test_sonnet_5_5_is_accepted_as_actor_model[medium] [claude-code:unrecognized_model] {"model":"claude-sonnet-5-5","query_source":"sdk"}
effort='medium' candidate actor model: claude-sonnet-5-5
effort='medium' attempt 1: green, cost 0.0065124
effort='medium' ResultMessage.is_error: False
effort='medium' ResultMessage.total_cost_usd: 0.0065124
effort='medium' ResultMessage.model_usage: {'claude-haiku-4-5-20251001': {'inputTokens': 936, 'outputTokens': 14, 'cacheReadInputTokens': 0, 'cacheCreationInputTokens': 0, 'webSearchRequests': 0, 'costUSD': 0.001006, 'contextWindow': 200000, 'maxOutputTokens': 32000, 'thinkingTokens': 0, 'canonicalModel': 'claude-haiku-4-5', 'provider': 'firstParty', 'costBasis': 'list'}, 'claude-sonnet-5-5': {'inputTokens': 2, 'outputTokens': 66, 'cacheReadInputTokens': 532, 'cacheCreationInputTokens': 509, 'webSearchRequests': 0, 'costUSD': 0.0055064, 'contextWindow': 200000, 'maxOutputTokens': 128000, 'thinkingTokens': 0, 'canonicalModel': 'claude-sonnet-5-5', 'provider': 'firstParty', 'costBasis': 'unknown'}}
PASSED
tests/llm/test_sonnet_side_model.py::test_sonnet_5_5_is_accepted_as_subagent_model [claude-code:unrecognized_model] {"model":"claude-sonnet-5-5","query_source":"agent:builtin:general-purpose"}
parent model (verifier_model default): claude-opus-5-5
candidate subagent model: claude-sonnet-5-5
attempt 1: green, cost 0.05119819999999999
ResultMessage.is_error: False
ResultMessage.total_cost_usd: 0.05119819999999999
ResultMessage.model_usage: {'claude-haiku-4-5-20251001': {'inputTokens': 978, 'outputTokens': 13, 'cacheReadInputTokens': 0, 'cacheCreationInputTokens': 0, 'webSearchRequests': 0, 'costUSD': 0.0010429999999999999, 'contextWindow': 200000, 'maxOutputTokens': 32000, 'thinkingTokens': 0, 'canonicalModel': 'claude-haiku-4-5', 'provider': 'firstParty', 'costBasis': 'list'}, 'claude-opus-5-5': {'inputTokens': 4, 'outputTokens': 218, 'cacheReadInputTokens': 2591, 'cacheCreationInputTokens': 3041, 'webSearchRequests': 0, 'costUSD': 0.029222199999999997, 'contextWindow': 1000000, 'maxOutputTokens': 128000, 'thinkingTokens': 0, 'canonicalModel': 'claude-opus-5-5', 'provider': 'firstParty', 'costBasis': 'list'}, 'claude-sonnet-5-5': {'inputTokens': 2, 'outputTokens': 3, 'cacheReadInputTokens': 0, 'cacheCreationInputTokens': 4173, 'webSearchRequests': 0, 'costUSD': 0.020932999999999997, 'contextWindow': 200000, 'maxOutputTokens': 32000, 'thinkingTokens': 0, 'canonicalModel': 'claude-sonnet-5-5', 'provider': 'firstParty', 'costBasis': 'unknown'}}
PASSED

=============================== warnings summary ===============================
tests/llm/test_sonnet_side_model.py::test_sonnet_5_5_is_accepted_as_subagent_model
  .venv/lib/python3.13/site-packages/claude_agent_sdk/types.py:1939: CanUseToolShadowedWarning: can_use_tool will not be invoked for: Agent. An allowed_tools entry that allows a whole tool auto-approves it before the callback is consulted. To gate every tool call, use a PreToolUse hook; or narrow the entry so calls fall through to can_use_tool. Allow rules from settings files can also shadow the callback but are not visible here.
    _warn_if_can_use_tool_shadowed(options)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================== 4 passed, 1 warning in 15.49s =========================
```

Suma de la corrida de decisión: `0.009656 + 0.0065024 + 0.0065124 +
0.05119819999999999 = 0.07386899999999999` USD.

### Corrida sobre el archivo committeado (verbatim)

Esta es la salida real de correr el archivo tal como quedó en el commit de
esta subtarea (resuelve `Configuration.model_fields["actor_model"].default` /
`["subagent_model"].default`, nunca un literal; imprime `is_error`,
`total_cost_usd` y `model_usage` de cada intento, `agent_seen`/`denials`
incluidos para el caso de sub-agente), pegada completa y sin editar:

```
============================= test session starts ==============================
platform linux -- Python 3.13.15, pytest-9.1.1, pluggy-1.6.0 -- .venv/bin/python
cachedir: .pytest_cache
rootdir: /home/zeta/dev/repos/agentic-system/.agentic/worktrees/GH-163
configfile: pyproject.toml
plugins: langsmith-0.12.4, anyio-4.15.1
collecting ... collected 4 items

tests/llm/test_sonnet_side_model.py::test_sonnet_5_5_is_accepted_as_actor_model[high] [claude-code:unrecognized_model] {"model":"claude-sonnet-5-5","query_source":"sdk"}
effort='high' resolved actor_model default: claude-sonnet-5-5
effort='high' attempt 1: ResultMessage.is_error=False total_cost_usd=0.0065024 model_usage={'claude-haiku-4-5-20251001': {'inputTokens': 936, 'outputTokens': 12, 'cacheReadInputTokens': 0, 'cacheCreationInputTokens': 0, 'webSearchRequests': 0, 'costUSD': 0.000996, 'contextWindow': 200000, 'maxOutputTokens': 32000, 'thinkingTokens': 0, 'canonicalModel': 'claude-haiku-4-5', 'provider': 'firstParty', 'costBasis': 'list'}, 'claude-sonnet-5-5': {'inputTokens': 2, 'outputTokens': 66, 'cacheReadInputTokens': 532, 'cacheCreationInputTokens': 509, 'webSearchRequests': 0, 'costUSD': 0.0055064, 'contextWindow': 200000, 'maxOutputTokens': 128000, 'thinkingTokens': 0, 'canonicalModel': 'claude-sonnet-5-5', 'provider': 'firstParty', 'costBasis': 'unknown'}}
effort='high' attempt 1: green, cost 0.0065024
PASSED
tests/llm/test_sonnet_side_model.py::test_sonnet_5_5_is_accepted_as_actor_model[low] [claude-code:unrecognized_model] {"model":"claude-sonnet-5-5","query_source":"sdk"}
effort='low' resolved actor_model default: claude-sonnet-5-5
effort='low' attempt 1: ResultMessage.is_error=False total_cost_usd=0.0065074 model_usage={'claude-haiku-4-5-20251001': {'inputTokens': 936, 'outputTokens': 13, 'cacheReadInputTokens': 0, 'cacheCreationInputTokens': 0, 'webSearchRequests': 0, 'costUSD': 0.001001, 'contextWindow': 200000, 'maxOutputTokens': 32000, 'thinkingTokens': 0, 'canonicalModel': 'claude-haiku-4-5', 'provider': 'firstParty', 'costBasis': 'list'}, 'claude-sonnet-5-5': {'inputTokens': 2, 'outputTokens': 66, 'cacheReadInputTokens': 532, 'cacheCreationInputTokens': 509, 'webSearchRequests': 0, 'costUSD': 0.0055064, 'contextWindow': 200000, 'maxOutputTokens': 128000, 'thinkingTokens': 0, 'canonicalModel': 'claude-sonnet-5-5', 'provider': 'firstParty', 'costBasis': 'unknown'}}
effort='low' attempt 1: green, cost 0.0065074
PASSED
tests/llm/test_sonnet_side_model.py::test_sonnet_5_5_is_accepted_as_actor_model[medium] [claude-code:unrecognized_model] {"model":"claude-sonnet-5-5","query_source":"sdk"}
effort='medium' resolved actor_model default: claude-sonnet-5-5
effort='medium' attempt 1: ResultMessage.is_error=False total_cost_usd=0.0065024 model_usage={'claude-haiku-4-5-20251001': {'inputTokens': 936, 'outputTokens': 12, 'cacheReadInputTokens': 0, 'cacheCreationInputTokens': 0, 'webSearchRequests': 0, 'costUSD': 0.000996, 'contextWindow': 200000, 'maxOutputTokens': 32000, 'thinkingTokens': 0, 'canonicalModel': 'claude-haiku-4-5', 'provider': 'firstParty', 'costBasis': 'list'}, 'claude-sonnet-5-5': {'inputTokens': 2, 'outputTokens': 66, 'cacheReadInputTokens': 532, 'cacheCreationInputTokens': 509, 'webSearchRequests': 0, 'costUSD': 0.0055064, 'contextWindow': 200000, 'maxOutputTokens': 128000, 'thinkingTokens': 0, 'canonicalModel': 'claude-sonnet-5-5', 'provider': 'firstParty', 'costBasis': 'unknown'}}
effort='medium' attempt 1: green, cost 0.0065024
PASSED
tests/llm/test_sonnet_side_model.py::test_sonnet_5_5_is_accepted_as_subagent_model [claude-code:unrecognized_model] {"model":"claude-sonnet-5-5","query_source":"agent:builtin:general-purpose"}
parent model (verifier_model default): claude-opus-5-5
resolved subagent_model default: claude-sonnet-5-5
attempt 1: ResultMessage.is_error=False total_cost_usd=0.021795400000000003 model_usage={'claude-haiku-4-5-20251001': {'inputTokens': 978, 'outputTokens': 15, 'cacheReadInputTokens': 0, 'cacheCreationInputTokens': 0, 'webSearchRequests': 0, 'costUSD': 0.001053, 'contextWindow': 200000, 'maxOutputTokens': 32000, 'thinkingTokens': 0, 'canonicalModel': 'claude-haiku-4-5', 'provider': 'firstParty', 'costBasis': 'list'}, 'claude-opus-5-5': {'inputTokens': 4, 'outputTokens': 218, 'cacheReadInputTokens': 4055, 'cacheCreationInputTokens': 1576, 'webSearchRequests': 0, 'costUSD': 0.017795000000000002, 'contextWindow': 1000000, 'maxOutputTokens': 128000, 'thinkingTokens': 0, 'canonicalModel': 'claude-opus-5-5', 'provider': 'firstParty', 'costBasis': 'list'}, 'claude-sonnet-5-5': {'inputTokens': 2, 'outputTokens': 3, 'cacheReadInputTokens': 3747, 'cacheCreationInputTokens': 426, 'webSearchRequests': 0, 'costUSD': 0.0029474, 'contextWindow': 200000, 'maxOutputTokens': 32000, 'thinkingTokens': 0, 'canonicalModel': 'claude-sonnet-5-5', 'provider': 'firstParty', 'costBasis': 'unknown'}} (agent_seen=True, denials=0)
attempt 1: green, cost 0.021795400000000003
PASSED

=============================== warnings summary ===============================
tests/llm/test_sonnet_side_model.py::test_sonnet_5_5_is_accepted_as_subagent_model
  .venv/lib/python3.13/site-packages/claude_agent_sdk/types.py:1939: CanUseToolShadowedWarning: can_use_tool will not be invoked for: Agent. An allowed_tools entry that allows a whole tool auto-approves it before the callback is consulted. To gate every tool call, use a PreToolUse hook; or narrow the entry so calls fall through to can_use_tool. Allow rules from settings files can also shadow the callback but are not visible here.
    _warn_if_can_use_tool_shadowed(options)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================== 4 passed, 1 warning in 15.43s =========================
```

Confirma, byte a byte, la clave exacta `claude-sonnet-5-5` en `model_usage` del
caso de sub-agente con `costUSD=0.0029474` (> 0), y `agent_seen=True`,
`denials=0` antes de esa lectura -- el sub-agente efectivamente corrió, y
corrió sobre el candidato.

Suma de esta corrida: `0.0065024 + 0.0065074 + 0.0065024 +
0.021795400000000003 = 0.0413076` USD.

### Suma total y corridas intermedias

Entre la corrida de decisión y la corrida verbatim de arriba, dos corridas
intermedias del mismo archivo (una sobre el head committeado tal cual, antes
de que esta misma revisión pidiera que cada intento imprimiera su propio
`is_error`/`total_cost_usd`/`model_usage`; otra inmediatamente después de ese
cambio de impresión) resultaron igual de verdes, en 0.05926319999999999 y
0.0413076 USD respectivamente -- no se pegan completas aquí porque la de
arriba ya es la que corresponde al archivo tal como quedó committeado. Sumando
las cuatro corridas: `0.07386899999999999 + 0.0413282 + 0.059263199999999995 +
0.0413076 ≈ 0.2158` USD -- muy por debajo del techo de 16 USD que el plan fijó
para el gate de esta subtarea.

El `CanUseToolShadowedWarning` que la SDK emite en el caso de sub-agente es
esperado y benigno: `Agent` está en `allowed`, así que la SDK auto-aprueba la
llamada antes de consultar `can_use_tool` -- exactamente la razón por la que
el rol parent necesita `Agent` en `tools` y en `allowed` (si no, `_deny_writes`
niega la llamada antes de que exista un sub-agente que probar).
