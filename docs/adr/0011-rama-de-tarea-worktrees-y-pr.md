---
status: accepted
date: 2026-09-13
---

# Una rama por tarea, un worktree por subtarea, y el pull request como entrega al humano

Hasta acá el sistema no tocaba código: la primera corrida real (GH-12 en gym-saas) terminó
en `definition_ready` con el conocimiento de producto destilado y ni una línea en `app/`.
Con Tech Lead y Developer entra el código, y con él tres preguntas que el README dejaba
implícitas: dónde escribe cada Developer, cómo se unen las subtareas, y qué recibe el
humano al final cuando todavía no hay QA que cierre.

**Cada subtarea trabaja en su propio worktree y su propia rama.** ADR-0004 decidió que
los Developers son nodos con `Send` y corren en paralelo; dos procesos de Claude Code
escribiendo la misma carpeta se pisan. El worktree vive bajo `.agentic/worktrees/<id>/`
del producto, fuera del control de versiones, y la rama se llama
`agentic/<tarea>.<subtarea>` (con punto: git no admite `agentic/GH-12` y
`agentic/GH-12/api` a la vez). Una subtarea que depende de otra arranca con el código de
esa otra: su rama aprobada se mergea en la de la dependiente antes de que el Developer
empiece. Al entregar, los worktrees se borran (el cierre es el merge humano y el sistema
ya no corre); las ramas quedan, así un escalado a mitad del desarrollo no pierde el
trabajo ya hecho aunque el checkpointer de ADR-0003 todavía no exista. El integrador lo
designa el sistema, no Tech Lead: es una subtarea más, `integration`, que entra cuando
todas las reales están aprobadas y hay más de una. Entra **entregada** cuando los merges
del fan-in fueron todo el trabajo, así se revisa la rama entera sin que ningún Developer
cobre por un merge ya hecho, y pendiente cuando quedó un conflicto o el plan declaró
trabajo de integración (issue #42).

**Una rama de tarea, `agentic/<tarea>`, creada al arrancar la etapa de definición.** No
al empezar a desarrollar (ni en la clasificación, que puede escalar antes de tocar nada): la entrevista de definición (ADR-0014) ya escribe glosario y ADRs, y eso
tiene que viajar en la misma rama que el código. Ahí el sistema une las ramas de subtarea,
una por una apenas se aprueban (ADR-0004, adenda quinta), y el Developer integrador termina
lo que ese merge no pudo. Con una sola subtarea no hay integración: su rama es la rama de
tarea.

**La entrega es un pull request.** El criterio de éxito (README §9) cuenta una tarea solo
si el humano mergea el diff sin tocarlo; eso pide un PR. El sistema lo abre desde la rama
de tarea con la definición única y el plan de Tech Lead en el cuerpo, y deja en el issue un
comentario con el link al PR y al documento de tarea. El estado de la tarea es
**listo para revisión**; mientras el camino no tenga QA, el merge es el cierre. Es el
primer adaptador de escritura a GitHub; hasta ahora solo se leía. ADR-0001 sigue en pie:
prohíbe que los roles se hablen por el issue, no que el sistema le hable al humano.

## Opciones descartadas

- **Un solo Developer a la vez sobre la carpeta principal**: anula el paralelismo que
  ADR-0004 ya decidió y deja al integrador sin trabajo que justifique su existencia.
- **Solo la rama local, sin PR**: el humano tendría que abrirlo a mano y §9 mediría un
  paso que el sistema no hizo.
- **Rama de tarea creada al desarrollar**: los ADRs y términos de la entrevista quedarían
  en la rama principal sin pasar por la revisión del PR.

## Adenda (2026-09-14): una sola identidad, en el entorno, y reglas que el Developer no salta

La segunda corrida de GH-12 mostró que la identidad del sistema (`agentic-system`) sólo
firmaba los commits del adaptador git, que la pasaba con `-c user.*`. El Developer
commitea por su cuenta dentro de su worktree y no la recibía: en una máquina sin
`git config user.*` su primer commit falló con "Author identity unknown" y quemó un
intento entero; en el reintento escribió la identidad del humano en el `.git/config`
compartido del producto y firmó ocho commits con ella.

**La identidad viaja como entorno, no como config ni como flag.** `Configuration` arma
una sola vez las cuatro variables `GIT_AUTHOR_*` y `GIT_COMMITTER_*`; el adaptador git las
pone en el entorno de cada comando suyo, y el runner del SDK las agrega al proceso de cada
rol, así que cualquier `git` que un rol corra por Bash firma como el sistema. En git el
entorno le gana al config, de modo que aunque el modelo insistiera con `user.*`, el commit
sale igual como `agentic-system`. Es un solo camino: el bug salió justo de tener dos.

**Lo que el prompt prohíbe, una regla lo respalda.** Los roles que ejecutan corren en
`bypassPermissions`, pero el SDK evalúa las reglas de denegación antes que el modo (documentado
en "Configure permissions" del Agent SDK). Developer e integrador llevan `denied` con
`git config`, `git push` y `git worktree`: la primera corta el camino que el modelo tomó
para contaminar el config del producto (la mitad del bug que el entorno no cubre), las otras
dos cubren lo que hasta ahora sólo decía el texto. Son reglas por prefijo: suben la barrera,
no la cierran (`git -C ruta config` o editar `.git/config` a mano las esquivan); la garantía
de que el commit sale firmado como el sistema la da el entorno, no la regla. `git checkout`
queda afuera: `git checkout -- archivo` es un uso legítimo.

Descartado: **`git config --worktree user.*` al crear cada worktree**. Necesita
`extensions.worktreeConfig` en el `.git/config` compartido del producto, que es justo lo que
no queremos tocar. Descartado también **una identidad por rol** (`agentic-developer`): la
rama y el mensaje ya dicen qué rol y qué subtarea; dos identidades duplican configuración
sin distinguir nada que el log no distinga.

## Adenda (2026-09-15, segunda): hooks del SDK como jaula dura, y la puerta de entrega

La adenda anterior dejó dicho que las reglas de denegación "suben la barrera, no la
cierran": son por prefijo, así que `git -C ruta config`, `--git-dir` o un Edit de
`.git/config` pasan. Y en la corrida 4 de GH-12 dos devoluciones fueron por cosas que el
estándar y el plan ya prohibían por escrito: dos `# type: ignore` en `dashboard-slice` y un
fix sin commitear en `expiring-soon`. La instrucción sola no alcanzó, y cada una costó un
pase de revisión (2.5 a 8.5 USD) más el Developer que la corrigió (issue #22).

**Los roles que ejecutan corren dentro de hooks `PreToolUse` del SDK.** Un hook ve la
llamada parseada, no un prefijo, y su deny vale también en `bypassPermissions` (doc de
permisos del Agent SDK, verificado en 0.2.152). Las decisiones son funciones puras en
`adapters/claude_sdk/hooks.py`, testeadas sin SDK:

- **Bash**: `git config` (leer con `--get`/`--list` sí), `git push`, `git worktree`,
  `--git-dir`, `git -C` fuera del checkout, `git commit --no-verify` y cambiar de rama
  (`git switch`, `git checkout` sin `--`), más `GIT_DIR=`, `git -c user.*` y
  `git -c core.hooksPath`. El comando se parte con `shlex`, así que cuenta después de `;`,
  `&&`, `|` o un salto de línea, dentro de `$(...)`, en `sh -c '...'` o `bash -lc '...'` y
  detrás de `VAR=valor`. El cuerpo de un heredoc no cuenta: un mensaje de commit que
  menciona `git push` pasa.
- **Edit y Write**: rutas dentro de `.git/` o fuera del checkout, y un `# type: ignore` o
  `# noqa` nuevo en un archivo Python. Uno que ya estaba no es de la tarea: el estándar
  aplica al código nuevo.

Qué no cubre, a propósito: un shell que se mueve primero (`cd ..; echo > archivo`),
archivos escritos por Bash (`sed -i`, `cat >`), comandos armados en tiempo de ejecución
(`$VAR`, `eval`, alias, `$(...)` entre comillas dobles), un script que entra a un shell
por stdin (`bash <<EOF`), symlinks que salen del checkout y
una línea que `shlex` no parsea, que pasa. Las reglas de denegación siguen puestas, y la
firma la sigue garantizando el entorno. La jaula sube la barrera sobre el prompt; no es un
sandbox.

**La entrega pasa por una puerta antes de contar.** Cuando el Developer o el integrador
entregan, el sistema mira su checkout: que haya commit, que el árbol esté limpio y que los
chequeos del producto pasen (ADR-0016, adenda). Si algo falta, rechaza la entrega con el
motivo, el comando y las últimas líneas, y el modelo lo recibe dentro del mismo turno y
vuelve a entregar. Rechaza como mucho dos veces por corrida de rol, reintento incluido;
después deja pasar, y
lo que dijeron los chequeos queda al principio de la sección del Developer, escrito por el
sistema, para que el revisor lo lea. El chequeo de commit y árbol limpio después de la
corrida sigue como respaldo: saltarlo es fallo de rol.

**La puerta es un `PreToolUse` sobre `StructuredOutput`, no un hook `Stop`.** Un spike
contra el SDK real lo decidió. Con `output_format`, el turno termina en la llamada a la
herramienta de salida estructurada. `Stop` dispara después, y un `block` ahí queda en la
transcripción como `hook_blocking_error`, pero la sesión termina igual con la entrega. Un
deny sobre `StructuredOutput` le llega al modelo como resultado de la herramienta: corre lo
que se le pide, vuelve a entregar y el stream trae un solo `ResultMessage`, así que el corte
en la primera entrega del runner (issue #16) no cambia. El timeout del hook es explícito:
el de los chequeos (`AGENTIC_CHECK_TIMEOUT_S`, 600 s cada uno) más un margen. El input de
`Stop` sí trae `background_tasks`: sirve para saber si un rol entrega con sub-agentes
corriendo, si alguna vez hace falta.

Descartado:

- **Puerta como hook `Stop`**, lo que proponía el issue: no frena una entrega por salida
  estructurada.
- **Límite de archivos tocados fuera de las costuras**: necesita un campo estructurado en
  `TechPlan` y cambia la huella de prompts. Pospuesto.
- **`PostToolUse` con ruff por archivo**: vale poco al lado de la puerta y ensucia el
  contexto.

## Adenda (2026-09-17, tercera): la jaula incluye el entorno de ejecución

Las dos adendas anteriores cerraron lo que un rol escribe: la identidad de git por
entorno, las reglas de denegación, los hooks del SDK. Ninguna cubre dónde **importa** el
código que corre. Un worktree no tiene entorno propio, así que una herramienta que busca
uno sube hasta encontrar el del producto. Verificado con uv 0.12.13: desde un
subdirectorio sin `.venv`, `uv run --no-project` usa el `.venv` del padre; con uno propio
adentro, le gana.

Lo que eso costó, medido en las corridas de GH-12: 22 invocaciones de `uv pip install -e`,
6 de `uv venv`, 7 de `uv sync`, 18 `ModuleNotFoundError: No module named 'app'` y 110
apariciones de `VIRTUAL_ENV`. Cada Developer chocó con la misma pared y la resolvió
distinto, y ninguna quedó en el documento de tarea. Peor: un `uv pip install -e .` adentro
de un worktree reescribe el editable install del entorno **compartido**, así que el
`pytest` de las subtareas hermanas, del integrador y del revisor importó el código de la
rama de `sql-adapter`. Las corridas 2, 3, 4 y el rescate corrieron con eso puesto: una
suite verde valía menos de lo que parecía. Con el sistema como segundo producto (ADR-0017)
el blanco además es el checkout del humano, que queda con `ModuleNotFoundError` cuando
`cleanup` borra el worktree al que quedó apuntando.

**El sistema prepara el entorno de cada checkout, y lo declara el producto.** La clave
`setup` de `.agentic/stack.yaml`, comandos con nombre en orden, que el sistema corre una
sola vez cuando el checkout nace, en su directorio, antes de que entre ningún rol. Quién
prepara qué es del producto, como los chequeos: hardcodear `uv venv` metería un ecosistema
adentro del sistema, que es justo lo que ADR-0016 evitó al no parsear la CI. Es opcional
(un producto en Go o sin dependencias no prepara nada) y cuando falta, `preflight` lo dice
al arrancar: con el entorno compartido el sistema no puede prometer aislamiento.

**Corre una sola vez, al crear el checkout.** Una subtarea que agrega una dependencia la
instala en su propio entorno, y eso ya es trabajo suyo. El checkout de tarea nace en la
etapa de definición y los worktrees de la ola en el fan-in, uno por uno; pedirlo de nuevo
devuelve el que existe.

**Si el setup falla, escala directo**, con el paso, el comando y sus últimas líneas. No es
fallo de rol: el rol todavía no corrió, y un entorno que no se arma no lo arregla el
modelo. **Y el checkout se descarta**, rama y commits intactos: git deja el worktree donde
está cuando un comando falla adentro, así que si quedara, toda pregunta posterior lo
reportaría como existente y el resume que la escalada pide correría roles en el mismo
entorno a medio armar que el humano acaba de arreglar.

Los worktrees de la ola nacen en el fan-in y no adentro de una rama del fan-out: una rama
nunca rutea al escalado, así que ahí el fallo no tendría a quién contárselo. No alcanza
con eso solo, igual: el rescate desde documento poda los worktrees y `deliver` los borra,
de modo que un nodo posterior puede ser el que cree uno. Cada uno de los seis que piden
checkout atrapa el fallo y lo escala con el rol que estaba por arrancar.

Descartado: **negar `pip install` / `uv pip` / `uv venv` con los hooks**. Prohibiría el
caso legítimo del punto anterior, y la garantía la da el entorno preparado, no la regla,
igual que con la identidad de git en la primera adenda.

## Adenda (2026-09-18, cuarta): un rol que no ejecuta también se entera de cuál es su regla

Las tres adendas anteriores son sobre lo que un rol que **ejecuta** no puede hacer: la
identidad por entorno, las reglas de denegación, los hooks, el entorno preparado. Para un
rol que no ejecuta, la negación nunca tuvo motivo: `hooks_for` instala sus matchers sólo
`if role.executes`, y un rol de solo lectura corre en `permission_mode="default"` con sus
`allowed`, así que lo que no está permitido lo niega la capa de permisos con su texto
genérico. Lo que eso cuesta está medido en la corrida de #69: el Product Analyst de bug
intentó escribir un test de reproducción por Bash (`cat > …`), leyó la negación como un
problema de sintaxis y reintentó con heredoc, con `python3` por stdin cinco veces y con
`base64` en las dos direcciones, ocho turnos en variantes de lo mismo, hasta agotar sus
turnos sin dictamen.

**La negación que recibe un rol que no ejecuta nombra la regla de su rol**, no la ruta ni
el comando que quedó afuera: que escribir está fuera de su rol, y qué sí puede hacer en su
lugar (reproducir leyendo el código, los documentos de tarea y los tests existentes, o
corriendo los chequeos que el producto declara). El comportamiento lo gobierna
`docs/product/role-failures.md`.

**El mecanismo es la costura de permisos del SDK, no un hook.** El SDK fijado tiene
`can_use_tool` con `PermissionResultDeny(message=...)`, y su regla de sombra es
exactamente nuestra semántica: no se consulta bajo `bypassPermissions` ni para lo que una
regla de `allowed` ya aprueba. Para un rol en `default` eso significa que el callback se
dispara justo sobre las llamadas que sus propias reglas no cubren, que es lo que un intento
de escritura es. Con eso no hace falta decidir si `cat > f` o `sed -i` escriben: la capa de
permisos ya decidió que no son un chequeo permitido. Un hook con detector de escritura
sería un segundo parser de shell al lado del que ya está, y la deriva contra la que el
propio docstring de `hooks.py` advierte.

**El fallback, declarado y no especulativo: el prompt, no un matcher.** `can_use_tool` se
cablea desde `ClaudeSDKClient` y el runner usa el camino de una sola vuelta de `query()`,
así que la primera tarea de la subtarea es un spike contra el SDK fijado, como la segunda
adenda ganó su diseño con el suyo: una prueba en `tests/llm/` que corre un rol de solo
lectura contra el CLI real, intenta escribir y verifica que el texto con la regla del rol le
llega al modelo. Si el spike muestra que `query()` no rutea el callback, **no entra
ningún matcher**: el fallback es la frase en el prompt del rol (escribir está fuera de tu
rol; reproducí leyendo o corriendo los chequeos declarados; una negación no es un problema
de sintaxis), y se acepta el texto genérico de la capa de permisos.

El motivo es que un hook que derive la decisión de `Role.allowed` tendría que reimplementar
la semántica de prefijo de `Bash(<comando>:*)` contra comandos que el producto declara y que
el prompt le pide al rol extender con un objetivo (`<comando> ruta::test`). Un matcher un
carácter más estricto que el del SDK niega un chequeo legítimo, y el Product Analyst de bug,
cuya única herramienta que no es lectura *es* ese chequeo, se queda sin poder reproducir
nada: un fallo peor que el que esta adenda arregla, e invisible para los tests escritos
contra ese mismo matcher. De ahí la regla general: **un mecanismo de negación sólo puede
agregarle un motivo a una llamada que la capa de permisos ya iba a rechazar, nunca decidir
por su cuenta qué puede correr un rol.** La evidencia de que la frase sola alcanza para la
regresión medida es que en #81 el rol no intentó escribir nunca y que en #69 el prompt no le
decía qué hacer en lugar de escribir.

Bajo el fallback no hay dato: la cuenta de negaciones, su campo en `Failure` y la línea del
documento que PM definió pierden su fuente, y eso se reporta como brecha a PM para la tarea
siguiente, no se resuelve con un rodeo.

**El texto es del dominio; los constantes del adaptador se quedan donde están.** La frase
que nombra la regla de un rol es lenguaje del dominio y se deriva de `role.executes`,
`role.tools` y `role.allowed`: vive en `agentic/core/process/domain/denials.py`, con su
test unitario, y el adaptador sólo la pasa a `PermissionResultDeny.message`. Los constantes
de la jaula del rol que ejecuta (`CONFIG`, `PUSH`, `WORKTREE`, `SILENCED`…) siguen en
`adapters/claude_sdk/hooks.py`: son forma vieja, los estándares aplican a lo que esta tarea
toca y no a lo que ya existía, y unificar las dos es un platform enhancement, nunca una
edición al pasar.

Consecuencias:

- El texto sube la barrera del prompt; no es un sandbox nuevo. La garantía de que el rol no
  escribe la sigue dando el permiso, igual que en la segunda adenda.
- La prueba del spike vive en `tests/llm/`, que los chequeos declarados de este repo no
  corren (`pytest -m "not llm"`): va acompañada de una prueba sobre `options_for`, que sí
  corren, que falla si el cableado desaparece, y el Developer deja en su sección el extracto
  de la corrida del spike como evidencia, porque el revisor no puede reproducirla con los
  chequeos declarados.
- Cuántas negaciones tuvo una corrida queda escrito en el documento, en una línea que
  escribe el sistema cuando hubo al menos una. No lleva métrica propia: PM lo dejó como
  supuesto en esta tarea.

## Adenda (2026-09-21, quinta): la compuerta para el guard de schema pasa, y con qué forma

Issue #88 (reemisión 3-13 veces por turno de una entrega estructurada inválida, #32):
la corrida real mostró el Developer llamando a la herramienta de salida estructurada
varias veces seguidas dentro de un mismo turno, con `[Developer] delivery:` 13 veces
contra 7 `[Developer] done`. La lectura de código antes de correr nada decía algo
importante: `ClaudeSdkRunner.run_once` sólo mira `AssistantMessage`, `StreamEvent` y
`ResultMessage`, y `schemas.validate` (el dominio) corre una sola vez, sobre el
`ResultMessage` final. Si eso fuera todo, en #32 `schemas.validate` habría corrido cero
veces, no trece: los reintentos pasaron *adentro del CLI*, contra su propio
`output_format`, antes de que nuestro código viera nada. La pregunta que esta subtarea
(`gate`) tenía que cerrar antes de que la subtarea `schema-guard` pudiera existir: ¿un
hook puede ver esos reintentos, y lo que ve es el mismo objeto que la validación de
verdad usaría?

**La compuerta pasa, con las cuatro condiciones así:**

(i) **Sí.** El rechazo del CLI llega al stream como `UserMessage` con un
`ToolResultBlock(is_error=True)`, cuyo `content` es un string con el mensaje de
validación del propio esquema JSON del CLI: `"Output does not match required schema:
root: must have required property 'summary', root: must have required property
'assumptions'"`. Nombra las claves exactas que faltan — lo mismo que el pedido original
quiere que el modelo reciba en el primer rechazo.

(ii) **No.** Un `PostToolUse` sobre `StructuredOutput` no se disparó sobre la llamada
rechazada; sólo se disparó una vez, sobre la llamada válida que vino después, con
`tool_response: 'Structured output provided successfully'`. El CLI no llega a
considerar "ejecutada" la llamada que su propio esquema rechaza, así que `PostToolUse`
no es la costura para contar ni para interceptar un rechazo de schema.

(iii) **Sí, y corre antes.** Un `PreToolUse` sobre `StructuredOutput` vio el
`tool_input` inválido completo (`{'markdown': '...</markdown>\n<summary>one line
</summary>\n<assumptions>none</assumptions>\n'}`) y lo vio *antes* de que el rechazo del
CLI llegara al stream: en el orden real de la corrida, el evento de `PreToolUse` quedó
en la posición `[1]`, el `ToolResultBlock(is_error=True)` en la `[2]`. `PreToolUse`
corre sobre la intención de la llamada, no sobre su resultado, así que un guard ahí
puede agregar su propio motivo (las claves que faltan) *antes* de que el modelo reciba
el genérico del CLI, o en su lugar.

(iv) **Sí, la que gobierna.** Sobre la llamada válida, el `tool_input` que vio el
`PreToolUse` (`{'markdown': '### Changes\nNothing, this is a probe.', 'summary':
'Probe call, no actual changes made.', 'assumptions': ''}`) es exactamente el mismo
diccionario, clave por clave y valor por valor, que `structured_output` del
`ResultMessage` de esa misma llamada. Sin diferencia de forma en `assumptions` que
discutir: como el rol de juguete usa `Delivery` (el esquema real de Developer),
`schemas.json_schema` ya convierte ese campo `list[str]` en un `string` para el modelo
(`schemas.line_lists`), así que el CLI pide y entrega el mismo string en las dos
costuras. La nota del plan sobre esa forma no tuvo que aplicarse: no hubo con qué
tropezar.

**Conclusión para `schema-guard`:** el canal existe, y con la forma que el plan pedía
verificar antes de construir nada. Un `PreToolUse` sobre `StructuredOutput` es la
costura correcta: ve el intento antes de que el CLI lo rechace, ve exactamente el mismo
objeto que una validación downstream usaría, y puede agregar el motivo con las claves
faltantes al mismo turno en el que el modelo ya recibe el genérico del CLI —o antes,
denegando primero con un motivo mejor, según decida esa subtarea. `PostToolUse` queda
descartado como costura para ese guard: nunca ve la llamada que el schema del CLI
rechazó, sólo la que quedó.

**Consecuencia para la métrica que ya existe.** El extracto muestra a `StructuredOutput`
llamado dos veces en el mismo turno cuando el CLI rechaza la primera (`[0]` inválida,
`[3]` válida): eso ya es, hoy, una llamada más en `tool_calls["StructuredOutput"]` sobre
`AssistantMessage` (`runner.py:255-256`), la fuente de `agentic.role.tool_calls
{tool="StructuredOutput"}`. Cuando `schema-guard` entre y deniegue esa primera llamada
por su cuenta en vez de dejar que el CLI la rechace, el modelo la reintenta igual, así
que sigue sumando una llamada más a esa misma serie: **una denegación del guard produce
una emisión de entrega más en `agentic.role.tool_calls{tool="StructuredOutput"}` de esa
corrida.** Queda dicho acá para que una subida de esa serie después del commit de
`schema-guard` se lea como "el guard denegó y el modelo reintentó", no como "el modelo
empeoró": la serie no distingue de dónde vino el rechazo, del CLI o del guard, y no hay
plan de que lo haga.

### Extracto del spike

```
$ uv run pytest -m llm tests/llm/test_delivery_rejection_visibility.py -q -s
TIMELINE (in call order):
  [0] AssistantMessage.ToolUseBlock: {'markdown': '### Changes\nsomething.\n</markdown>\n<summary>one line</summary>\n<assumptions>none</assumptions>\n'}
  [1] PreToolUse: {'markdown': '### Changes\nsomething.\n</markdown>\n<summary>one line</summary>\n<assumptions>none</assumptions>\n'}
  [2] UserMessage.ToolResultBlock: {'is_error': True, 'content': "Output does not match required schema: root: must have required property 'summary', root: must have required property 'assumptions'"}
  [3] AssistantMessage.ToolUseBlock: {'markdown': '### Changes\nsomething.', 'summary': 'one line', 'assumptions': 'none'}
  [4] PreToolUse: {'markdown': '### Changes\nsomething.', 'summary': 'one line', 'assumptions': 'none'}
  [5] PostToolUse: {'tool_input': {'markdown': '### Changes\nsomething.', 'summary': 'one line', 'assumptions': 'none'}, 'tool_response': 'Structured output provided successfully'}
  [6] UserMessage.ToolResultBlock: {'is_error': None, 'content': 'Structured output provided successfully'}
  [7] ResultMessage: {'is_error': False, 'subtype': 'success', 'structured_output': {'markdown': '### Changes\nsomething.', 'summary': 'one line', 'assumptions': 'none'}}
condition (i) CLI rejection reached the stream: True at [2]
condition (ii) PostToolUse fired on the invalid call: False at []
condition (iii) PreToolUse saw the invalid input: True at [1]
condition (iii) PreToolUse ran before the CLI's own rejection reached the stream
.PreToolUse tool_input(s) seen: [{'markdown': '### Changes\nNothing, this is a probe.', 'summary': 'Probe call, no actual changes made.', 'assumptions': ''}]
ResultMessage.structured_output: {'markdown': '### Changes\nNothing, this is a probe.', 'summary': 'Probe call, no actual changes made.', 'assumptions': ''}
ResultMessage.is_error: False
condition (iv) tool_input == structured_output (deserialised, same object): True
.
2 passed in 8.09s
```

El rol de juguete (`tests/llm/test_delivery_rejection_visibility.py`, `TOY_ROLE`) no
entra al dominio: se construye en el propio archivo del spike, con `Delivery` (leído,
nunca editado) como su esquema, sin presubmit cableado, para que lo único que decida si
la entrega es válida sea el `output_format` del CLI mismo — la comparación de (iv) es
contra eso, no contra un mecanismo que esta subtarea inventó.

**Lo que `schema-guard` construyó sobre esta compuerta.** Un `SchemaGuard` (port propio
en `application/ports.py`, distinto de `Presubmit` y sin ensancharlo) cuya única
implementación, `RoleSchemaGuard`, envuelve `schemas.validate` sobre `role.schema` y el
vocabulario de la corrida: la validez sigue siendo enteramente de `schemas.validate`, el
guard sólo traduce lo que esa llamada ya rechazó a las claves exactas que faltan, más la
línea de contrato ("son claves del JSON, no etiquetas dentro de `markdown`"). Corre en
el mismo `PreToolUse` sobre `StructuredOutput` que la Puerta de entrega, en un único
`HookMatcher` con un único callback compuesto: el guard primero, la puerta después, con
corto circuito en la primera negación — el orden queda escrito en el código
(`hooks._delivery_gate`), no heredado de cómo el SDK combinaría dos matchers propios.
Deniega como mucho una vez por corrida (`domain.schema_guard.REFUSALS`); agotada esa
vez deja pasar la entrega inválida, que termina donde termina hoy: `AttemptFailed
(reason="schema")` desde `_attempt`, con el reintento de `run_with_retry` que ya
entrega. La instancia y su contador se construyen una vez por corrida de rol, en
`run_with_retry` y fuera del loop de intentos, para que el tope sobreviva al reintento;
se cablea sólo para los roles que ejecutan (Developer, el integrador) — la condición
vive en `run_with_retry`, y para todo otro rol `hooks_for` y `options_for` reciben
`None` y quedan byte a byte como antes de este commit.

### Opciones descartadas

- **Asumir por lectura de código que el rechazo era nuestro** (que `schemas.validate`
  corrió trece veces en #32): la traza de `run_once` ya decía que eso era imposible antes
  de correr el spike; la corrida sólo lo confirma con evidencia en vez de con inferencia
  (ADR-0016).
- **Un hook `Stop` para el guard**, por la misma razón que la segunda adenda ya descartó
  para la puerta de entrega: con `output_format` el turno termina en la llamada a
  `StructuredOutput`, y un `Stop` corre después de que la sesión ya decidió terminar.

## Adenda (2026-09-21, sexta): `--fresh` archiva la corrida anterior en vez de construir encima

`agentic run --fresh` (ADR-0003, adenda) borra el thread de la tarea y deja sus ramas y sus
worktrees donde estaban. `GitWorkspace.task_checkout` es idempotente por diseño, así que la
corrida siguiente encuentra `agentic/<tarea>` viva y construye sobre los commits de otro
plan: exactamente lo que la negativa de `run` sobre una tarea con estado existe para
evitar, con el agravante de que `--fresh` la saltea. Para las corridas 3 y 4 de GH-12 el
archivado se hizo a mano, con un script corrido con `!` (`archive/run2-04abd2c/GH-12*`,
`archive/run3-75de807/GH-12`), y el relanzamiento de GH-12 de cero depende de esto.

**`--fresh` archiva antes de borrar el thread.** Un caso de uso nuevo,
`application/archive.py`, con reglas puras de nombre y de número en `domain/rules.py` y dos
verbos tontos más en el port `Workspace` (`branches(prefix)`, `rename_branch`), uno en
`TaskDocuments` (`write_into`) y uno para el chequeo del `.gitignore` del producto. El orden
es el del daño, de menor a mayor: commitear cada worktree de la tarea → sacarlos →
renombrar la rama de tarea → renombrar las de subtarea → borrar el thread → tocar el issue.

**Corre dentro del lock y una sola vez.** Primera sentencia dentro de
`with _locked(lock, task_id)` de `cli._run` y antes de `adelete_thread`, así que está bajo
la misma exclusión mutua que la invocación. El padre de un `--detach` no archiva nada:
retorna antes del `with` y sigue tocando sólo el archivo de lock y `Popen`; archiva el hijo
cuando vuelve a entrar sin `--detach`. Y con `--fresh` el stack **no** se lee en la base de
la tarea: `main()` omite `declared_stack_of`, porque `checkouts.task_base` resuelve por
`base_of(task_branch)` y esa rama está por dejar de existir; la verificación en HEAD que
`preflight` ya hizo es la que rige, que es la base de la que nace el checkout siguiente.

**La rama de tarea viva, sola, decide si hay algo que archivar.** `_checkout` crea la rama
cuando falta, le escribe `agenticBase` y le agrega un worktree: preguntar por el documento
antes que por la rama haría que el archivado **fabricara** `agentic/<tarea>` para archivarla.
Una tarea que escaló en `classify` tiene documento y cero ramas — la rama nace al arrancar
la etapa de definición, no antes— y ahí no hay nada que archivar: el documento lo pisa la
corrida fresca y su historia ya está en git. El paso del issue se parte en dos condiciones
distintas: `unlabel(NEEDS_HUMAN)` corre si el documento dice que la tarea estaba pausada
—reiniciarla la saca de la cola de escalados—, y el comentario `Started over: …` corre sólo
si algo se archivó, porque un comentario sin prefijo que nombrar es ruido.

**Se commitean todos los worktrees, no sólo el de tarea.** `Workspace.cleanup` remueve con
`--force`, que se lleva el árbol sucio sin decir nada, y el caso para el que existe `--fresh`
es una corrida cortada a mitad del desarrollo: ahí cada worktree de subtarea tiene lo que su
Developer no llegó a commitear, que es la única parte del historial que git todavía no
tiene. `commit_all` devuelve `None` sobre un árbol limpio, así que un worktree limpio no
deja commit vacío. El documento de tarea se copia al worktree de tarea con `write_into` y se
commitea ahí, con el mismo verbo que ya usan `plan` y `define`: nada de plumbing sobre un
índice temporal para commitear en una rama que nadie tiene checkouteada. Descartado dejar los
worktrees puestos: `--fresh` existe para que la corrida siguiente arranque limpia, y un
worktree viejo apunta a una rama renombrada.

**El renombre de la rama de tarea va primero, y eso es lo que hace decidible un reintento.**
`subtask_checkout` pide `task_checkout` antes que nada, así que ninguna corrida puede producir
`agentic/<tarea>` ausente con `agentic/<tarea>.<sub>` viva: ese estado sólo lo deja un
archivado que se cortó. La regla, pura sobre `(refs, ramas vivas, task_id)`:

- rama de tarea viva ⇒ archivado nuevo, `N = max(existentes) + 1`;
- rama de tarea ausente y alguna de subtarea viva ⇒ archivado sin terminar: se reusa el `N`
  más alto que esa tarea ya tiene y se saltean el documento y los worktrees, que ya están
  hechos y cuya recreación acuñaría la rama;
- ninguna de las dos ⇒ no hay nada que archivar.

`system_commit` no decide nada: sale del frontmatter, es parte del nombre y nada más, y
`unknown` repetido entre corridas es inofensivo. Descartada la regla que lo usaba como
discriminador de reintento: no es único.

**Ningún id de tarea se compara por prefijo.** `branches(prefix)` es un verbo sobre cadenas
y `branches("agentic/GH-1")` devuelve también `agentic/GH-12`; este repo va por GH-88, así
que el par existe hoy. Sin filtrar, un `--fresh` sobre GH-1 renombraría la rama de una tarea
ajena y viva —el lock es por tarea y no protege nada ahí— y leería un archivado sin terminar
donde no lo hay. Todo filtrado por id pasa por `rules.branches_of_task(names, task_id)`, que
compara segmentos completos: después del id viene fin de cadena o el punto que
`subtask_branch` define.

**El documento no puede impedir el archivado, y el archivado no puede mentir sobre él.**
`documents.load` pasa por `document.parse`, que levanta `ValueError` sin frontmatter; un
documento ilegible deja `system_commit = "unknown"` y el archivado sigue, porque `--fresh` es
el comando al que se recurre cuando la tarea quedó fea y negarse por el archivo que describe
esa fealdad devuelve al humano al script con `!`. Del otro lado, `commit_all` es `git add -A`
y respeta el `.gitignore` del producto: que `.agentic/tasks/` esté versionado es cierto acá
(ADR-0017) y en gym-saas, pero no es una garantía del sistema. Se verifica que el documento
quedó adentro y, cuando el producto lo ignora, se archiva igual y se dice, en la línea que la
CLI imprime y en el comentario del issue.

**Todo queda escrito, porque no hay dónde confirmar.** `archive_previous_run` devuelve
`prefix`, `written` y `document_kept`, no una cadena o `None`: "lo archivé ahora" y "ya estaba
archivado" tienen cosas distintas que decir y sólo `prefix is None` significa que no había
nada. `_run` imprime una línea antes de `_invoke` en los dos casos y el comentario en el issue
se postea en los dos; un comentario duplicado es barato y un archivado mudo no se puede
rastrear. No hay confirmación interactiva: `--detach` no tiene TTY.

**El prefijo del comentario se registra como comentario del sistema.** `resume.human_decision`
toma como texto del humano todo comentario que no arranque con uno de `SYSTEM_COMMENTS`, y ese
texto termina en la sección `## Human` que todo rol lee sin declararla (ADR-0003). Un
`Started over: …` sin registrar haría que el sistema se cite a sí mismo como decisión del
humano en la reanudación siguiente. `STARTED_OVER` vive en `application/archive.py`, junto al
código que lo postea —la forma que `escalate.ESCALATED`, `deliver.READY` y `discard.DISCARDED`
ya tienen— y `resume.SYSTEM_COMMENTS` lo importa en el mismo diff. La flecha va en un solo
sentido: `archive.py` no importa `resume.py`, lee el frontmatter crudo y toma `NEEDS_HUMAN` de
`escalate`.

**Un fallo de git corta y no borra el thread.** Cualquier `GitError` —un worktree que no se
puede remover, un renombre con las dos puntas presentes— sube como `CannotStart`, la salida
que la CLI ya tiene: no se intenta ningún renombre después, el thread queda, y el archivado es
idempotente por construcción (destino presente y origen ausente es no-op), así que el reintento
lo termina. Un worktree que no se puede sacar significa que otra cosa está usando esta tarea, y
eso es del humano. El sistema nunca renombra una rama todavía checkouteada en un worktree.

**Lo que esta adenda no cambia.** `--fresh` sin ramas sigue igual, `run` sin `--fresh` sigue
negándose con el mismo texto, `resume` no cambia de comportamiento, el documento se renderiza y
se parsea igual, y ningún prompt de `domain/prompts/` se toca: `prompts.fingerprint` no se
mueve.

### Riesgos conocidos

- Un worktree de subtarea con un merge de dependencia en conflicto (ADR-0004, novena adenda)
  se commitea con sus marcadores adentro: es lo que la corrida dejó, y es lo que se archiva,
  pero quien lea esa rama después no distingue un merge resuelto de uno archivado en conflicto.
- El archivado no deja métrica. Una corrida reiniciada es invisible en Grafana salvo por lo que
  gastó la corrida nueva.
- La regla de tres casos no distingue un archivado sin terminar de una rama de tarea que alguien
  borró a mano con sus ramas de subtarea vivas: en ese caso reusaría un `N` y saltearía el paso
  del documento. Se acepta: no hay forma de distinguirlos sin guardar estado que sobreviva al
  thread que `--fresh` borra.

## Adenda (2026-09-22, séptima): la jaula de supresiones la declara el producto

La cuarta adenda dejó dicho que los constantes de la jaula del rol que ejecuta (`CONFIG`,
`PUSH`, `WORKTREE`, `SILENCED`…) son forma vieja y que unificarlos es su propio platform
enhancement. `SILENCED` deja de ser uno de ellos: no se unifica, se va del sistema entero
(issue #38). `_SILENCED` (el regex de `# type: ignore`/`# noqa`) y `_PYTHON` (`.py`, `.pyi`)
eran el único lugar del sistema que sabía que el producto estaba en Python; en TypeScript, Go
o Kotlin un `@ts-ignore`, un `eslint-disable` o un `//nolint` pasaban sin que la jaula del
estándar 6 los viera. Lo que cubre `Edit|Write` hoy: los caminos dentro de `.git/` o fuera del
checkout, como siempre, y una ocurrencia nueva de una supresión que el producto declaró bajo
`forbidden` en `.agentic/stack.yaml` — ninguna. Un producto que no declara `forbidden` tiene
la jaula instalada y sin nada que negar, la misma rama de no-op que `presubmit`/`guard`
ausentes ya tienen; nunca falla cerrado ni abierto sobre una declaración rota, porque una
declaración rota nunca pasa de `stack.forbidden`, que la rechaza con `ValueError` al cargar
(`core.knowledge.domain.stack`).

**Dos mecánicas, decididas en esta adenda y no antes.** `forbidden` es una lista en el orden
del archivo, y `core.process.domain.suppressions.denial` prueba sus reglas en ese orden: la
primera cuyo `pattern` aplica a la extensión del archivo y cuyas ocurrencias en `after`
superan a las de `before` gana, y ninguna otra se mira. Con eso el motivo que un Developer lee
es determinístico incluso si dos reglas del producto se solapan, en vez de depender de cuál
haya corrido última. Y la negación es el prefijo del sistema (`_DENIED`, "Denied by the
system: ") seguido del `reason` del producto **verbatim, sin una palabra más del sistema**:
`SILENCED` hoy dice "narrow it with `cast`", instrucción que el sistema ya no tiene autoridad
para dar en un producto cuyo lenguaje no conoce.

**El `pattern` de este repo se preserva byte a byte; el `reason` diverge, y es a propósito.**
`.agentic/stack.yaml` declara `forbidden` con el mismo regex y la misma lista de extensiones
que `_SILENCED`/`_PYTHON` tenían: la jaula sobre este producto queda demostrablemente sin
cambios por la mudanza, y relajarla ensancharía lo que un Developer puede escribir acá, que
no es lo que esta tarea es. El `reason`, en cambio, no se copia: la última frase de `SILENCED`
le decía al Developer que angostara con `cast`, y ADR-0021 prohíbe `cast` en este repo sin
excepción; copiado tal cual le entregaría al Developer un círculo cerrado justo cuando trata
de dejar mypy en verde. La forma que ADR-0021 sí sanciona (`# type: ignore[<código>]`) sigue
cayendo bajo el mismo `pattern` preservado — angostar el regex para dejarla pasar es una
decisión sobre el dial de ADR-0021, no de esta tarea. El texto nuevo nombra el estado real
(esto agrega una supresión de tipo o de lint) y apunta a arreglar el tipo o sacar la unidad de
`[tool.mypy] files` y decirlo en el pull request. Quien lea el diff y vea `pattern` igual y
`reason` distinto no debe restaurar la copia: son dos decisiones separadas, cada una con su
motivo propio, no un descuido.

### Riesgos conocidos

- **La declaración de este repo no rige en esta tarea.** El stack se lee en la base
  (`core.knowledge.domain.stack`, ADR-0024), así que la declaración nueva de `forbidden` entra
  en vigor recién en la tarea siguiente al merge. Los tres Developers de esta misma tarea
  corren con la jaula de `Edit|Write` instalada y vacía mientras `checks.types: uv run mypy`
  ya está declarado en la base y corre en cada entrega: la presión de mypy no encuentra jaula
  que la acompañe. Se cierra de las dos formas que funcionan sobre un solo diff: queda escrito
  acá, y un invariante del plan hace de "ningún `# type: ignore` o `# noqa` nuevo en
  `agentic/` o `tests/`" un grep sobre el diff en vez del juicio de un revisor sobre el
  estándar 6.

## Adenda (2026-10-04, octava): un rol que ejecuta no manda señales a procesos ajenos

En la corrida de apostrophe #32 un Developer corrió `pkill -f uvicorn` para liberar el
puerto antes de levantar su propio e2e: mató, dos veces, la app local que el humano corre
con `scripts/serve.sh` en `:8000` (issue #151). `pkill`, `killall`, `fuser -k` y un `kill`
sin verificar a quién pertenece el pid alcanzan cualquier proceso del usuario, no solo el
que el rol levantó; con corridas en paralelo, la misma línea puede tirar el servidor de
e2e de otra corrida en medio de sus propios checks.

**La propiedad de un proceso se decide solo con `/proc/<pid>/cwd`.** Nunca un patrón, un
nombre de programa o un puerto: eso es lo que `pkill -f uvicorn` usaba, y es exactamente
lo que tumbó una app que no era del rol. `core.process.domain.signals.denial` es la
función pura que decide esto -- `owner: Callable[[int], Path | None]` inyectado, nunca
`/proc` leído por el dominio -- y compara con `Path.is_relative_to` por componentes, así
que un worktree hermano con prefijo de nombre (`GH-32` y `GH-32.new-sale`) no cuenta como
propio. Un cwd ilegible, inexistente, `(deleted)` o cualquier excepción al resolverlo
cuenta como ajeno: `hooks._owner`, el único lugar que lee `/proc` de verdad, falla
cerrado ante cualquiera de los cuatro.

**Se niega siempre, sea cual sea la forma que lo envuelve:** `pkill`, `killall`, `fuser`
con `k` en sus opciones cortas o con `--kill`, y `kill-port` -- solo o detrás de `npx`,
`npm exec`, `pnpm dlx` o `yarn dlx`. Un `kill` que llega detrás de `xargs` se niega
siempre también, cualesquiera sean las opciones de `xargs`: el pid ahí es un dato que
viene de un pipe, no uno que el rol conoce de antemano. `signals.command_of` pela, en un
solo bucle junto con las asignaciones `NAME=valor`, los envoltorios que no cambian de qué
proceso se trata (`xargs`, `env`, `sudo`, `timeout`, `setsid`, `nice`, `builtin`,
`command`, `exec`, `nohup`, `time`); una opción de un envoltorio que el módulo no conoce
se lee como una sin valor y el pelado sigue, nunca corta el análisis.

**La gramática de `kill` es una lista blanca, no una lista negra.** `-l`/`-L` en primera
posición no manda señal y pasa; si no, se consume a lo sumo una especificación de señal
(`-9`, `-TERM`, `-s TERM`, `-n 9`, `--signal TERM`, `--signal=TERM`), después un `--`
opcional, después los operandos. Un operando pasa solo si es `^[0-9]+$` y `owner` lo ubica
dentro del checkout, o si calza exactamente `^\$(!|[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_]
[A-Za-z0-9_]*\})$` (`$!`, `$NOMBRE`, `${NOMBRE}`): el puñado de formas que siempre nombran
el último trabajo del rol o una variable que el rol mismo asignó, nunca el proceso de
otro. Todo lo demás se niega, incluido `-<dígitos>` (un grupo de proceso), `0`, `%n`, un
operando con `$(...)` o con comillas invertidas, y texto suelto. Una opción de señal sin
su valor, o un `kill` que al final no tiene ningún operando, es por sí mismo una
denegación: no es un problema de sintaxis distinto del resto, es la misma regla.

**El checkout se resuelve una sola vez, en el adaptador.** `hooks._bash` calcula
`cwd.resolve()` al construir el callback, no en cada llamada, y ese valor viaja a través
de las llamadas recursivas que ya existían para `sh -c`/`bash -lc`. `git_denial` -- mismo
nombre, misma firma -- vuelve a resolver su propio `cwd` igual, porque un llamador
directo (un test, un checkout pasado por symlink) no tiene por qué haberlo resuelto antes;
resolver dos veces es barato e idempotente. `_denial_of` gana una sola línea nueva, antes
de su propio `while`: `signals.denial(words, cwd, _owner)` con las palabras crudas, sin
pelar; si eso niega, es lo único que se devuelve. El resto de `_denial_of`, `_git`,
`_WRAPPERS` y `_SHELLS` no cambia ni una línea.

**Código nuevo junto al viejo, sin forzar una migración.** `signals.py` nace tipado,
dentro de `[tool.mypy] files` (el directorio `core/process/domain` ya lo estaba); el
parser de `_git` y sus constantes siguen fuera de mypy, como siempre, porque no es esta
tarea la que decide mover esa unidad del dial de ADR-0021.

### Riesgos conocidos

- `PID=$(...); kill $PID` pasa: el pid literal que `kill` recibe es un pid común, y
  `owner` lo verifica igual que a cualquier otro -- la línea completa nunca pasa por este
  veredicto, solo el `kill $PID` final, que ya no tiene el `$(...)` adentro.
- `find ... -exec kill {} \;`, `parallel kill`, los intérpretes (`python -c "os.kill(...)"`)
  y las opciones largas de un envoltorio con valor separado que la tabla de
  `signals.py` no nombra quedan afuera: ninguno es la forma que #32 usó, y cada uno abre
  su propio costurero de parsing que esta tarea no resuelve.
- Un `pkill` escrito en un archivo que corren el presubmit o el producto no lo ve esta
  jaula, que solo mira la llamada a Bash: la salida es un `forbidden` del producto
  (`.agentic/stack.yaml`), que se lee en la base y rige recién en la tarea siguiente al
  merge, y solo cuenta ocurrencias nuevas. En apostrophe, declararlo contra `pkill` y
  `killall` es del humano.
- Un pid puede reusarse entre el momento en que el hook lo verifica y el momento en que
  `kill` corre de verdad: una ventana que existe en cualquier sistema que decide por pid,
  no una que esta tarea cierra.
- Sin `/proc` (una plataforma que no es Linux), `owner` falla cerrado: todo pid lee como
  ajeno, así que todo `kill` queda negado, incluido el de un pid propio.
- La negación no queda escrita en el documento de la tarea, igual que la de git: el
  rastro vive en lo que el modelo ve en su propio turno, no en un artefacto que el
  revisor pueda releer después.
- #32 sigue sin salida dentro del sistema: si el puerto lo ocupa un proceso ajeno, la
  única vía que esta adenda deja es levantar en otro puerto o entregar nombrando la
  brecha en `summary` -- no hay forma de liberar el puerto sin matar algo que no es del
  rol.

Para el cuerpo del pull request: el seguimiento (contar las negaciones de esta jaula por
rol que ejecuta, igual que ya se cuenta la de git) y la acción del humano en apostrophe
(declarar `forbidden` contra `pkill` y `killall`) quedan pendientes, fuera de esta tarea.

## Adenda 2026-10-06: el cuerpo del pull request tiene tope y la entrega que falla escala (GH-203)

apostrophe GH-47 terminó la integración, empujó su rama y murió en `deliver`: GitHub
rechaza un cuerpo de más de 65.536 caracteres y la tarea quedó en `developing`, sin pull
request y sin escalado.

- El cuerpo tiene tope en 60.000 unidades UTF-16 (`pull_request.PR_BODY_MAX`; es la unidad
  que cuenta GitHub, un emoji vale dos), con margen para el `Closes #N`. Un cuerpo que
  cabe sale byte por byte como siempre. Si no cabe, solo se descartan secciones enteras
  (la primera, la del PM, puede recortarse en una línea en blanco fuera de un bloque de
  código) y una sección `Task document` apunta al documento de tarea, que las tiene
  completas. El cuerpo viaja con `--body-file`.
- El puntero nombra un documento que existe en la rama: solo cuando el cuerpo tiene tope,
  `deliver` guarda el documento con el estado que recibió, lo escribe en el worktree y
  commitea esa única ruta (`Workspace.commit_paths`, nunca `commit_all`). Es una
  instantánea: su estado y sus campos posteriores quedan en la copia del checkout del
  producto. Si el producto ignora la ruta, el puntero lo dice.
- Un fallo del commit del documento, del `push` o de `gh pr create` es un escalado de
  clase `failure` cuyo motivo nombra el paso. No lo retoma el poller
  (`rules.auto_resumable` exige una entrada `failure` en el `pause_log`, y este escalado
  solo escribe la de `escalation`): lo resuelve el humano. `resume_target` no cambia: la
  reanudación pasa por `integrate` sin costo (los merges ya están hechos, no se despacha
  ningún rol) y llega a `deliver`.
- Una vez que `gh` devolvió la URL, nada en `deliver` lanza: el comentario del issue y
  `cleanup` son de mejor esfuerzo y el parche con `pull_request` se arma antes.

Riesgos que quedan: el error de un comentario o un `cleanup` que falló después del PR se
traga sin registro; un proceso muerto entre `gh pr create` y el checkpoint termina en un
escalado visible por "already exists" (encontrar el PR existente sigue diferido, adenda de
ADR-0039); la instantánea mergeada conserva un estado viejo, y quién versiona el documento
final no se resuelve acá; un `OSError` de `documents.save` o `write_into` sigue sin
atraparse.

## Adenda 2026-10-06: la entrega con una rama remota que divergió (GH-204)

apostrophe GH-59: el PR #95 de la primera corrida se cerró sin mergear y la tarea se relanzó
con `--fresh`. La corrida nueva integró nueve horas de trabajo sobre un `agentic/GH-59` local
nuevo, pero `origin/agentic/GH-59` seguía en la historia del PR cerrado, y `git push` murió
por non-fast-forward. Esta adenda agrega la decisión que corre antes del push; el escalado de
un push que falla ya existía (GH-203) y se reutiliza.

**La tabla.** `rules.push_plan` es una regla pura sobre hechos y responde `push`, `replace` o
`refuse`:

| Remoto `agentic/<tarea>` | PRs con esa rama de cabeza | Plan |
|---|---|---|
| ausente | no se consultan | `push` |
| ancestro de HEAD o igual | no se consultan | `push` |
| divergió | alguno OPEN | `refuse` `open_pull_request` |
| divergió | alguno MERGED (ninguno OPEN) | `refuse` `merged_pull_request` |
| divergió | uno o más, todos CLOSED sin mergear | `replace` (sha viejo) |
| divergió | ninguno, y la punta está en un archivo de esta tarea | `replace` (sha viejo) |
| divergió | ninguno, y no está en un archivo | `refuse` `unowned_divergence` |

Un PR abierto o mergeado prohíbe el reemplazo en todos los casos.

**El predicado de archivo estrecha, a propósito, el "o la corrida es `--fresh`" del pedido.**
Nada registra "esta corrida es fresca"; lo que `--fresh` significa de verdad es "el remoto
tiene historia que el propio sistema archivó", y eso es un hecho de git:
`is_ancestor(punta_remota, archivo)` sobre `rules.archived_task_branches`, que devuelve solo los
archivos de la rama de tarea (ambas grafías, cualquier `run<N>-<sha>` incluido `unknown`; nunca
el de una subtarea ni el de otra tarea, `GH-1` contra `GH-12`). Es más estricto que el pedido:
un remoto al que alguien empujó después del archivo se rechaza, no se destruye.

**Orden fijo en `deliver`.** (1) `remote_tip` es la única lectura remota (`ls-remote` decide si
existe, luego `fetch` a una ref privada y `rev-parse` debe dar el mismo sha, o `GitError`); un
fallo nunca se lee como "ausente". (2) Lecturas locales: `is_ancestor(punta, HEAD)` y, solo si
es falso, los archivos. (3) `pull_requests_on`, solo si divergió. (4) `push_plan`. (5) Solo
trabajo local: el registro, el cuerpo, el commit del documento y el guardado. (6) `push` con el
lease y `gh pr create`. Entre (3) y (6) no corre nada remoto, y el commit del documento agrega un
commit encima de HEAD, así que "la punta remota es ancestro de HEAD" responde igual antes y
después: el plan sigue vigente. Ese único sha es el insumo del plan, el lease, el
`replaced_remote_tip` guardado y el que nombran el cuerpo y el comentario.

**`replaced_remote_tip`.** Clave opcional del documento de tarea, escrita solo cuando existe
(un documento sin reemplazo sale byte por byte como antes) y leída con `.get`. Se guarda antes
del push con lease. `rules.replace_record`: un plan `replace` la fija; un plan `push` la
conserva solo si el remoto observado no contiene el sha registrado (el reemplazo ya aterrizó y
falló un paso posterior); todo lo demás la limpia, incluido un lease perdido (el remoto sigue
siendo el sha viejo) y cualquier `refuse`. Todo escalado posterior al reemplazo nombra el sha.

**El prefacio del cuerpo del PR es el registro durable.** `bounded_body(..., preface=...)`
antepone la nota, la cuenta contra `PR_BODY_MAX` y nunca la recorta: las secciones ceden
primero. Con `""` el cuerpo es idéntico al de antes. El comentario `Ready for review:` (de mejor
esfuerzo) repite la línea; si el comentario falla, el cuerpo igual nombra el sha.

**`reflect_run` no pasa por `push_plan`.** Su lease incondicional sobre `agentic/playbook` es de
una rama que el sistema posee (ADR-0043); no es una decisión de `push_plan`.

**§8: qué se amplía y para quién.** El sistema puede ahora hacer force-push con lease. Solo lo
hace `deliver`, solo a `agentic/<tarea>`, solo cuando `push_plan` dice `replace` y solo contra el
sha que observó; `GitWorkspace.push` nunca fuerza sin un `lease` explícito y nunca relee ese sha.
El sha viejo queda recuperable desde el documento, el cuerpo del PR, el comentario y, en el caso
de archivo, la rama archivada local. Costo: un `ls-remote` y un `fetch` de una rama por entrega;
`gh pr list` y el recorrido de archivos solo corren con divergencia, y nada escala con el tamaño
de los datos.

**Un solo camino de escalado.** Una negativa, una consulta de PRs fallida, una lectura remota
fallida o un lease perdido escalan por el `_undelivered` / `escalation("failure", ...)` de GH-203;
no se agrega un segundo camino.

Riesgos que quedan: si alguien borra a mano los archivos de una tarea, un reemplazo legítimo por
archivo pasa a ser un rechazo (falla seguro, termina en escalado); `gh pr list --head` también
trae un PR de fork con el mismo nombre de cabeza, lo que solo puede agregar un OPEN o MERGED y
rechazar; los commits de una persona sobre la rama de un PR cerrado se reemplazan, como pide el
pedido, y el lease solo cubre un push dentro de la ventana; si alguien resetea el remoto a mano a
un sha no relacionado, la reanudación limpia la clave tras un lease perdido (correcto), y si lo
hace tras un reemplazo aterrizado, el plan es `refuse unowned_divergence`.

## Addendum (GH-406): restaurar es el inverso del archivado, por copia

`agentic restore --issue N --from archive/run<N>-<commit>` trae de vuelta una tarea archivada que
estaba entregada y aprobada. Es el inverso de `archive_previous_run`, pero **copia**: crea las
ramas vivas con `Workspace.create_branch` (nunca fuerza, no hace nada si el ref ya está en ese
sha, falla si apunta a otro) y deja intactos los refs del archivo; `next_archive_run` no cambia.
La tarea se crea solo en `rules.approved_head(state)` y cada subtarea solo en su `reviewed_head`,
cada uno comprobado como ancestro de su rama archivada: lo que el archivo hizo por su cuenta se
queda bajo el prefijo.

Orden, bajo el `RunLock` (comando `restore`; un lock tomado es `task_live`), cada paso omitible en
un reintento porque `rules.restore_steps` decide qué falta mirando solo el estado observado:

1. Pre-chequeos de solo lectura (los rechazos de abajo).
2. `archive_live`: si hay una rama viva de la tarea en otro commit, se archiva primero.
3. `create_refs`: las ramas en los shas registrados.
4. `write_document`: el documento archivado (o su copia viva, que puede tener más entradas de
   `landing`, ninguna se pierde) más el registro de `restorations`; `landing_restarts` toma el
   máximo y `landing_cost_carried` suma el costo de la corrida desplazada, si la hubo.
5. `push` del sha registrado solo si el remoto no tiene la rama; cualquier fallo es `push_refused`,
   sin reintentos.
6. `reopen` del pull request (`Tracker.reopen_pull_request`; su `ValueError` se lee como
   `pr_merged` o `pr_base_gone`).
7. `settle`: las filas de `PM: final validation` por `plan_settlement.pending`, permitido por
   `plan_settlement.settleable(facts, restored=True)`.

Rechazos, un conjunto cerrado: `task_live`, `no_archive`, `no_document`, `not_delivered`,
`pr_merged`, `pr_base_gone`, `head_disagrees`, `head_not_reachable`, `remote_diverged`,
`pr_head_moved`, `push_refused`, `already_restored`. No toca etiquetas ni el estado del issue.
Una restauración por tarea; el mismo prefijo en un reintento termina lo que falta.

