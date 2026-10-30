Esta página muestra las clases públicas de los módulos del grupo `support` de `agentic.core.process.application` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 2 de 5, con las clases en orden alfabético por módulo y clase.

- Padre: [Clases de application](classes-process-application.md)
- Hermanas:
  - [application: checks](classes-process-application-checks.md)
  - [application: definition](classes-process-application-definition.md)
  - [application: landing](classes-process-application-landing.md)
  - [application: lifecycle](classes-process-application-lifecycle.md)
  - [application: reflection](classes-process-application-reflection.md)
  - [application: support (parte 1)](classes-process-application-support-1.md)
  - [application: support (parte 3)](classes-process-application-support-3.md)
  - [application: support (parte 4)](classes-process-application-support-4.md)
  - [application: support (parte 5)](classes-process-application-support-5.md)

```mermaid
classDiagram
    class ports__Commands["Commands"]
    class ports__FastForwardView["FastForwardView"]
    class ports__FlagVocabulary["FlagVocabulary"]
    class ports__FreeSpace["FreeSpace"]
    class ports__GitError["GitError"]
    class ports__Grant["Grant"]
    class ports__Inbox["Inbox"]
    class ports__IssueComment["IssueComment"]
    class ports__IssueRow["IssueRow"]
    class ports__IssueState["IssueState"]
    class ports__LaunchAttempt["LaunchAttempt"]
    class ports__Launcher["Launcher"]
    class ports__ListedPullRequest["ListedPullRequest"]
    class ports__LockHeld["LockHeld"]
    class ports__LockRecord["LockRecord"]
    ports__LockHeld o-- ports__LockRecord
```

## Relaciones con otras páginas

- [application: reflection](classes-process-application-reflection.md)
  - `reflect_run.SystemRepo` se asocia con `ports.Inbox`
- [application: support (parte 3)](classes-process-application-support-3.md)
  - `ports.Ports` se asocia con `ports.Commands`
  - `ports.Ports` se asocia con `ports.FlagVocabulary`
  - `ports.Ports` se asocia con `ports.Inbox`
- [application: support (parte 5)](classes-process-application-support-5.md)
  - `ports.UnstageablePath` hereda de `ports.GitError`
