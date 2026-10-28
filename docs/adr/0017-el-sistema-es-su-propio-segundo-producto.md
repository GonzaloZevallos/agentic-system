---
status: accepted
date: 2026-09-16
---

# El sistema es su propio segundo producto, con bootstrap de compilador y auto-hospedado

ADR-0002 dice literal que "el repo del sistema no cambia durante una corrida". Desarrollar
agentic-system con agentic-system lo viola de frente, y no hay forma de arreglarlo con una
excepción: o se separa la copia que **corre** de la copia que se **modifica**, o la regla se
cae. Esa separación es la decisión, y de ella cuelgan #44, #45, #46, #47 y la primera
corrida recursiva de #48.

**El sistema es un segundo producto, no reemplaza al gimnasio.** El SaaS de gimnasios sigue
siendo el producto de prueba y la base del set de evaluación (README §9), porque tiene
métricas de negocio discutibles de verdad (churn, asistencia promedio, recupero de
inactivos) y el sistema no las tiene. La regla "no está terminada si no sale medida" sólo se
puede probar donde haya algo que medir que no sea el propio proceso. Como beneficio, el
segundo producto es justo lo que README §8 dice que dispara extraer el adaptador: hasta hoy
la costura se generalizó sobre un solo ejemplo, que es lo que ADR-0002 se propuso no hacer.

