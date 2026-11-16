Esta página muestra las clases públicas de todos los módulos de `agentic.core.measurement.application` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo.

- Padre: [Clases de application](classes-measurement-application.md)

```mermaid
classDiagram
    class dashboards__ColumnDrift["ColumnDrift"]
    class dashboards__DashboardStore["DashboardStore"]
    class dashboards__Report["Report"]
    class dashboards__StoreUnavailable["StoreUnavailable"]
    class dashboards__UnknownProject["UnknownProject"]
    class dashboards__WidgetData["WidgetData"]
    class eval_commands__Launched["Launched"]
    class listing_reads__Listing["Listing"]
    class listing_reads__Stopped["Stopped"]
    class ports__EvalChild["EvalChild"]
    class ports__EvalRoots["EvalRoots"]
    class ports__GhRead["GhRead"]
    class ports__IssueListing["IssueListing"]
    class ports__Page["Page"]
    class ports__PageUnreadable["PageUnreadable"]
    eval_commands__Launched --> ports__EvalChild
```
