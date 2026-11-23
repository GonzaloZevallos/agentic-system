Esta página muestra las clases públicas de los módulos del grupo `support` de `agentic.core.process.application` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 7 de 8, con las clases en orden alfabético por módulo y clase.

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
  - [application: support (parte 6)](classes-process-application-support-6.md)
  - [application: support (parte 8)](classes-process-application-support-8.md)

```mermaid
classDiagram
    class ports__TrackerRateLimited["TrackerRateLimited"]
    class ports__TrackerRefused["TrackerRefused"]
    class ports__TrackerTimedOut["TrackerTimedOut"]
    class ports__TrackerTransient["TrackerTransient"]
    class ports__TrackerUnreachable["TrackerUnreachable"]
    class ports__UnstageablePath["UnstageablePath"]
    class ports__Upstream["Upstream"]
    class ports__UpstreamMerge["UpstreamMerge"]
    class ports__Workspace["Workspace"]
    class preflight__RunsOnItsOwnCheckout["RunsOnItsOwnCheckout"]
    class preflight__ScratchInARepository["ScratchInARepository"]
    class run_role__Escalating["Escalating"]
    class run_role__EstimateCapReached["EstimateCapReached"]
    class run_role__EstimateStop["EstimateStop"]
    class run_role__GuardChain["GuardChain"]
    run_role__EstimateCapReached --|> run_role__Escalating
```

## Relaciones con otras páginas

- [application: landing](classes-process-application-landing.md)
  - `landing_plan.LandingContext` se asocia con `ports.Upstream`
- [application: support (parte 3)](classes-process-application-support-3.md)
  - `ports.UnstageablePath` hereda de `ports.GitError`
- [application: support (parte 4)](classes-process-application-support-4.md)
  - `ports.Ports` se asocia con `ports.Workspace`
- [application: support (parte 5)](classes-process-application-support-5.md)
  - `run_role.GuardChain` agrega a `ports.SchemaGuard`
- [application: support (parte 6)](classes-process-application-support-6.md)
  - `ports.TrackerRateLimited` hereda de `ports.TrackerCallFailed`
  - `ports.TrackerTimedOut` hereda de `ports.TrackerCallFailed`
  - `ports.TrackerTransient` hereda de `ports.TrackerCallFailed`
  - `ports.TrackerUnreachable` hereda de `ports.TrackerCallFailed`
- [application: support (parte 8)](classes-process-application-support-8.md)
  - `run_role.PromptFailure` hereda de `run_role.Escalating`
  - `run_role.RoleFailure` hereda de `run_role.Escalating`
  - `run_role.TaskCapReached` hereda de `run_role.Escalating`
