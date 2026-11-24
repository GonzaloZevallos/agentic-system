Esta página muestra las clases públicas de los módulos del grupo `support` de `agentic.core.process.application` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 6 de 9, con las clases en orden alfabético por módulo y clase.

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
  - [application: support (parte 9)](classes-process-application-support-9.md)

```mermaid
classDiagram
    class ports__RawResult["RawResult"]
    class ports__RemergeDiffUnsupported["RemergeDiffUnsupported"]
    class ports__RiskArea["RiskArea"]
    class ports__RoleRunner["RoleRunner"]
    class ports__RunLock["RunLock"]
    class ports__RunProcess["RunProcess"]
    class ports__RunRegistration["RunRegistration"]
    class ports__RunRegistry["RunRegistry"]
    class ports__RunStopped["RunStopped"]
    class ports__SampleApp["SampleApp"]
    class ports__SampleAppHandle["SampleAppHandle"]
    class ports__SchemaGuard["SchemaGuard"]
    class ports__Scratch["Scratch"]
    class ports__SentEntry["SentEntry"]
```

## Relaciones con otras páginas

- [application: support (parte 2)](classes-process-application-support-2.md)
  - `ports.RawResult` se asocia con `ports.CheckRuns`
- [application: support (parte 3)](classes-process-application-support-3.md)
  - `ports.RemergeDiffUnsupported` hereda de `ports.GitError`
- [application: support (parte 5)](classes-process-application-support-5.md)
  - `ports.Ports` se asocia con `ports.RoleRunner`
  - `ports.Ports` se asocia con `ports.SampleApp`
- [application: support (parte 8)](classes-process-application-support-8.md)
  - `run_role.GuardChain` agrega a `ports.SchemaGuard`
