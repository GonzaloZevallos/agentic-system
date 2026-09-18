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
