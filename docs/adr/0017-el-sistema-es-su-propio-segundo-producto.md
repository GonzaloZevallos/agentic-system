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
