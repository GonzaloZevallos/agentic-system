Esta página muestra las clases públicas de los módulos del grupo `commands` de `agentic.core.measurement.application` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo.

- Padre: [Clases de application](classes-measurement-application.md)
- Hermanas:
  - [application: ports](classes-measurement-application-ports.md)

```mermaid
classDiagram
    class dashboards__ColumnDrift["ColumnDrift"]
    class dashboards__DashboardStore["DashboardStore"]
    class dashboards__Report["Report"]
    class dashboards__StoreUnavailable["StoreUnavailable"]
    class dashboards__UnknownProject["UnknownProject"]
    class dashboards__WidgetData["WidgetData"]
    class eval_commands__Launched["Launched"]
```

## Relaciones con otras páginas

- [application: ports](classes-measurement-application-ports.md)
  - `eval_commands.Launched` se asocia con `ports.EvalChild`
