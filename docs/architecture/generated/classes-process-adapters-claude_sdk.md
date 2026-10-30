Esta página muestra las clases públicas de los módulos del grupo `claude_sdk` de `agentic.core.process.adapters` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo.

- Padre: [Clases de adapters](classes-process-adapters.md)
- Hermanas:
  - [adapters: filesystem](classes-process-adapters-filesystem.md)
  - [adapters: git](classes-process-adapters-git.md)
  - [adapters: github](classes-process-adapters-github.md)
  - [adapters: shell](classes-process-adapters-shell.md)
  - [adapters: toplevel](classes-process-adapters-toplevel.md)

```mermaid
classDiagram
    class claude_sdk__hooks__HookActivity["HookActivity"]
    class claude_sdk__hooks__TurnCount["TurnCount"]
    class claude_sdk__printer__Printer["Printer"]
    class claude_sdk__runner__ClaudeSdkRunner["ClaudeSdkRunner"]
```

## Relaciones con otras páginas

- [adapters: toplevel](classes-process-adapters-toplevel.md)
  - `claude_sdk.runner.ClaudeSdkRunner` agrega a `telemetry.Telemetry`
