Esta página muestra las clases públicas de los módulos del grupo `landing` de `agentic.core.process.application` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo.

- Padre: [Clases de application](classes-process-application.md)
- Hermanas:
  - [application: checks](classes-process-application-checks.md)
  - [application: definition](classes-process-application-definition.md)
  - [application: lifecycle](classes-process-application-lifecycle.md)
  - [application: reflection](classes-process-application-reflection.md)
  - [application: support (parte 1)](classes-process-application-support-1.md)
  - [application: support (parte 2)](classes-process-application-support-2.md)
  - [application: support (parte 3)](classes-process-application-support-3.md)
  - [application: support (parte 4)](classes-process-application-support-4.md)
  - [application: support (parte 5)](classes-process-application-support-5.md)
  - [application: support (parte 6)](classes-process-application-support-6.md)

```mermaid
classDiagram
    class landing__LandResult["LandResult"]
    class landing_plan__LandingContext["LandingContext"]
    class landing_plan__Release["Release"]
    class landing_plan__RoleRun["RoleRun"]
    class landing_sync__Merges["Merges"]
    class landing_sync__ResumeContext["ResumeContext"]
    class merge__MergeResult["MergeResult"]
```

## Relaciones con otras páginas

- [application: support (parte 3)](classes-process-application-support-3.md)
  - `landing_plan.LandingContext` se asocia con `ports.Ports`
- [application: support (parte 5)](classes-process-application-support-5.md)
  - `landing_plan.LandingContext` se asocia con `ports.Upstream`
