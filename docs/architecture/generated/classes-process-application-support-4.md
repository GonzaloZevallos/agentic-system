Esta página muestra las clases públicas de los módulos del grupo `support` de `agentic.core.process.application` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 4 de 7, con las clases en orden alfabético por módulo y clase.

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
  - [application: support (parte 5)](classes-process-application-support-5.md)
  - [application: support (parte 6)](classes-process-application-support-6.md)
  - [application: support (parte 7)](classes-process-application-support-7.md)

```mermaid
classDiagram
    class ports__MergeFailed["MergeFailed"]
    class ports__OwnerRuleEntry["OwnerRuleEntry"]
    class ports__PerfLock["PerfLock"]
    class ports__PerfRunner["PerfRunner"]
    class ports__Ports["Ports"]
    class ports__Presubmit["Presubmit"]
    class ports__ProcessReaper["ProcessReaper"]
    class ports__ProductKnowledge["ProductKnowledge"]
    class ports__ProductStack["ProductStack"]
    class ports__Prompts["Prompts"]
    class ports__RawResult["RawResult"]
    class ports__RemergeDiffUnsupported["RemergeDiffUnsupported"]
    class ports__RiskArea["RiskArea"]
    class ports__RoleRunner["RoleRunner"]
    class ports__RunLock["RunLock"]
    ports__Ports --> ports__ProcessReaper
    ports__Ports --> ports__ProductKnowledge
    ports__Ports --> ports__ProductStack
    ports__Ports --> ports__Prompts
    ports__Ports --> ports__RoleRunner
```

## Relaciones con otras páginas

- [application: checks](classes-process-application-checks.md)
  - `presubmit.CheckoutPresubmit` agrega a `ports.Ports`
- [application: landing](classes-process-application-landing.md)
  - `landing_plan.LandingContext` se asocia con `ports.Ports`
- [application: support (parte 1)](classes-process-application-support-1.md)
  - `ports.Ports` se asocia con `limits.RunLimits`
  - `ports.RawResult` se asocia con `ports.CheckRuns`
- [application: support (parte 2)](classes-process-application-support-2.md)
  - `ports.Ports` se asocia con `ports.CheckScheduler`
  - `ports.Ports` se asocia con `ports.Commands`
  - `ports.Ports` se asocia con `ports.FlagVocabulary`
  - `ports.RemergeDiffUnsupported` hereda de `ports.GitError`
- [application: support (parte 3)](classes-process-application-support-3.md)
  - `ports.Ports` se asocia con `ports.Inbox`
- [application: support (parte 5)](classes-process-application-support-5.md)
  - `ports.Ports` se asocia con `ports.SampleApp`
- [application: support (parte 6)](classes-process-application-support-6.md)
  - `ports.Ports` se asocia con `ports.TaskDocuments`
  - `ports.Ports` se asocia con `ports.Tracker`
  - `ports.Ports` se asocia con `ports.Workspace`
