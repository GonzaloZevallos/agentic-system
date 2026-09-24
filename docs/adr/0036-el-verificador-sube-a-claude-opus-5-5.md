---
status: accepted
date: 2026-09-24
---

# El verificador sube a `claude-opus-5-5`

ADR-0035 (subtask `harness`, esta misma tarea GH-131) leyó la causa de
`[claude-code:unrecognized_model]` en #130 -- el CLI empaquetado por
`claude-agent-sdk 0.2.152`, no el modelo -- y la cerró bumpeando a `0.2.159` con
un CLI estrictamente más nuevo. Su `## Gate result` deja los dos candidatos
verdes y facturados: `claude-opus-5-5` en `0.010849` USD, `claude-opus-5` (el
default de entonces) en `0.013571` USD. Esta ADR es la que ese resultado
autoriza a escribir: `Configuration.verifier_model` sube, `probe.py`'s `MODEL`
la sigue, y este documento nombra por qué, qué se mantiene fijo, la palanca de
reversión, el tripwire, el confound de línea de base, por qué el orden
CLI-antes-que-modelo es seguro, y la ventana de precio cero en Langfuse.

## Por qué sube el verificador

El verificador es la mitad cara y crítica del proceso: es quien lee la salida
de un actor y decide si pasa. `claude-opus-5-5` es el sucesor de
`claude-opus-5`, y el gate de ADR-0035 -- el candidato aceptado por el SDK
bumpeado y facturado con éxito -- es la única evidencia que este repo exige
antes de mover el default (invariante: `Configuration.verifier_model` y
`probe.py`'s `MODEL` se mueven juntos, con la salida verde pegada en el mismo
diff). No hay una segunda razón detrás de esta: la tarea original (#128) pedía
exactamente este movimiento, y #130 lo dejó a mitad de camino por el SDK, no
por el modelo.

## La pregunta del esfuerzo: sigue fija

`options_for` pasa `effort=role.effort` (`runner.py:125`); ningún
`Role.effort` se tocó en esta tarea (invariante explícito del plan) y sigue en
`"medium"`, su default, para los siete slugs `kind="verifier"` de
`agentic/core/process/domain/roles.py`. Eso deja el modelo como la única
variable que se movió: si el comportamiento del verificador cambia después de
este merge, no hay un segundo grado de libertad (esfuerzo, `thinking`,
`max_thinking_tokens` -- ninguno de los tres se tocó, ninguno lo hace esta
tarea) que confunda la lectura.

## La palanca de reversión

`AGENTIC_VERIFIER_MODEL` gana sobre el default en `Configuration.from_sources`
(`ENV_PREFIX + name.upper()`, `configuration.py:116-118`): exportarla vuelve
el verificador a `claude-opus-5`, o a cualquier otro identificador, sin tocar
código. Pero esa palanca depende de qué CLI la ejecuta: si alguien la fija
en un stage0 más viejo que este merge -- uno que todavía instala
`claude-agent-sdk 0.2.152` y por lo tanto el CLI `2.1.259` -- la palanca no
"revierte" nada, reabre a mano exactamente la falla `[claude-code:
unrecognized_model]` que ADR-0035 cerró, porque ese CLI nunca reconoció
`claude-opus-5-5` y tampoco reconocía `claude-opus-5-5` como texto de
rollback hacia adelante. La atomicidad de ADR-0035 (un `scripts/stage0.sh
<ref>` re-pinea el wheel y el default en el mismo `uv sync --locked`) protege
el camino de avance, no un uso manual de la palanca contra un checkout viejo.

## El tripwire: costo por modelo, alarma en el límite de intento

`agentic.role.cost_usd`, filtrado por la etiqueta `model` y por `role.slug`,
leído contra los siete slugs `kind="verifier"`, es la serie que muestra si
`claude-opus-5-5` sale más caro que `claude-opus-5` corrida a corrida. La
alarma es `agentic.role.failures{reason="limit"}`: post-ADR-0029 `limit` es el
propio tope de intento de 10 USD (`max_budget_usd`, `configuration.py:48`), así
que un verificador que se volvió más caro aparece ahí primero -- una escalada
de tarea es la segunda ocurrencia, no la primera. Ninguna de las dos series es
nueva ni se renombra (invariante: ningún `agentic.*` se mueve); el tripwire es
una lectura sobre series existentes, no una emisión nueva.

## El confound de línea de base: el `bl-*` fresco es la segunda mitad del tripwire

`arm` excluye el modelo (ADR-0030) y `prompts.fingerprint` no se mueve en esta
tarea (invariante), así que las dos únicas etiquetas que separan "antes" de
"después" de este merge son `model` y `system.baseline`. Sin un tag `bl-*`
fresco declarado por el humano al mergear (ADR-0030: el humano declara la
línea de base, no el sistema), una muestra de `claude-opus-5-5` cae bajo
`bl-2026-09-23` -- la misma línea de base que cuatro tareas ya terminadas --
y el tripwire de la sección anterior no puede distinguir "el verificador
cambió de costo porque el modelo subió" de "el verificador cambió de costo
por ruido dentro de la misma línea de base". El PR de esta tarea pide el tag
fresco como precondición ordenada, igual que ADR-0035 pide el re-pin de
stage0.

## Por qué el orden CLI-antes-que-modelo es seguro: la atomicidad

`Configuration.verifier_model` es Python propio de stage0 exactamente como
`claude_agent_sdk` es el wheel propio de stage0: `scripts/stage0.sh` corre
`(cd "$dir" && env -u VIRTUAL_ENV uv sync --quiet --locked)` contra el clon
detenido en un solo sha, así que un commit instala los dos juntos. No existe
una ref en `master` que lleve el default de modelo nuevo sin el bump del lock
-- el invariante de esta tarea (`Configuration.verifier_model` y la entrada
`claude-agent-sdk` de `uv.lock` van en el mismo PR) hace esa combinación
irrepresentable. Esto se escribe explícito porque un lector que note que el
modelo se sirve desde el producto mientras el CLI corre desde stage0 va a
alcanzar exactamente esta preocupación: quien olvide re-pinear stage0 se queda
con el CLI viejo *y* el modelo viejo -- el mismo estado que funciona hoy --
nunca con el CLI viejo y el modelo nuevo.

## La ventana de precio cero en Langfuse

El precio de `claude-opus-5-5` en Langfuse es estado de la instancia, no del
repo (ADR-0008, su propio modo de falla documentado: un modelo sin precio
registrado factura observabilidad a costo cero, silenciosamente, porque
`probe.py` no es un check declarado -- nadie lo corre en cada delivery). Entre
el merge de este PR y que el operador registre el precio a mano, toda muestra
de `agentic.role.cost_usd` con `model="claude-opus-5-5"` puede leer en cero
sin que ningún check lo marque. El comando exacto para cerrar la ventana:
registrar el precio del modelo en la UI de Langfuse (Settings > Models) y
verificar con `uv run observability/collector/probe.py` después de
`MODEL = "claude-opus-5-5"` (este mismo PR ya lo deja así en
`observability/collector/probe.py:28`) -- el probe manda un span con ese
modelo y espera hasta que ClickHouse muestre costo mayor a cero.

## Lo que esta ADR no ejerce

Esta ADR no corre ningún rol de producto sobre `claude-opus-5-5` contra las
diecisiete costuras del sistema: la única evidencia es el gate de dos
funciones de `tests/llm/test_verifier_model.py` (el default, ahora
`claude-opus-5-5`, corrido una vez tras el bump) y el gate de cinco archivos
que ADR-0035 ya corrió y documentó como parcialmente no concluyente
(`test_denial_routing.py` y `test_turn_notice.py` no llegaron a ejercitar el
ruteo `can_use_tool`/`PermissionResultDeny` ni el conteo de turnos). Ninguna
tarea de producto real pasó todavía por este verificador con el modelo nuevo;
eso ocurre recién en la primera tarea después del re-pin de stage0 (ADR-0035,
"Stated assumptions", iii).

## Gate result (verificador)

Comando: `uv run pytest -m llm tests/llm/test_verifier_model.py -v -s`.

```
tests/llm/test_verifier_model.py::test_verifier_model_is_accepted_and_billed
resolved verifier_model default: claude-opus-5-5
attempt 1: green, cost 0.0067123999999999994
ResultMessage.is_error: False
ResultMessage.total_cost_usd: 0.0067123999999999994
ResultMessage.structured_output: {'markdown': '### Changes\nProbe, nothing built.'}
PASSED

1 passed in 4.01s
```

Costo medido: 0.0067123999999999994 USD, un run del único test que queda en el
archivo (la función `test_candidate_verifier_model_is_accepted_and_billed` y
el literal `CANDIDATE` se borraron en este mismo diff: la prueba del default
ya cubre `claude-opus-5-5`, y mantener las dos facturaba dos llamadas para
probar un solo string en cada corrida futura del gate).
