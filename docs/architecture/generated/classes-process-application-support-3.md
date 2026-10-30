Esta página muestra las clases públicas de los módulos del grupo `support` de `agentic.core.process.application` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 3 de 5, con las clases en orden alfabético por módulo y clase.

- Padre: [Clases de application](classes-process-application.md)
- Hermanas:
  - [application: checks](classes-process-application-checks.md)
  - [application: definition](classes-process-application-definition.md)
  - [application: landing](classes-process-application-landing.md)
  - [application: lifecycle](classes-process-application-lifecycle.md)
  - [application: reflection](classes-process-application-reflection.md)
  - [application: support (parte 1)](classes-process-application-support-1.md)
  - [application: support (parte 2)](classes-process-application-support-2.md)
  - [application: support (parte 4)](classes-process-application-support-4.md)
  - [application: support (parte 5)](classes-process-application-support-5.md)

```mermaid
classDiagram
    class ports__MergeCallFailed["MergeCallFailed"]
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
    class ports__RoleRunner["RoleRunner"]
    class ports__RunLock["RunLock"]
    class ports__RunRegistration["RunRegistration"]
    class ports__RunRegistry["RunRegistry"]
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
  - `ports.Ports` se asocia con `ports.CheckScheduler`
- [application: support (parte 2)](classes-process-application-support-2.md)
  - `ports.Ports` se asocia con `ports.Commands`
  - `ports.Ports` se asocia con `ports.FlagVocabulary`
  - `ports.Ports` se asocia con `ports.Inbox`
- [application: support (parte 4)](classes-process-application-support-4.md)
  - `ports.Ports` se asocia con `ports.SampleApp`
  - `ports.Ports` se asocia con `ports.TaskDocuments`
  - `ports.Ports` se asocia con `ports.Tracker`
- [application: support (parte 5)](classes-process-application-support-5.md)
  - `ports.Ports` se asocia con `ports.Workspace`
