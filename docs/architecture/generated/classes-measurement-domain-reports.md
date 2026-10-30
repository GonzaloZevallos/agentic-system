Esta página muestra las clases públicas de los módulos del grupo `reports` de `agentic.core.measurement.domain` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo.

- Padre: [Clases de domain](classes-measurement-domain.md)
- Hermanas:
  - [domain: langfuse](classes-measurement-domain-langfuse.md)
  - [domain: scorecard](classes-measurement-domain-scorecard.md)

```mermaid
classDiagram
    class findings_report__Figures["Figures"]
    class findings_report__FindingLine["FindingLine"]
    class findings_report__FindingsDocument["FindingsDocument"]
    class findings_report__FindingsReport["FindingsReport"]
    class playbook_window__CheckProposal["CheckProposal"]
    class playbook_window__Comparison["Comparison"]
    class playbook_window__Segment["Segment"]
    class role_usage__RoleUsage["RoleUsage"]
    findings_report__FindingsReport --> findings_report__Figures
```
