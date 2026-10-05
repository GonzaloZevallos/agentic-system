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

Corrida de decisión (contra el literal `claude-sonnet-5-5`, antes de mover los
defaults):

```
tests/llm/test_sonnet_side_model.py::test_sonnet_5_5_is_accepted_as_actor_model[high]
effort='high' candidate actor model: claude-sonnet-5-5
effort='high' attempt 1: green, cost 0.009656
PASSED
tests/llm/test_sonnet_side_model.py::test_sonnet_5_5_is_accepted_as_actor_model[low]
effort='low' candidate actor model: claude-sonnet-5-5
effort='low' attempt 1: green, cost 0.0065024
PASSED
tests/llm/test_sonnet_side_model.py::test_sonnet_5_5_is_accepted_as_actor_model[medium]
effort='medium' candidate actor model: claude-sonnet-5-5
effort='medium' attempt 1: green, cost 0.0065124
PASSED
tests/llm/test_sonnet_side_model.py::test_sonnet_5_5_is_accepted_as_subagent_model
parent model (verifier_model default): claude-opus-5-5
candidate subagent model: claude-sonnet-5-5
attempt 1: green, cost 0.05119819999999999
PASSED

4 passed, 1 warning in 15.49s
```

Suma de la corrida de decisión: `0.009656 + 0.0065024 + 0.0065124 +
0.05119819999999999 = 0.07386899...` USD.

Corrida posterior al movimiento (el archivo ya resuelve
`Configuration.model_fields["actor_model"].default` /
`["subagent_model"].default`, nunca un literal):

```
tests/llm/test_sonnet_side_model.py::test_sonnet_5_5_is_accepted_as_actor_model[high]
effort='high' resolved actor_model default: claude-sonnet-5-5
effort='high' attempt 1: green, cost 0.0065074
PASSED
tests/llm/test_sonnet_side_model.py::test_sonnet_5_5_is_accepted_as_actor_model[low]
effort='low' resolved actor_model default: claude-sonnet-5-5
effort='low' attempt 1: green, cost 0.0065074
PASSED
tests/llm/test_sonnet_side_model.py::test_sonnet_5_5_is_accepted_as_actor_model[medium]
effort='medium' resolved actor_model default: claude-sonnet-5-5
effort='medium' attempt 1: green, cost 0.0065124
PASSED
tests/llm/test_sonnet_side_model.py::test_sonnet_5_5_is_accepted_as_subagent_model
parent model (verifier_model default): claude-opus-5-5
resolved subagent_model default: claude-sonnet-5-5
attempt 1: green, cost 0.021801400000000002
PASSED

4 passed, 1 warning in 15.06s
```

Suma de la segunda corrida: `0.0065074 + 0.0065074 + 0.0065124 +
0.021801400000000002 = 0.0413286...` USD.

Suma de las dos corridas: `≈ 0.1158` USD -- muy por debajo del techo de 16 USD
que el plan fijó para el gate de esta subtarea.

El `CanUseToolShadowedWarning` que la SDK emite en el caso de sub-agente es
esperado y benigno: `Agent` está en `allowed`, así que la SDK auto-aprueba la
llamada antes de consultar `can_use_tool` -- exactamente la razón por la que
el rol parent necesita `Agent` en `tools` y en `allowed` (si no, `_deny_writes`
niega la llamada antes de que exista un sub-agente que probar).
