Esta página muestra las clases públicas de los módulos del grupo `scorecard` de `agentic.core.measurement.domain` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 2 de 2, con las clases en orden alfabético por módulo y clase.

- Padre: [Clases de domain](classes-measurement-domain.md)
- Hermanas:
  - [domain: langfuse](classes-measurement-domain-langfuse.md)
  - [domain: reports](classes-measurement-domain-reports.md)
  - [domain: scorecard (parte 1)](classes-measurement-domain-scorecard-1.md)

```mermaid
classDiagram
    class evaluation__Score["Score"]
    class evaluation__TaskDocument["TaskDocument"]
    class scorecard__CauseCount["CauseCount"]
    class scorecard__Figure["Figure"]
    class scorecard__Scorecard["Scorecard"]
    class scorecard__ScorecardDocument["ScorecardDocument"]
    class scorecard__TaskFigures["TaskFigures"]
    class scorecard__Verdict["Verdict"]
    class stages__Claim["Claim"]
    class stages__StageUnknown["StageUnknown"]
    scorecard__Scorecard --> scorecard__Figure
```

## Relaciones con otras páginas

- [domain: scorecard (parte 1)](classes-measurement-domain-scorecard-1.md)
  - `scorecard.TaskFigures` se asocia con `cycle.PathFacts`
