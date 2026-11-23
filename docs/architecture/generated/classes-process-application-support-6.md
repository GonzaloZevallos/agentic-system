Esta página muestra las clases públicas de los módulos del grupo `support` de `agentic.core.process.application` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 6 de 8, con las clases en orden alfabético por módulo y clase.

- Padre: [Clases de application](classes-process-application.md)
- Hermanas:
  - [application: build](classes-process-application-build.md)
  - [application: checks](classes-process-application-checks.md)
  - [application: definition](classes-process-application-definition.md)
  - [application: landing](classes-process-application-landing.md)
  - [application: lifecycle (parte 1)](classes-process-application-lifecycle-1.md)
  - [application: lifecycle (parte 2)](classes-process-application-lifecycle-2.md)
  - [application: reflection](classes-process-application-reflection.md)
  - [application: support (parte 1)](classes-process-application-support-1.md)
  - [application: support (parte 2)](classes-process-application-support-2.md)
  - [application: support (parte 3)](classes-process-application-support-3.md)
  - [application: support (parte 4)](classes-process-application-support-4.md)
  - [application: support (parte 5)](classes-process-application-support-5.md)
  - [application: support (parte 7)](classes-process-application-support-7.md)
  - [application: support (parte 8)](classes-process-application-support-8.md)

```mermaid
classDiagram
    class ports__SentEntry["SentEntry"]
    class ports__SentMemory["SentMemory"]
    class ports__SpecReader["SpecReader"]
    class ports__SpecUnreadable["SpecUnreadable"]
    class ports__SpecValidator["SpecValidator"]
    class ports__Stage0Candidate["Stage0Candidate"]
    class ports__Stage0Escalation["Stage0Escalation"]
    class ports__Stage0Store["Stage0Store"]
    class ports__Stage0Users["Stage0Users"]
    class ports__StopFlag["StopFlag"]
    class ports__SystemRepo["SystemRepo"]
    class ports__TaskDocuments["TaskDocuments"]
    class ports__Tracker["Tracker"]
    class ports__TrackerCallFailed["TrackerCallFailed"]
    class ports__TrackerRateLimited["TrackerRateLimited"]
    ports__SystemRepo --> ports__Tracker
    ports__TrackerRateLimited --|> ports__TrackerCallFailed
```

## Relaciones con otras páginas

- [application: support (parte 3)](classes-process-application-support-3.md)
  - `ports.HeldListMayBeCut` hereda de `ports.TrackerCallFailed`
  - `ports.SystemRepo` se asocia con `ports.Inbox`
- [application: support (parte 4)](classes-process-application-support-4.md)
  - `ports.Ports` se asocia con `ports.TaskDocuments`
  - `ports.Ports` se asocia con `ports.Tracker`
- [application: support (parte 7)](classes-process-application-support-7.md)
  - `ports.TrackerTimedOut` hereda de `ports.TrackerCallFailed`
  - `ports.TrackerTransient` hereda de `ports.TrackerCallFailed`
  - `ports.TrackerUnreachable` hereda de `ports.TrackerCallFailed`
