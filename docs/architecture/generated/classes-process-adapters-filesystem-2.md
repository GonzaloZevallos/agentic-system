Esta página muestra las clases públicas de los módulos del grupo `filesystem` de `agentic.core.process.adapters` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 2 de 2, con las clases en orden alfabético por módulo y clase.

- Padre: [Clases de adapters](classes-process-adapters.md)
- Hermanas:
  - [adapters: claude_sdk](classes-process-adapters-claude_sdk.md)
  - [adapters: filesystem (parte 1)](classes-process-adapters-filesystem-1.md)
  - [adapters: git](classes-process-adapters-git.md)
  - [adapters: github](classes-process-adapters-github.md)
  - [adapters: shell](classes-process-adapters-shell.md)
  - [adapters: toplevel](classes-process-adapters-toplevel.md)

```mermaid
classDiagram
    class filesystem__liveness__OsRunProcess["OsRunProcess"]
    class filesystem__lock__FilesystemRunLock["FilesystemRunLock"]
    class filesystem__perf_lock__FilesystemPerfLock["FilesystemPerfLock"]
    class filesystem__prompts__FilesystemPrompts["FilesystemPrompts"]
    class filesystem__run_registry__FilesystemRunRegistry["FilesystemRunRegistry"]
    class filesystem__scratch__FilesystemScratch["FilesystemScratch"]
    class filesystem__stage0_store__FilesystemStage0Store["FilesystemStage0Store"]
    class filesystem__stage0_users__ProcStage0Users["ProcStage0Users"]
```
