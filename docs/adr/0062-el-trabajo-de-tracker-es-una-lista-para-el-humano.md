---
status: accepted
date: 2026-10-24
---

# El trabajo de tracker es una lista para el humano, no una escalada

GH-336. En apostrophe GH-307, tras aterrizar las rebanadas, quedaba solo trabajo del
tracker (postear una tabla, cerrar o retitular seguimientos). La corrida escaló dos veces:
PjM pidió `gh` porque ningún rol lo tiene, y el Tech Lead falló dos veces porque un plan sin
archivos se rechazaba (`estimated_files must be at least one file`). Costó 6,50 USD y el
humano cerró el issue a mano.

## Qué se decide

1. **Ningún rol recibe `gh`.** Es la regla de ADR-0001; este caso no la cambia.
2. **El código del sistema toca el tracker solo por el puerto `Tracker`.** Esta tarea suma
   un comentario por tarea, hecho por una sola función (`post_tracker_checklist`).
3. **El trabajo del tracker que pide criterio de una persona va al humano como un
   comentario con una lista de casillas**, postea una vez, con un marcador que lleva el hash
   del contenido (`<!-- agentic-tracker: <tarea> <12 hex> -->`). La idempotencia es el patrón
   receptor de `split`: primero el comentario, después el registro
   `tracker_checklist_posted: {at, hash}` en el documento; si falta el registro, el marcador
   evita el segundo post. Una lista que cambia es un comentario nuevo que dice que reemplaza
   a la anterior: el sistema no edita ni borra comentarios.
4. **Una tarea con trabajo de tracker nunca cierra su propio issue.** Un plan con
   `tracker_checklist` y subtareas abre su pull request con `closes_issue=False`.
5. **El trabajo de tracker nunca es una subtarea**: los Developers tampoco tienen `gh`.

## La tercera forma de plan

Sin subtareas, sin partes, con `tracker_checklist` no vacío y `estimated_files == 0`. Va al
nodo terminal `tracker_only`, con el estado `tracker_only`: postea la lista y termina sin
llamar a un rol, sin pull request y sin `delivery_outcome`. Un fallo del comentario escala
con la clase `failure` y la reanudación vuelve a `tracker_only`, nunca a `plan`. Todo plan sin
lista se comporta igual que antes.

## Consecuencias

- Los comentarios que el código postea empiezan con `Tracker work for you: `, registrado en
  `resume.SYSTEM_COMMENTS`: una reanudación no los lee como decisión del humano.
- El cuadro de puntaje excluye `tracker_only`, como excluye `split` (`SCOREABLE_STATUSES` y
  `TERMINATED_STATUSES` no cambian). Es un hueco conocido: el gasto de esas tareas no se
  cuenta, y contarlo es otro issue.
- Vale para las tareas planeadas después de este cambio: un documento anterior, como el de
  GH-307, se comporta como hasta ahora.
- Que PjM y el Tech Lead dejen de pedir `gh` no lo prueba ningún test, solo que la regla esté
  en el prompt; la primera tarea real de solo tracker es la evidencia.

## Addendum (GH-342): el registro vacío y la entrega que no se cae

**Causa.** `tracker_checklist_posted` es un canal de reducción del grafo: arranca en `{}` para
toda tarea, no en "ausente". `post_tracker_checklist` trataba todo valor distinto de `None`
como un registro completo y fallaba con `KeyError: 'hash'` en `deliver` (GH-346). Ahora un
solo lector, `rules.tracker_posted`, devuelve el registro solo si `at` y `hash` son cadenas;
`{}`, un registro sin `hash` o cualquier otra cosa significa "nada publicado".

**Los dos caminos, lado a lado.**

- `tracker_only` no tiene pull request: un comentario fallido escala con `failure` y la
  reanudación vuelve al nodo.
- `deliver` abre el pull request igual. La lista, acotada (20 ítems de 200 caracteres y un
  puntero al documento, `rules.tracker_failure_block`), va en el prefacio del cuerpo, si el
  pull request se abre en esa entrega, y en el comentario "Ready for review". No se escribe
  registro, así que el marcador y el hash siguen siendo la idempotencia. Un fallo del
  guardado intermedio, del comentario de listo o de `release_task` no termina el nodo: queda
  una línea `paso: Clase` en el `pause_log`.

Límite: si una re-entrega ocurre mientras `gh` no deja comentar, la lista no llega ni al cuerpo
ni al issue; queda en el documento de la tarea.
