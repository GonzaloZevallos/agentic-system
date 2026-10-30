Esta página muestra las clases públicas de todos los módulos de `agentic.core.measurement.adapters` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo.

- Padre: [Clases de adapters](classes-measurement-adapters.md)

```mermaid
classDiagram
    class langfuse__dashboards__PostgresDashboards["PostgresDashboards"]
    class langfuse__scores__LangfuseScores["LangfuseScores"]
    class langfuse__scores__Rejected["Rejected"]
    class langfuse__widget_data__ClickHouseWidgetData["ClickHouseWidgetData"]
    class otel__telemetry__RoleRun["RoleRun"]
    class otel__telemetry__Run["Run"]
    class otel__telemetry__TaskRun["TaskRun"]
    otel__telemetry__RoleRun --|> otel__telemetry__Run
    otel__telemetry__TaskRun --|> otel__telemetry__Run
```