**Bootstrap de compilador: la corrida la ejecuta un checkout aparte, fijado a un sha.** Es
el `stage0` del glosario de Proceso (ADR-0018, issue #51), con su propio entorno. El código
que corre no es el que se está editando, y por eso una tarea puede reescribir el sistema
mientras el sistema la atiende. El detalle y la implementación viven en #44.

**Auto-hospedado: los artefactos del producto viven en este mismo repo.** `.agentic/tasks/`,
`.agentic/state.sqlite`, `.agentic/worktrees/`, la rama `agentic/<tarea>` y el pull request
quedan acá, no en un clon aparte. Un clon no agrega aislamiento real una vez que el runtime
está fijado (el aislamiento lo da `stage0`, no la distancia en el disco) y sí duplica el
estado, que es la forma más barata de tener dos verdades.

**Una tarea sobre el sistema entra siempre como platform enhancement** (#46): sin PM, sin
flags, sin métricas de negocio. El "usuario" del sistema es el humano que escribe el issue,
y el PRD es el issue. Forzar el camino de feature acá produciría un PM inventando jobs para
un usuario que está sentado escribiendo el ticket.

## La regla de ADR-0002, reformulada

Pasa a ser **el sistema nunca escribe en el checkout desde el que corre**. El motivo
original se sostiene entero: que la historia de un producto no ensucie el repo del sistema,
y que dos productos no se mezclen. Acá el producto *es* el sistema, y su historia *es* la
del repo, así que lo que había que proteger no era el repo sino el código en ejecución. La
adenda está en ADR-0002.

## Qué recibe un rol que trabaja sobre el sistema (issue #45)

El sistema espera del producto una forma fija (README §8) y este repo no la tenía. Lo que se
decidió, con lo que quedó en default **dicho y no omitido**:

- **`.agentic/stack.yaml`**: los tres comandos que corre CI, no una aproximación (`uv run
  pytest -m "not llm"`, `uv run ruff check .`, `uv run ruff format --check .`). `uv sync
  --locked` no entra: prepara el entorno en vez de juzgar el código, y el stack todavía no
  tiene clave para eso (#37, #38).
- **Glosario**: el adaptador aprende a seguir `CONTEXT-MAP.md`, en vez de que este repo
  agregue un `CONTEXT.md` de raíz. Un glosario de raíz sería un duplicado de 4.600 palabras
  mantenido a mano y sin dueño, y el adaptador sirve a cualquier producto multi-módulo. El
  mapa va primero en lo que el rol recibe: su sección *Relationships* dice qué contexto
  conoce a cuál, y eso no está en ningún `CONTEXT.md`.
- **Un término pertenece a un contexto, no a un archivo.** `GlossaryTerm` suma `context`, y
  una destilación escribe en tantos glosarios como módulos tocó la tarea. Una tarea que
  cambia comportamiento entre módulos deja términos en cada uno, nunca todos en uno solo; sin
  el campo, el sistema tendría que elegir un archivo por su cuenta y se equivocaría en
  silencio. El mapa nombra sus contextos y ése es el nombre que PM usa (el del directorio
  también resuelve). Un contexto que el mapa no conoce, o un término sin contexto en un
  producto mapeado, son errores **del chequeo de la salida de PM**, no de la escritura:
  `run_role` convierte un `ValueError` del chequeo en un intento fallido y reintenta con el
  motivo como entrada, mientras que fallar al escribir mataría la corrida después de haber
  pagado a PM. Y el mapa no puede enlazar fuera del producto: vive en el checkout que un rol
  edita, así que un enlace relativo sería una forma de escribir fuera de su propio worktree.
- **Una reescritura conserva las líneas que no le pertenecen.** Quien escribe una definición
  no es dueño de toda la entrada: `_Avoid_` sí lo es, `_Campo_` (ADR-0018) y lo que un
  producto agregue no. Sin esto, la primera vez que PM tocara un término de este repo le
  borraría su línea `_Campo_` sin decir nada.
- **`docs/product/` queda vacío.** El README son 10.800 palabras de estado de diseño para
  humanos, y lo que un Developer necesita para tocar código está en `roles.py` y `rules.py`,
  que puede leer con sus tools. Además el camino de platform enhancement no tiene PM, así que
  lo que se escribiera ahí no tendría quién lo destile y envejecería sin dueño desde el día
  uno. Si la primera corrida (#48) se estrella por falta de contexto, ese es el hallazgo que
  decide entre escribir capacidades a mano o destilar el README, con evidencia.
- **`CODING_STANDARDS.md`: el default del sistema** (ADR-0012). Describe bien lo que este
  repo ya hace, y una copia propia serían 53 líneas duplicadas para cambiar cero.
- **`.agentic/flags.yaml`: el default.** Platform enhancement no usa flags.

**El número que hay que mirar en la primera corrida**: con todo esto, un rol de producto
recibe unas **24 mil palabras**, unos 31 mil tokens, en cada llamada, y el número crece con
cada ADR. Casi todo son los 19 ADRs, que `read()` manda enteros y siempre. No se toca ahora, porque elegir qué ADR importa para
qué tarea es generalizar sobre cero ejemplos, pero es lo que más probablemente haga cara la
corrida recursiva y conviene medirlo antes de culpar a otra cosa.

## Opciones descartadas

- **Un clon aparte del sistema como "el producto"**: duplica el estado y el aislamiento ya
  lo da el checkout fijado. Dos copias del mismo repo con dos `.agentic/` distintos es una
  fuente de confusión permanente a cambio de nada.
- **Reemplazar el gimnasio por el sistema**: perdería las métricas de negocio, que es lo
  único con lo que se puede probar la regla de la medición, y dejaría la costura otra vez
  generalizada sobre un solo ejemplo.
- **Correr sobre el mismo checkout con cuidado**: es la opción que ADR-0002 prohíbe, y el
  "cuidado" no sobrevive a un Developer que edita `rules.py` mientras `rules.py` decide su
  ruteo.
- **Una tarea del sistema como feature**: PM no tiene qué definir cuando el usuario es quien
  escribió el issue, y las métricas de producto no existen.
- **Un repo de tareas aparte, sólo para `.agentic/tasks/`**: rompe ADR-0001, que quiere el
  documento de tarea junto al código que describe.

## Consecuencias

- ADR-0002 lleva la adenda con la regla nueva; README §13 anota la reemplazada.
- README §8 pasa a dos productos y el sistema entra con su forma de repo.
- `.gitignore`: `.agentic/state.sqlite` y `.agentic/worktrees/` quedan fuera del control de
  versiones (ADR-0003, ADR-0011), y `.agentic/tasks/` **sí** se commitea, porque es el
  registro crudo del trabajo (ADR-0001). La línea `tasks/` que ya estaba pasa a `/tasks/`:
  sin la barra inicial también ignoraba `.agentic/tasks/`, que es otra cosa, y los
  documentos de la primera corrida recursiva se habrían perdido sin un mensaje de error.
- CLAUDE.md dice la regla nueva y nombra a `stage0`.
- El adaptador de README §8 se puede extraer ahora: hay dos productos.

**Adenda (2026-10-28):** el párrafo de `.gitignore` de arriba (`.agentic/tasks/` sí se commitea) lo supersede [ADR-0077](0077-el-documento-de-tarea-nunca-se-versiona-en-una-rama-de-entrega.md).
