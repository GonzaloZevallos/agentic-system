Esta página muestra las clases públicas de los módulos del grupo `github` de `agentic.core.process.adapters` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 1 de 2, con las clases en orden alfabético por módulo y clase. Las relaciones entre las clases de `github.board` y las demás clases de la capa no se dibujan: pyreverse analizó ese archivo en una corrida aparte.

- Padre: [Clases de adapters](classes-process-adapters.md)
- Hermanas:
  - [adapters: claude_sdk](classes-process-adapters-claude_sdk.md)
  - [adapters: filesystem (parte 1)](classes-process-adapters-filesystem-1.md)
  - [adapters: filesystem (parte 2)](classes-process-adapters-filesystem-2.md)
  - [adapters: git](classes-process-adapters-git.md)
  - [adapters: github (parte 2)](classes-process-adapters-github-2.md)
  - [adapters: shell](classes-process-adapters-shell.md)
  - [adapters: smtp](classes-process-adapters-smtp.md)
  - [adapters: toplevel](classes-process-adapters-toplevel.md)

```mermaid
classDiagram
    class github__account__AccountKeys["AccountKeys"]
    class github__activity__GhActivity["GhActivity"]
    class github__board__GhBoard["GhBoard"]
    class github__ci_logs__GhCiLogs["GhCiLogs"]
    class github__commit_ci__GhCommitCi["GhCommitCi"]
    class github__format_fix_reads__GhFormatFixReads["GhFormatFixReads"]
    class github__format_fix_reads__GhRepoSlug["GhRepoSlug"]
    class github__gh_call_log__InProcessGhCallLog["InProcessGhCallLog"]
```
