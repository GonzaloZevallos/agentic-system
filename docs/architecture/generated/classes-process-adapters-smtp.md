Esta página muestra las clases públicas de los módulos del grupo `smtp` de `agentic.core.process.adapters` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo.

- Padre: [Clases de adapters](classes-process-adapters.md)
- Hermanas:
  - [adapters: claude_sdk](classes-process-adapters-claude_sdk.md)
  - [adapters: filesystem (parte 1)](classes-process-adapters-filesystem-1.md)
  - [adapters: filesystem (parte 2)](classes-process-adapters-filesystem-2.md)
  - [adapters: git](classes-process-adapters-git.md)
  - [adapters: github (parte 1)](classes-process-adapters-github-1.md)
  - [adapters: github (parte 2)](classes-process-adapters-github-2.md)
  - [adapters: shell](classes-process-adapters-shell.md)
  - [adapters: toplevel](classes-process-adapters-toplevel.md)

```mermaid
classDiagram
    class smtp__config__SmtpSettings["SmtpSettings"]
    class smtp__notifier__SmtpNotifier["SmtpNotifier"]
    smtp__notifier__SmtpNotifier o-- smtp__config__SmtpSettings
```
