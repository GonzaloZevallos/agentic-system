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
todas las reales están aprobadas y hay más de una.

**Una rama de tarea, `agentic/<tarea>`, creada al arrancar la etapa de definición.** No
al empezar a desarrollar (ni en la clasificación, que puede escalar antes de tocar nada): la entrevista de definición (ADR-0014) ya escribe glosario y ADRs, y eso
tiene que viajar en la misma rama que el código. El Developer integrador une ahí las ramas
de subtarea y corre la suite completa. Con una sola subtarea no hay integración: su rama
es la rama de tarea.

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
  (`git switch`, `git checkout` sin `--`). El comando se parte con `shlex`, así que cuenta
  después de `;`, `&&`, `|` o un salto de línea, dentro de `$(...)`, en `sh -c '...'` y
  detrás de `VAR=valor`. El cuerpo de un heredoc no cuenta: un mensaje de commit que
  menciona `git push` pasa.
- **Edit y Write**: rutas dentro de `.git/` o fuera del checkout, y un `# type: ignore` o
  `# noqa` nuevo en un archivo Python. Uno que ya estaba no es de la tarea: el estándar
  aplica al código nuevo.

Qué no cubre, a propósito: un shell que se mueve primero (`cd ..; echo > archivo`),
archivos escritos por Bash (`sed -i`, `cat >`), comandos armados en tiempo de ejecución
(`$VAR`, `eval`, alias, `$(...)` entre comillas dobles), symlinks que salen del checkout y
una línea que `shlex` no parsea, que pasa. Las reglas de denegación siguen puestas, y la
firma la sigue garantizando el entorno. La jaula sube la barrera sobre el prompt; no es un
sandbox.

**La entrega pasa por una puerta antes de contar.** Cuando el Developer o el integrador
entregan, el sistema mira su checkout: que haya commit, que el árbol esté limpio y que los
chequeos del producto pasen (ADR-0016, adenda). Si algo falta, rechaza la entrega con el
motivo, el comando y las últimas líneas, y el modelo lo recibe dentro del mismo turno y
vuelve a entregar. Rechaza como mucho dos veces por corrida de rol; después deja pasar, y
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
