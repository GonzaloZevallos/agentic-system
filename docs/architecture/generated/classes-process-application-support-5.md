Esta página muestra las clases públicas de los módulos del grupo `support` de `agentic.core.process.application` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 5 de 6, con las clases en orden alfabético por módulo y clase.

- Padre: [Clases de application](classes-process-application.md)
- Hermanas:
  - [application: checks](classes-process-application-checks.md)
  - [application: definition](classes-process-application-definition.md)
  - [application: landing](classes-process-application-landing.md)
  - [application: lifecycle](classes-process-application-lifecycle.md)
  - [application: reflection](classes-process-application-reflection.md)
  - [application: support (parte 1)](classes-process-application-support-1.md)
  - [application: support (parte 2)](classes-process-application-support-2.md)
  - [application: support (parte 3)](classes-process-application-support-3.md)
  - [application: support (parte 4)](classes-process-application-support-4.md)
  - [application: support (parte 6)](classes-process-application-support-6.md)

```mermaid
classDiagram
    class ports__SentEntry["SentEntry"]
    class ports__SentMemory["SentMemory"]
    class ports__StopFlag["StopFlag"]
    class ports__TaskDocuments["TaskDocuments"]
    class ports__Tracker["Tracker"]
    class ports__TrackerCallFailed["TrackerCallFailed"]
    class ports__TrackerRateLimited["TrackerRateLimited"]
    class ports__TrackerRefused["TrackerRefused"]
    class ports__TrackerTimedOut["TrackerTimedOut"]
    class ports__TrackerTransient["TrackerTransient"]
    class ports__TrackerUnreachable["TrackerUnreachable"]
    class ports__UnstageablePath["UnstageablePath"]
    class ports__Upstream["Upstream"]
    ports__TrackerRateLimited --|> ports__TrackerCallFailed
    ports__TrackerTimedOut --|> ports__TrackerCallFailed
    ports__TrackerTransient --|> ports__TrackerCallFailed
    ports__TrackerUnreachable --|> ports__TrackerCallFailed
```

## Relaciones con otras páginas

- [application: landing](classes-process-application-landing.md)
  - `landing_plan.LandingContext` se asocia con `ports.Upstream`
- [application: reflection](classes-process-application-reflection.md)
  - `reflect_run.SystemRepo` se asocia con `ports.Tracker`
- [application: support (parte 2)](classes-process-application-support-2.md)
  - `ports.UnstageablePath` hereda de `ports.GitError`
- [application: support (parte 3)](classes-process-application-support-3.md)
  - `ports.Ports` se asocia con `ports.TaskDocuments`
  - `ports.Ports` se asocia con `ports.Tracker`
