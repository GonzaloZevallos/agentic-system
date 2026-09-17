
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
modelo. Va en el fan-in y no adentro de una rama del fan-out a propósito: una rama nunca
rutea al escalado, así que ahí el fallo no tendría a quién contárselo.

Descartado: **negar `pip install` / `uv pip` / `uv venv` con los hooks**. Prohibiría el
caso legítimo del punto anterior, y la garantía la da el entorno preparado, no la regla,
igual que con la identidad de git en la primera adenda.
