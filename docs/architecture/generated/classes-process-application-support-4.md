Esta página muestra las clases públicas de los módulos del grupo `support` de `agentic.core.process.application` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 4 de 6, con las clases en orden alfabético por módulo y clase.

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
  - [application: support (parte 5)](classes-process-application-support-5.md)
  - [application: support (parte 6)](classes-process-application-support-6.md)

```mermaid
classDiagram
    class ports__ProductKnowledge["ProductKnowledge"]
    class ports__ProductStack["ProductStack"]
    class ports__Prompts["Prompts"]
    class ports__RawResult["RawResult"]
    class ports__RoleRunner["RoleRunner"]
    class ports__RunLock["RunLock"]
    class ports__RunRegistration["RunRegistration"]
    class ports__RunRegistry["RunRegistry"]
    class ports__RunStopped["RunStopped"]
    class ports__SampleApp["SampleApp"]
    class ports__SampleAppHandle["SampleAppHandle"]
    class ports__SchemaGuard["SchemaGuard"]
    class ports__Scratch["Scratch"]
```

## Relaciones con otras páginas

- [application: support (parte 3)](classes-process-application-support-3.md)
  - `ports.Ports` se asocia con `ports.ProductKnowledge`
  - `ports.Ports` se asocia con `ports.ProductStack`
  - `ports.Ports` se asocia con `ports.Prompts`
  - `ports.Ports` se asocia con `ports.RoleRunner`
  - `ports.Ports` se asocia con `ports.SampleApp`
- [application: support (parte 6)](classes-process-application-support-6.md)
  - `run_role.GuardChain` agrega a `ports.SchemaGuard`
