Esta página muestra las clases públicas de los módulos del grupo `checks` de `agentic.core.process.application` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo.

- Padre: [Clases de application](classes-process-application.md)
- Hermanas:
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
    class ci_review__CiEvidence["CiEvidence"]
    class coverage_guard__InvariantCoverageGuard["InvariantCoverageGuard"]
    class perf__PerfExit["PerfExit"]
    class perf__PerfInterrupted["PerfInterrupted"]
    class perf__PerfRefused["PerfRefused"]
    class perf__Report["Report"]
    class presubmit__CheckoutPresubmit["CheckoutPresubmit"]
    class presubmit__Reuse["Reuse"]
    class run_check__RunCheck["RunCheck"]
    class schema_guard__RoleSchemaGuard["RoleSchemaGuard"]
    run_check__RunCheck o-- presubmit__CheckoutPresubmit
```

## Relaciones con otras páginas

- [application: support (parte 3)](classes-process-application-support-3.md)
  - `presubmit.CheckoutPresubmit` agrega a `ports.Ports`
