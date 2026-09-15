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
