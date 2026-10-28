---
status: accepted
date: 2026-10-28
---

# El documento de tarea nunca se versiona en una rama de entrega

El 2026-10-26 el commit `5c92452f` de apostrophe (GH-442) agregó `.agentic/tasks/GH-442.md`,
una foto de 148 líneas del documento de la tarea. El documento vivo en el checkout del
producto siguió creciendo sin versionar, y desde entonces cada `git merge --ff-only
origin/master` del checkout del dueño falla con "untracked working tree files would be
overwritten by merge". El checkout dejó de seguir al master (126 commits atrás), la app
local no se actualizó, y el sistema leyó un `.agentic/stack.yaml` viejo: `agentic merge
--issue 386` rechazó con `ci_failed` por un job no bloqueante que el master ya no declara.
Los documentos de GH-47, GH-83, GH-86, GH-97 y GH-117 también están versionados y aparecen
modificados en el checkout.

Supersede parcialmente dos textos: la cláusula "el documento se commitea" de ADR-0001 y el
párrafo de autoalojamiento de ADR-0017 (`.agentic/tasks/` sí entra en el control de
versiones). Un ADR aceptado se supersede, nunca se edita (ADR-0010): ambos llevan solo una
línea de adenda que apunta aquí.

## Decisión

1. **Ninguna rama de entrega de ningún producto lleva nada bajo `.agentic/tasks/`,
   `.agentic/runs/`, `.agentic/logs/` ni `.agentic/worktrees/`.** Una regla pura,
   `rules.unstageable(relative) -> bool` sobre la tupla cerrada `rules.UNSTAGEABLE`, dice
   qué rutas son esas. Ningún otro código lleva su propia lista de prefijos.
   `commit_all` y `commit_paths` la consultan.
2. **El archivado es la única excepción.** `Workspace.commit_archived_document` commitea
   exactamente `.agentic/tasks/<id>.md`, con plumbing (sin worktree ni rama viva), sobre una
   ref que ya está bajo `archive/` (la de tarea, ya renombrada a `archive/run<N>-…/`). Usa
   un lease sobre la ref (`update-ref <ref> <nuevo> <viejo>`) para no pisar un movimiento
   concurrente. `restore` sigue leyendo el documento archivado con `show` y no cambia.
3. **El setup escribe `.agentic/tasks/*.md` en `.git/info/exclude`**, nunca en el
   `.gitignore` del producto: ADR-0002 prohíbe escribir en el checkout del humano y el
   setup solo toca `.git/info/exclude`. Cada producto agrega la línea a su propio
   `.gitignore` en una entrega propia. Esto reemplaza la letra de "el `.gitignore` del
   producto cubre `.agentic/tasks/*.md`" del issue.
4. **La puerta de entrega rechaza una ruta que el turno introduce.** Cuenta una ruta bajo
   `UNSTAGEABLE` que está agregada o modificada en `before...head` y cuyo blob en `head`
   difiere de su blob en la ref upstream ya traída. Una eliminación, o un documento que el
   master ya versiona, no cuenta. La puerta no hace fetch: una ref vieja puede rechazar una
   vez un `sync_master`, y el presupuesto de rechazos lo acota.
5. **El poller avisa con una línea de log, una vez por cambio de estado,** cuando el
   checkout del producto no puede avanzar con fast-forward a su upstream. El motivo es un
   valor cerrado: `diverged` o `would_overwrite`. La comprobación es de solo lectura (ni
   merge, ni checkout, ni fetch más allá del `upstream()` compartido de la ronda). No se
   agrega ningún span, métrica ni etiqueta de medición.

## Opciones consideradas

- **Rechazar el merge de los pull requests ya abiertos que llevan `<id>: task document`.**
  Se rechaza: escalaría todos los pull requests abiertos a la vez, y el costo es una
  colisión puntual hasta que el producto limpie su índice, que el aviso del poller cubre.
- **Escribir la regla en el `.gitignore` del producto desde el setup.** Se rechaza por
  ADR-0002.
- **Un repo aparte para `.agentic/tasks/`.** Ya rechazado en ADR-0017; un ignore no
  impide que un `git add -f` lo versione, la regla pura sí.
- **Dejar el documento versionado y arreglar el checkout a mano.** Se rechaza: el choque
  vuelve con cada documento nuevo.

## Consecuencias

- El registro crudo de una tarea vive en el disco de la máquina y en las ramas de archivo,
  no en la historia del master; leer tareas viejas desde otro clon ya no funciona. Esto
  vale también para este repo.
- `Workspace.is_ignored` queda en el puerto pero ninguna entrega ni archivado lo llama.
  La cláusula de `publish_owed`/`approved_head` "difiere solo en el documento de tarea"
  nunca coincide y queda inofensiva.
- Los documentos que un producto ya versiona siguen versionados hasta que él mismo los
  saque con `git rm --cached` en una entrega suya; mientras tanto son inofensivos porque el
  master no los vuelve a cambiar.
- Riesgos que se aceptan: los pull requests en vuelo recrean la colisión al mergear hasta
  que el producto limpie; el aviso va al log del poller, que es el canal que nadie leyó
  durante dos días (una notificación `needs-human` queda como seguimiento); si el proceso
  muere entre renombrar la rama de tarea y commitear el documento archivado, una tarea sin
  subtareas se archiva con `document_kept = False` y la copia viva sigue en disco.
