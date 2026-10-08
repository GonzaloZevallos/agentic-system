---
status: accepted
date: 2026-10-07
---

# Los chequeos pueden correr solo lo que el cambio afecta, hasta el merge

La puerta de entrega del Developer, la reproducción del revisor y el merge corren cada
chequeo del `stack.yaml` sobre todo el árbol (ADR-0016, adenda: "La puerta de entrega corre
todos los chequeos"; ADR-0045 para el merge). En apostrophe eso son ~2560 tests de pytest,
vitest y el Playwright completo (~7,5 min en serie) para un cambio de un archivo de CSS, y
con varias corridas encima el e2e llega a `check_timeout_s` (17 de 60 tareas). Casi todo el
tiempo y los timeouts vienen de tests que el cambio no puede afectar.

Este documento (GH-215) **enmienda por nombre** la frase de ADR-0016, "la puerta de entrega
corre todos los chequeos": para un producto que lo declara, la puerta corre la forma
affected-only de un chequeo cuando el cambio lo permite. ADR-0016 no se edita; esta es la
enmienda, y el resto de esa adenda sigue en pie. Este documento lo registra por secciones,
una por subtask: la llave y el contrato (`stack-key`, abajo), la puerta, el revisor y el
dominio en las suyas.

## La llave: `affected_checks` y `affected_fallback`

`.agentic/stack.yaml` gana dos llaves hermanas opcionales, leídas en el base de la tarea
como `checks` (ADR-0016):

- **`affected_checks: {nombre: comando}`.** Cada nombre debe estar también bajo `checks`:
  un nombre que no corre nunca haría creer al producto que corre. Cada comando es un string
  no vacío que contiene el token literal `{base}` y **no empieza con él**: la regla de
  permiso del revisor es el prefijo literal anterior a `{base}`, y un comando que empieza
  con el token no tiene prefijo que permitir.
- **`affected_fallback: [glob, ...]`.** Una lista de strings no vacíos; la lista vacía es
  válida y equivale a ausente.
- **Ausentes** se leen `{}` y `()`. **Presentes y rotas** levantan `ValueError` que nombra
  la llave y la fuente, en la carga y otra vez en `preflight.declared_stack` (que las llama
  solo para validar; su retorno sigue siendo `setup`), igual que `witness`, `design` y
  `parallel_checks` (ADR-0044). `checks()` no se toca: todo consumidor existente de
  `checks` queda idéntico byte por byte, y un producto que no declara nada recibe la misma
  puerta, la misma tabla `### Checks`, las mismas entradas `presubmit_checks`, los mismos
  atributos de span y las mismas reglas del revisor que hoy.
- **Puerto.** `ProductStack.affected_checks(revision)` y `affected_fallback(revision)`;
  `GitStack` los lee del commit que se le pide.

## El `{base}`

`{base}` es siempre `task_base(state, ports)` (`application/checkouts.py`): el comienzo de
la rama de la tarea, de modo que el comando mide **`task_base..head`**, la rama entera y no
el incremento que un revisor está juzgando. La puerta, la descripción del revisor y la regla
de permiso del revisor lo toman de ahí. El dominio lo recibe como argumento y nunca lo
calcula. Se sustituye con `str.replace("{base}", sha)`, nunca con `str.format`, porque los
comandos reales llevan llaves (`${VAR}`, JSON, awk).

## El contrato del comando

Un comando affected-only **debe salir con código distinto de cero cuando no selecciona
nada**: el exit 5 de pytest lo hace, y vitest también sin `--passWithNoTests`. El producto
no agrega `--passWithNoTests` ni equivalente (la plantilla lo dice). El sistema nunca lee la
salida de la herramienta; solo el código de salida. Una selección vacía que saliera en verde
sería un verde sin tests.

## Cuándo corre completo

Cada chequeo corre `full` cuando se da cualquiera de estos:

- no tiene forma affected-only;
- el conjunto de rutas cambiadas está vacío;
- alguna ruta cambiada coincide con un glob de `affected_fallback`. `.agentic/stack.yaml` es
  siempre uno de ellos, agregado por el dominio y no por el producto: un cambio al propio
  contrato de los chequeos no se mide con una selección que ese cambio pudo mover.

Si no, corre `affected-only`. Los globs se comparan con `fnmatch.fnmatchcase` sobre la ruta
POSIX completa, así que **`*` cruza `/`**: un glob amplio cae a completo más a menudo de lo
que un producto podría esperar, y eso es hacia el lado seguro. Las rutas salen de
`Workspace.changed_paths(base, head)`; si esa llamada levanta `GitError` u `OSError`, todos
los chequeos corren completos. Ninguna falla de la selección omite un chequeo: un lookup que
falla, un conjunto vacío o una corrida affected que no pasa resuelven a la forma completa,
nunca a no correrlo.

## Reintento y "se queda completo"

Una corrida affected que no pasa (código distinto de cero o timeout) es seguida, en la misma
llamada de la puerta, por la forma completa del mismo chequeo, que se registra como segunda
fila. Un chequeo que falló en su forma completa dentro de un `CheckoutPresubmit` corre solo
completo en cada invocación posterior de ese objeto. Ese conjunto vive en la instancia: una
corrida nueva del Developer vuelve a empezar en affected-only. El peor caso por chequeo y
por corrida son tres corridas affected y tres completas.

## Las filas y quién decide

Un chequeo puede dejar ahora dos filas. Solo `presubmit.final_results(rows)` (la última fila
por nombre, en el orden declarado) y `presubmit.check_durations(rows)` (segundos sumados por
nombre) deciden si un chequeo pasó o falló y cuánto tardó. Los consumidores: `presubmit.refusal`
(que recibe `final_results`), `_presubmit_arrays` de `telemetry.py` (estado de
`final_results`, duración de `check_durations`; con una fila por nombre la salida es idéntica
byte por byte) y la tabla. Solo `checks_markdown` y el registro crudo `presubmit_checks` del
parche muestran las filas reemplazadas. Cada fila escrita bajo un plan lleva `mode`
(`affected-only` o `full`), puesto por el sistema y nunca por el modelo. `merge.py` no se
edita: nunca arma un plan, así que siempre tiene una fila por nombre.

## El merge siempre corre completo

`agentic merge` (ADR-0045) corre **siempre** las formas completas, sobre el head mergeado con
el master. Nada se mergea sobre una corrida parcial. Nunca pasa un plan affected a
`CheckoutPresubmit` ni lee `affected_checks` ni `affected_fallback`. La puerta del Developer y
la del revisor son intermedias; la completa es la última, y es la que decide el merge. El
diagnóstico de bugs (`diagnoser_for`, `diagnose.py`) también se queda en los chequeos
completos.

Un producto que declara `ci` (ADR-0045, adenda GH-216) mergea sobre su CI verde y fresco sin
correr chequeos locales: ahí la corrida completa es la del CI, que debe correr la suite entera.

## El revisor

`describe_checks` gana una descripción affected opcional: cada comando affected con el base
ya sustituido, junto a su comando completo; dice que una fila `affected-only` se seleccionó
contra `task_base..head` (la rama entera, no el incremento bajo revisión), que no es
evidencia de la suite completa y que la suite completa corre en el merge. `reviewer_for`
agrega una regla `Bash(<prefijo literal antes de {base}>:*)` por comando affected, además de
`check_rules`. Ningún archivo bajo `domain/prompts/` cambia, así que `prompts.fingerprint` no
se mueve.

## Techos de timeout

Cuando hay un plan, el techo de `timeout_s` es `check_timeout_s × (len(checks) + len(affected))`
en serie y `2 × check_timeout_s` en paralelo, más el testigo en ambos. Son techos, no gasto:
en una entrega verde el costo escala con la selección y no con el árbol; una selección roja o
vacía paga una corrida completa extra por chequeo.

## Observabilidad y seguridad

- **§7.** No se agrega, mueve ni renombra ninguna métrica, etiqueta, span ni atributo de span.
  `agentic.presubmit.checks` y `.duration` cambian de significado solo para los productos
  que optan: el estado de un chequeo es su fila final y su duración la suma de sus filas. El
  modo se lee del `presubmit_checks` del documento. `observability/README.md` lo dice.
- **§8.** Nada nuevo es alcanzable. Los comandos affected los declara el producto, como
  `checks`, y corren en el mismo checkout con el mismo entorno. El revisor gana una regla
  `Bash(prefijo:*)` por comando declarado y sigue sin poder escribir nada. Una vez que hay
  plan, el costo escala con la selección en lugar del árbol entero.

## Alternativas descartadas

- **Que el sistema elija la selección.** Cada stack sabe cómo seleccionar (vitest
  `related`/`--changed`, Playwright `--only-changed`, pytest por mapeo de rutas o testmon);
  el sistema solo pasa el base.
- **Leer la salida de la herramienta para saber si seleccionó algo.** Frágil y distinto en
  cada herramienta; el código de salida distinto de cero es el contrato.
- **Mergear con la corrida affected cuando la puerta ya pasó.** Descartado: una selección
  vieja que elige algunos tests y pasa es un verde parcial que el sistema no ve.

## Riesgos conocidos

- Una selección vencida que elige algunos tests y pasa es un verde sobre un conjunto parcial,
  y el sistema no lo ve. La única corrida completa es `agentic merge`, después de un ciclo de
  revisión. Por eso el registro y el texto del revisor dicen `affected-only`.
- La puerta de esta propia tarea lee el stack viejo en el base, así que ningún producto real
  ejerce las llaves hasta que una declare. Solo los dobles y la prueba con git real las
  prueban aquí. Las entradas de apostrophe quedan fuera de alcance.
- Los globs donde `*` cruza `/` hacen que los patrones amplios caigan a completo más a menudo
  de lo que un producto esperaría.
- En paralelo, el tiempo de pared de un chequeo es la suma de sus propias filas; el de la
  puerta es la mayor de esas sumas, y sumarlas todas lo sobrestima.
