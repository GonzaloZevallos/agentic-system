Esta página muestra las clases públicas de los módulos del grupo `toplevel` de `agentic.core.process.adapters` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo.

- Padre: [Clases de adapters](classes-process-adapters.md)
- Hermanas:
  - [adapters: claude_sdk](classes-process-adapters-claude_sdk.md)
  - [adapters: filesystem (parte 1)](classes-process-adapters-filesystem-1.md)
  - [adapters: filesystem (parte 2)](classes-process-adapters-filesystem-2.md)
  - [adapters: git](classes-process-adapters-git.md)
  - [adapters: github](classes-process-adapters-github.md)
  - [adapters: shell](classes-process-adapters-shell.md)

```mermaid
classDiagram
    class evaluation__ClosedInbox["ClosedInbox"]
    class evaluation__EvaluationStack["EvaluationStack"]
    class evaluation__EvaluationTracker["EvaluationTracker"]
    class evaluation__EvaluationWorkspace["EvaluationWorkspace"]
    class evaluation__EvaluationWrite["EvaluationWrite"]
    class telemetry__NoTelemetry["NoTelemetry"]
    class telemetry__RoleSpan["RoleSpan"]
    class telemetry__Telemetry["Telemetry"]
```

## Relaciones con otras páginas

- [adapters: claude_sdk](classes-process-adapters-claude_sdk.md)
  - `claude_sdk.runner.ClaudeSdkRunner` agrega a `telemetry.Telemetry`
