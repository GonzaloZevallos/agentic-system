Esta página muestra las clases públicas de los módulos del grupo `support` de `agentic.core.process.application` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 6 de 6, con las clases en orden alfabético por módulo y clase.

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
  - [application: support (parte 5)](classes-process-application-support-5.md)

```mermaid
classDiagram
    class ports__UpstreamMerge["UpstreamMerge"]
    class ports__Workspace["Workspace"]
    class preflight__RunsOnItsOwnCheckout["RunsOnItsOwnCheckout"]
    class preflight__ScratchInARepository["ScratchInARepository"]
    class run_role__Escalating["Escalating"]
    class run_role__EstimateCapReached["EstimateCapReached"]
    class run_role__EstimateStop["EstimateStop"]
    class run_role__GuardChain["GuardChain"]
    class run_role__Output["Output"]
    class run_role__PromptFailure["PromptFailure"]
    class run_role__RoleFailure["RoleFailure"]
    class run_role__TaskCapReached["TaskCapReached"]
    run_role__EstimateCapReached --|> run_role__Escalating
    run_role__PromptFailure --|> run_role__Escalating
    run_role__RoleFailure --|> run_role__Escalating
    run_role__TaskCapReached --|> run_role__Escalating
```

## Relaciones con otras páginas

- [application: support (parte 3)](classes-process-application-support-3.md)
  - `ports.Ports` se asocia con `ports.Workspace`
- [application: support (parte 4)](classes-process-application-support-4.md)
  - `run_role.GuardChain` agrega a `ports.SchemaGuard`
