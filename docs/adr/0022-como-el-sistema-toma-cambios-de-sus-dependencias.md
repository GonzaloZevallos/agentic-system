---
status: accepted
date: 2026-09-22
---

# Cómo el sistema toma cambios de sus dependencias: lo que cambia el comportamiento de los roles va en su propio pull request

`claude-agent-sdk>=0.2.152` es un piso, no un pin: el lock fija la versión y cada bump cambia
el comportamiento de los roles sin que nada lo haga visible. Este ADR decide cómo entran esos
cambios. Es una decisión distinta de la de ADR-0021 —cómo este repo se tipa a sí mismo— y vive
aparte porque tiene otro radio de daño, otro costo de reversión y otro lector: quien algún día
quiera automatizar el submódulo de skills, o quien esté mirando una ola de escalados de setup
después de un fin de semana tranquilo, no va a abrir un ADR sobre alcance de mypy. Se tocan
sólo en `pyproject.toml`, que esta tarea deja con un único escritor.

## Un tick verde de CI no prueba nada sobre el harness

CI corre `uv run pytest -m "not llm"`, y todo lo que fija el comportamiento del SDK vive en
`tests/llm/`: la costura `PreToolUse` sobre `StructuredOutput` y el ruteo de
`PermissionResultDeny` (ADR-0011, cuarta y quinta adendas), la evidencia de parentesco de
`TRACEPARENT` (ADR-0020), `can_use_tool` bajo `permission_mode="default"`. Un PR **agrupado**
que suba el SDK junto a ruff y pytest, con un check verde que nunca ejercitó el harness, es
**menos** visible que hoy: el tilde verde ahora sugiere activamente que el bump es seguro.

De ahí el corte:

- **`claude-agent-sdk`, `langgraph` y `langgraph-checkpoint-sqlite` van cada uno en su propio
  pull request, nunca agrupados.** Los dos primeros porque son el harness y el motor del grafo
  (`Send`, reducers, `interrupt`); el tercero porque ADR-0003 eligió `AsyncSqliteSaver`
  específicamente por sus lecturas async, y un bump ahí mueve el checkpointer debajo de toda
  reanudación.
- **El resto va en un único PR semanal agrupado**: ruff, pytest, pydantic, pyyaml, anyio,
  python-dotenv, el par de OpenTelemetry y `langgraph-cli`.
- **Los PRs no agrupados nombran la compuerta manual en su cuerpo**, vía prefijo de
  `commit-message` y etiqueta: el humano es el único con la suscripción, y la compuerta es
  `uv run pytest -m llm tests/llm/test_delivery_rejection_visibility.py
  tests/llm/test_trace_propagation.py`. El tick verde prueba ruff, format y `-m "not llm"`, y
  el cuerpo del PR lo dice en vez de dejar que el tilde insinúe más.

## Qué ecosistemas entran y cuál queda afuera a propósito

- **`uv`: adentro**, semanal. Dependabot lee `uv.lock`.
- **`github-actions`: adentro**, semanal, agrupado. `actions/checkout@v4` y
  `astral-sh/setup-uv@v5` son majors flotantes que ya absorben parches en silencio; un PR hace
  visible el salto de major, que es justo lo que el pedido quiere.
- **`gitsubmodule`: afuera, a propósito.** ADR-0013 fija el plugin de skills en 1.2.3 y dice
  con todas las letras que actualizarlo es un commit visible, porque la versión queda en el
  frontmatter del documento de tarea al lado de los hashes de prompts y un bump cambia el
  comportamiento de los roles. Nada en CI puede verlo: el submódulo está excluido de ruff y lo
  estará de `files` (ADR-0021). Un PR automático ahí sería un tilde verde sobre un cambio de
  comportamiento no medido, que es exactamente la falla que este ADR existe para evitar,
  llegando por el otro lado. Lo sube el humano, deliberadamente.

