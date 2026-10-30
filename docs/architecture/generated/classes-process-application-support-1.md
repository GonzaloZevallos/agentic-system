Esta página muestra las clases públicas de los módulos del grupo `support` de `agentic.core.process.application` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 1 de 5, con las clases en orden alfabético por módulo y clase.

- Padre: [Clases de application](classes-process-application.md)
- Hermanas:
  - [application: checks](classes-process-application-checks.md)
  - [application: definition](classes-process-application-definition.md)
  - [application: landing](classes-process-application-landing.md)
  - [application: lifecycle](classes-process-application-lifecycle.md)
  - [application: reflection](classes-process-application-reflection.md)
  - [application: support (parte 2)](classes-process-application-support-2.md)
  - [application: support (parte 3)](classes-process-application-support-3.md)
  - [application: support (parte 4)](classes-process-application-support-4.md)
  - [application: support (parte 5)](classes-process-application-support-5.md)

```mermaid
classDiagram
    class checkouts__SetupFailed["SetupFailed"]
    class limits__RunLimits["RunLimits"]
    class ports__AddedAdr["AddedAdr"]
    class ports__AddedLine["AddedLine"]
    class ports__AttemptFailed["AttemptFailed"]
    class ports__Board["Board"]
    class ports__BoardSent["BoardSent"]
    class ports__BoardWritten["BoardWritten"]
    class ports__BranchPullRequest["BranchPullRequest"]
    class ports__CheckRequest["CheckRequest"]
    class ports__CheckResults["CheckResults"]
    class ports__CheckScheduler["CheckScheduler"]
    class ports__CheckTool["CheckTool"]
    class ports__Checkout["Checkout"]
    class ports__CommandResult["CommandResult"]
```

## Relaciones con otras páginas

- [application: support (parte 3)](classes-process-application-support-3.md)
  - `ports.Ports` se asocia con `limits.RunLimits`
  - `ports.Ports` se asocia con `ports.CheckScheduler`
