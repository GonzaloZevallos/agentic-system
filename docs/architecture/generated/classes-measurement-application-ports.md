Esta página muestra las clases públicas de los módulos del grupo `ports` de `agentic.core.measurement.application` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo.

- Padre: [Clases de application](classes-measurement-application.md)
- Hermanas:
  - [application: commands](classes-measurement-application-commands.md)

```mermaid
classDiagram
    class listing_reads__Listing["Listing"]
    class listing_reads__Stopped["Stopped"]
    class ports__EvalChild["EvalChild"]
    class ports__EvalRoots["EvalRoots"]
    class ports__GhRead["GhRead"]
    class ports__IssueListing["IssueListing"]
    class ports__NoCache["NoCache"]
    class ports__Page["Page"]
    class ports__PageUnreadable["PageUnreadable"]
    class ports__QuotaReading["QuotaReading"]
    class ports__QuotaUnreadable["QuotaUnreadable"]
    class ports__ReadCache["ReadCache"]
    class ports__Spend["Spend"]
    listing_reads__Listing --> ports__Spend
    listing_reads__Stopped --> ports__Spend
```

## Relaciones con otras páginas

- [application: commands](classes-measurement-application-commands.md)
  - `eval_commands.Launched` se asocia con `ports.EvalChild`
