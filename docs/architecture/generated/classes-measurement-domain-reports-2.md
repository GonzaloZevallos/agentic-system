Esta página muestra las clases públicas de los módulos del grupo `reports` de `agentic.core.measurement.domain` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 2 de 2, con las clases en orden alfabético por módulo y clase.

- Padre: [Clases de domain](classes-measurement-domain.md)
- Hermanas:
  - [domain: langfuse](classes-measurement-domain-langfuse.md)
  - [domain: reports (parte 1)](classes-measurement-domain-reports-1.md)
  - [domain: scorecard (parte 1)](classes-measurement-domain-scorecard-1.md)
  - [domain: scorecard (parte 2)](classes-measurement-domain-scorecard-2.md)

```mermaid
classDiagram
    class playbook_window__Segment["Segment"]
    class role_usage__RoleUsage["RoleUsage"]
    class shadow_report__Buckets["Buckets"]
    class shadow_report__Figures["Figures"]
    class shadow_report__ShadowReport["ShadowReport"]
    class shadow_report__TaskShadow["TaskShadow"]
    class stage_report__StageRow["StageRow"]
    class stage_report__Unread["Unread"]
    shadow_report__Figures --> shadow_report__Buckets
    shadow_report__ShadowReport --> shadow_report__Figures
    shadow_report__TaskShadow --> shadow_report__Figures
```
