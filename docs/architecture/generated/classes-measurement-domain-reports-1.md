Esta página muestra las clases públicas de los módulos del grupo `reports` de `agentic.core.measurement.domain` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 1 de 2, con las clases en orden alfabético por módulo y clase.

- Padre: [Clases de domain](classes-measurement-domain.md)
- Hermanas:
  - [domain: langfuse](classes-measurement-domain-langfuse.md)
  - [domain: reports (parte 2)](classes-measurement-domain-reports-2.md)
  - [domain: scorecard (parte 1)](classes-measurement-domain-scorecard-1.md)
  - [domain: scorecard (parte 2)](classes-measurement-domain-scorecard-2.md)

```mermaid
classDiagram
    class findings_report__Figures["Figures"]
    class findings_report__FindingLine["FindingLine"]
    class findings_report__FindingsDocument["FindingsDocument"]
    class findings_report__FindingsReport["FindingsReport"]
    class issue_listing__ListedIssue["ListedIssue"]
    class issue_listing__Missing["Missing"]
    class issue_listing__PageRecord["PageRecord"]
    class issue_listing__TaskTimes["TaskTimes"]
    class planning__PlanningFigures["PlanningFigures"]
    class planning__TaskPlanning["TaskPlanning"]
    findings_report__FindingsReport --> findings_report__Figures
```
