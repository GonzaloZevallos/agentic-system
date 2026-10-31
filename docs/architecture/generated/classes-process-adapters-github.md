Esta página muestra las clases públicas de los módulos del grupo `github` de `agentic.core.process.adapters` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. Las relaciones entre las clases de `github.board` y las demás clases de la capa no se dibujan: pyreverse analizó ese archivo en una corrida aparte.

- Padre: [Clases de adapters](classes-process-adapters.md)
- Hermanas:
  - [adapters: claude_sdk](classes-process-adapters-claude_sdk.md)
  - [adapters: filesystem](classes-process-adapters-filesystem.md)
  - [adapters: git](classes-process-adapters-git.md)
  - [adapters: shell](classes-process-adapters-shell.md)
  - [adapters: toplevel](classes-process-adapters-toplevel.md)

```mermaid
classDiagram
    class github__board__GhBoard["GhBoard"]
    class github__ci_logs__GhCiLogs["GhCiLogs"]
    class github__inbox__GhInbox["GhInbox"]
    class github__run__GhRetry["GhRetry"]
    class github__run__StreamRead["StreamRead"]
    class github__tracker__GhTracker["GhTracker"]
```
