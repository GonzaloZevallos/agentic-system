Esta página muestra las clases públicas de los módulos del grupo `scorecard` de `agentic.core.measurement.domain` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo.

- Padre: [Clases de domain](classes-measurement-domain.md)
- Hermanas:
  - [domain: langfuse](classes-measurement-domain-langfuse.md)
  - [domain: reports](classes-measurement-domain-reports.md)

```mermaid
classDiagram
    class evaluation__EvalSet["EvalSet"]
    class evaluation__EvaluationRecord["EvaluationRecord"]
    class evaluation__Score["Score"]
    class evaluation__TaskDocument["TaskDocument"]
    class scorecard__Figure["Figure"]
    class scorecard__Scorecard["Scorecard"]
    class scorecard__ScorecardDocument["ScorecardDocument"]
    class scorecard__TaskFigures["TaskFigures"]
    class scorecard__Verdict["Verdict"]
    scorecard__Scorecard --> scorecard__Figure
```
