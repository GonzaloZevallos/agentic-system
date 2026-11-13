Esta página muestra las clases públicas de los módulos del grupo `support` de `agentic.core.process.application` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 3 de 7, con las clases en orden alfabético por módulo y clase.

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
  - [application: support (parte 4)](classes-process-application-support-4.md)
  - [application: support (parte 5)](classes-process-application-support-5.md)
  - [application: support (parte 6)](classes-process-application-support-6.md)
  - [application: support (parte 7)](classes-process-application-support-7.md)

```mermaid
classDiagram
    class ports__Grant["Grant"]
    class ports__Inbox["Inbox"]
    class ports__IssueComment["IssueComment"]
    class ports__IssueRow["IssueRow"]
    class ports__IssueState["IssueState"]
    class ports__LabelTimeline["LabelTimeline"]
    class ports__LaunchAttempt["LaunchAttempt"]
    class ports__Launcher["Launcher"]
    class ports__ListedPullRequest["ListedPullRequest"]
    class ports__LoadReader["LoadReader"]
    class ports__LockHeld["LockHeld"]
    class ports__LockRecord["LockRecord"]
    class ports__MergeCallFailed["MergeCallFailed"]
    ports__LockHeld o-- ports__LockRecord
```

## Relaciones con otras páginas

- [application: checks](classes-process-application-checks.md)
  - `presubmit.Readiness` se asocia con `ports.LoadReader`
- [application: reflection](classes-process-application-reflection.md)
  - `reflect_run.SystemRepo` se asocia con `ports.Inbox`
- [application: support (parte 4)](classes-process-application-support-4.md)
  - `ports.Ports` se asocia con `ports.Inbox`
