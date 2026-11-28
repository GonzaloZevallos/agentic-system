Esta página muestra las clases públicas de los módulos del grupo `github` de `agentic.core.process.adapters` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 2 de 2, con las clases en orden alfabético por módulo y clase.

- Padre: [Clases de adapters](classes-process-adapters.md)
- Hermanas:
  - [adapters: claude_sdk](classes-process-adapters-claude_sdk.md)
  - [adapters: filesystem (parte 1)](classes-process-adapters-filesystem-1.md)
  - [adapters: filesystem (parte 2)](classes-process-adapters-filesystem-2.md)
  - [adapters: git](classes-process-adapters-git.md)
  - [adapters: github (parte 1)](classes-process-adapters-github-1.md)
  - [adapters: shell](classes-process-adapters-shell.md)
  - [adapters: smtp](classes-process-adapters-smtp.md)
  - [adapters: toplevel](classes-process-adapters-toplevel.md)

```mermaid
classDiagram
    class github__inbox__GhInbox["GhInbox"]
    class github__label_timeline__GhLabelTimeline["GhLabelTimeline"]
    class github__rate_limit_watch__GhRateLimitWatch["GhRateLimitWatch"]
    class github__rate_limit_watch__RateLimitReader["RateLimitReader"]
    class github__run__GhRetry["GhRetry"]
    class github__run__RateLimitLatch["RateLimitLatch"]
    class github__run__StreamRead["StreamRead"]
    class github__tracker__GhTracker["GhTracker"]
```
