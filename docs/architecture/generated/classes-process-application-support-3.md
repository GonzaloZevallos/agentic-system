Esta página muestra las clases públicas de los módulos del grupo `support` de `agentic.core.process.application` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 3 de 8, con las clases en orden alfabético por módulo y clase.

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
  - [application: support (parte 8)](classes-process-application-support-8.md)

```mermaid
classDiagram
    class ports__FormatFixReads["FormatFixReads"]
    class ports__FreeSpace["FreeSpace"]
    class ports__GitError["GitError"]
    class ports__Grant["Grant"]
    class ports__Heartbeats["Heartbeats"]
    class ports__HeldListMayBeCut["HeldListMayBeCut"]
    class ports__Inbox["Inbox"]
    class ports__InstalledProducts["InstalledProducts"]
    class ports__IssueComment["IssueComment"]
    class ports__IssueRow["IssueRow"]
    class ports__IssueState["IssueState"]
    class ports__LabelTimeline["LabelTimeline"]
    class ports__LandRecord["LandRecord"]
    class ports__LaunchAttempt["LaunchAttempt"]
```

## Relaciones con otras páginas

- [application: support (parte 1)](classes-process-application-support-1.md)
  - `ci_push.PushFailed` hereda de `ports.GitError`
- [application: support (parte 4)](classes-process-application-support-4.md)
  - `ports.Ports` se asocia con `ports.Inbox`
- [application: support (parte 5)](classes-process-application-support-5.md)
  - `ports.RemergeDiffUnsupported` hereda de `ports.GitError`
- [application: support (parte 7)](classes-process-application-support-7.md)
  - `ports.HeldListMayBeCut` hereda de `ports.TrackerCallFailed`
  - `ports.SystemRepo` se asocia con `ports.Inbox`
  - `ports.UnstageablePath` hereda de `ports.GitError`