**Sobre verificar el ecosistema `uv`**: ningún rol de este camino puede. `DEVELOPER.tools` es
`EXECUTE` (`roles.py:131`) —sin `WebFetch`, sin `WebSearch`— y los entrevistadores leen
archivos. Así que la decisión es del Tech Lead y se toma: `uv` es un ecosistema soportado. El
**fallback queda declarado y no construido** (estándares §4, nada especulativo): si GitHub
rechaza el ecosistema, lo dice en la página de Dependabot del repositorio, y el seguimiento es
un workflow con `schedule:` que corre `uv lock --upgrade` y abre un PR. Esa frase va en el
cuerpo del pull request, para que el humano sepa dónde mirar la semana siguiente al merge en
vez de que un Developer lo descubra a mitad de subtarea sin forma de chequearlo.

## El modo de falla peor de la lista no es un check en rojo: es un escalado de setup

`.agentic/stack.yaml` declara `setup: deps: uv sync --locked`, y ADR-0011 (tercera adenda)
hace que un setup fallido **escale directo, antes de que corra ningún rol**, con su propia
clase de escalado, descartando el checkout. El uv que corre ese comando es el que esté
instalado donde corre stage0; el uv que valida el lock en CI viene de `astral-sh/setup-uv`.

Entonces: un `uv.lock` regenerado por Dependabot bajo un uv más nuevo puede quedar verde en CI
y fallar `--locked` del otro lado, y el primer síntoma **no es un check en rojo**: es toda
tarea siguiente escalando al crear su worktree, sin nada en el documento de tarea que apunte a
un PR de dependencias mergeado el fin de semana.

Dos cosas, no una:

- **`astral-sh/setup-uv` lleva un `version:` explícito**, para que el lock se valide bajo un uv
  conocido y no bajo uno móvil. `tests/test_stack.py` lo verifica, lo que lo convierte en la
  única propiedad de este trabajo verificada por un chequeo concedido en el base de esta tarea.
  Editar ese `version:` es un commit humano, como el submódulo: uv es la única herramienta por
  la que pasan tanto `setup` como todos los `checks`, y nada en CI distingue su versión de la
  que corre el humano.
- **La referencia de la action se queda en el PR agrupado de `github-actions`**, y eso es
  seguro y no un descuido: Dependabot sube el ref de `uses:`, nunca edita el valor de un input,
  así que un PR de `setup-uv@v6` no puede mover el uv fijado. Lo que sí puede mover son los
  defaults de la action, y eso llega como un PR visible con el pin todavía en el diff.

El pin angosta la ventana y no la cierra —el uv del humano y el de stage0 no los fija nada que
esta tarea alcance, y fijarlos es otro cambio con otro radio—, así que queda escrito acá: **un
`uv.lock` regenerado bajo un uv más nuevo no falla como check en rojo, escala toda tarea al
crear el checkout, bajo la clase de escalado de setup.** Una ola de esos escalados después de
un fin de semana tranquilo significa mirar el último PR de dependencias mergeado. Es el único
síntoma de esta clase que el sistema reporta.

## Opciones descartadas

- **Un único PR agrupado semanal con todo adentro**, que es lo que el pedido sugiere: esconde
  el bump del SDK detrás de un tilde verde que nunca corrió `tests/llm/`, que es peor que hoy.
- **Un tercer stream no agrupado para `setup-uv`**: no compra nada sobre el pin, porque el ref
  de la action no puede mover el valor del input.
- **Automatizar `gitsubmodule`**: ver arriba; ADR-0013 ya decidió que ese bump es un commit
  visible del humano.
- **Construir el fallback de `uv lock --upgrade` por las dudas**: código especulativo que los
  estándares §4 prohíben, para un caso que la página de Dependabot del repositorio reporta sola.

Ver ADR-0021 para el chequeo de tipos que entra en la misma tarea; se tocan sólo en
`pyproject.toml`.
