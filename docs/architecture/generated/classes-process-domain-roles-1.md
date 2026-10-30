Esta página muestra las clases públicas de los módulos del grupo `roles` de `agentic.core.process.domain` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 1 de 3, con las clases en orden alfabético por módulo y clase.

- Padre: [Clases de domain](classes-process-domain.md)
- Hermanas:
  - [domain: checks (parte 1)](classes-process-domain-checks-1.md)
  - [domain: checks (parte 2)](classes-process-domain-checks-2.md)
  - [domain: landing (parte 1)](classes-process-domain-landing-1.md)
  - [domain: landing (parte 2)](classes-process-domain-landing-2.md)
  - [domain: machine (parte 1)](classes-process-domain-machine-1.md)
  - [domain: machine (parte 2)](classes-process-domain-machine-2.md)
  - [domain: playbook](classes-process-domain-playbook.md)
  - [domain: roles (parte 2)](classes-process-domain-roles-2.md)
  - [domain: roles (parte 3)](classes-process-domain-roles-3.md)
  - [domain: state (parte 1)](classes-process-domain-state-1.md)
  - [domain: state (parte 2)](classes-process-domain-state-2.md)
  - [domain: state (parte 3)](classes-process-domain-state-3.md)
  - [domain: state (parte 4)](classes-process-domain-state-4.md)
  - [domain: state (parte 5)](classes-process-domain-state-5.md)

```mermaid
classDiagram
    class design__Design["Design"]
    class docs__DiagramBudget["DiagramBudget"]
    class docs__Docs["Docs"]
    class roles__Interview["Interview"]
    class roles__Pair["Pair"]
    class roles__Prompt["Prompt"]
    class roles__Role["Role"]
    class schemas__Answers["Answers"]
    class schemas__CapabilityFile["CapabilityFile"]
    class schemas__Classification["Classification"]
    class schemas__Consolidation["Consolidation"]
    class schemas__Consultation["Consultation"]
    class schemas__CopyDraft["CopyDraft"]
    class schemas__CopyFinding["CopyFinding"]
    class schemas__CopyReview["CopyReview"]
    roles__Interview --> roles__Role
    roles__Pair --> roles__Role
```

## Relaciones con otras páginas

- [domain: roles (parte 3)](classes-process-domain-roles-3.md)
  - `schemas.Answers` hereda de `schemas.RoleOutput`
  - `schemas.Classification` hereda de `schemas.RoleOutput`
  - `schemas.Consolidation` hereda de `schemas.RoleOutput`
  - `schemas.Consultation` hereda de `schemas.RoleOutput`
  - `schemas.CopyDraft` hereda de `schemas.RoleOutput`
  - `schemas.CopyReview` hereda de `schemas.RoleOutput`
