---
status: accepted
date: 2026-10-28
---

# PM también devuelve los archivos de capacidad al cerrar, y el sistema los escribe y los commitea en la rama de la tarea

GH-399. PM ya destila al aprobar el PRD (ADR-0006): devuelve el archivo completo de cada
capacidad tocada y los términos del glosario. Eso registra lo que se pidió, no lo que se
entregó, y nada lo refresca al cerrar: la validación final de PM (`pm_final`, ADR-0040) no
devolvía `definitions`. El dueño quiere documentación funcional que cualquiera lea para
entender cómo anda el producto, al día con cada issue que cierra.

Extiende ADR-0006 (el destilado) y ADR-0040 (la validación final de PM).

## Decisión

1. **Un segundo pase de destilado.** Cuando `pm_final` aprueba, devuelve también
   `definitions`, con la misma forma y la misma validación que las del PRD
   (`clarification_check`, sin segunda copia de esas reglas): el archivo completo de cada
   capacidad cuyo comportamiento cambió lo entregado, conciliado con el diff
   (`git diff <base>..<head>`) y con los informes finales de UX Lead y UI Lead. Vacío si
   ninguna cambió, y siempre vacío en `returned`: un `returned` con `definitions` se rechaza
   y no se escribe nada. `FinalValidation` no lleva glosario.
2. **El sistema escribe y commitea.** El nodo escribe con `ports.knowledge.consolidate`,
   commitea las rutas relativas a la tarea en la rama de la tarea y registra
   `close_docs {parent, head, paths}` en el documento, solo cuando hay commit. Los archivos
   entran en el pull request que se revisa.
3. **`rules.head_reviewed`.** Es el único predicado que compara la cabeza del pull request
   con `reviewed_head`/`approved_head`: lo usan `approved_head`, `auto_merge_decision`,
   `plan_settlement.approved_green` y `publication_candidate`. Acepta la cabeza de
   `close_docs` solo si su `parent` es la cabeza revisada de la unidad y toda ruta de
   `close_docs.paths` está bajo `docs/product/`. Así el merge, la publicación debida y el
   aterrizaje aceptan ese commit y ningún otro.
4. **Una sola escritora de `task_head`.** Sigue siendo el fan-in (`integrate`). Tras
   `pm_final` el grafo vuelve al fan-in, que estampa la cabeza nueva.
5. **Alcance.** Solo features con `touches_ui`, que es donde corre `pm_final`. Bugs,
   platform enhancements, features sin UI y aprobaciones con `definitions` vacío conservan
   el camino, las corridas de roles, las escrituras y los bytes del documento de hoy: sin
   commit y sin `close_docs`.
6. **Idioma y capturas.** Toda la prosa de documentación va en español rioplatense; los
   identificadores, rutas y nombres de campo quedan en inglés y los datos cargados por el
   usuario no se traducen. Una capacidad que muestra una pantalla enlaza su imagen
   `docs/assets/screens/<nombre>.webp` solo si existe, solo de las pantallas que la tarea
   tocó y con el nombre del registro de pantallas del producto.

## Consecuencias

- **Costo.** Cada feature con UI cuyo PM devuelve definiciones paga un chequeo completo más
  sobre la cabeza con documentación (con `ci:` declarado, una corrida de CI). Es lineal en
  esas tareas y no toca a las demás.
- **Riesgo nombrado.** Tech Lead no revisa la prosa de capacidad que PM escribe al cerrar.
  Se mitiga porque PM lee el diff entregado antes de escribir, y porque el cuerpo del pull
  request y el comentario de "listo" nombran los archivos de `close_docs.paths` como
  escritos por PM sin revisión, para que el humano los mire. `close_docs.paths` es una
  anotación, no una medición del diff; la hace confiable que el mismo helper arme el commit
  y el registro.
- **Seguridad.** PM final gana lectura de todo el checkout de la tarea (`Read`, `Glob`,
  `Grep`, `git diff`, `git log`, `git show`) y no escribe nada.
- **El commit del documento de la tarea no cambia.** Cuando `deliver` commitea el
  documento de la tarea encima de la cabeza con el cuerpo acotado, `pr_head` ya difería de
  lo revisado; el predicado compara contra `pr_head` igual que antes, así que ese merge se
  pierde igual que hoy.
- **Fuera de alcance.** Extender el pase a features sin UI, y `docs/README.md` ("Cómo
  funciona"), que va junto al issue del producto que genera las capturas.
