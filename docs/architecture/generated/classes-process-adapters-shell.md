Esta página muestra las clases públicas de los módulos del grupo `shell` de `agentic.core.process.adapters` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo.

- Padre: [Clases de adapters](classes-process-adapters.md)
- Hermanas:
  - [adapters: claude_sdk](classes-process-adapters-claude_sdk.md)
  - [adapters: filesystem (parte 1)](classes-process-adapters-filesystem-1.md)
  - [adapters: filesystem (parte 2)](classes-process-adapters-filesystem-2.md)
  - [adapters: git](classes-process-adapters-git.md)
  - [adapters: github](classes-process-adapters-github.md)
  - [adapters: toplevel](classes-process-adapters-toplevel.md)

```mermaid
classDiagram
    class shell__commands__ShellCommands["ShellCommands"]
    class shell__load_reader__ShellLoadReader["ShellLoadReader"]
    class shell__perf_runner__PerfShellRunner["PerfShellRunner"]
    class shell__reaper__ShellReaper["ShellReaper"]
    class shell__sample_app__ShellSampleApp["ShellSampleApp"]
    class shell__stage0_candidate__Runner["Runner"]
    class shell__stage0_candidate__ShellStage0Candidate["ShellStage0Candidate"]
    class shell__stage0_candidate__SubprocessRunner["SubprocessRunner"]
    class shell__stage0_candidate__TimedOut["TimedOut"]
    class shell__stop__EventStopFlag["EventStopFlag"]
    class shell__stop__NullStopFlag["NullStopFlag"]
```
