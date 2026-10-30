Esta página muestra las clases públicas de los módulos del grupo `roles` de `agentic.core.process.domain` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 2 de 3, con las clases en orden alfabético por módulo y clase.

- Padre: [Clases de domain](classes-process-domain.md)
- Hermanas:
  - [domain: checks (parte 1)](classes-process-domain-checks-1.md)
  - [domain: checks (parte 2)](classes-process-domain-checks-2.md)
  - [domain: landing (parte 1)](classes-process-domain-landing-1.md)
  - [domain: landing (parte 2)](classes-process-domain-landing-2.md)
  - [domain: machine (parte 1)](classes-process-domain-machine-1.md)
  - [domain: machine (parte 2)](classes-process-domain-machine-2.md)
  - [domain: playbook](classes-process-domain-playbook.md)
  - [domain: roles (parte 1)](classes-process-domain-roles-1.md)
  - [domain: roles (parte 3)](classes-process-domain-roles-3.md)
  - [domain: state (parte 1)](classes-process-domain-state-1.md)
  - [domain: state (parte 2)](classes-process-domain-state-2.md)
  - [domain: state (parte 3)](classes-process-domain-state-3.md)
  - [domain: state (parte 4)](classes-process-domain-state-4.md)
  - [domain: state (parte 5)](classes-process-domain-state-5.md)

```mermaid
classDiagram
    class schemas__Delivery["Delivery"]
    class schemas__DesignDraft["DesignDraft"]
    class schemas__DesignItem["DesignItem"]
    class schemas__DesignReview["DesignReview"]
    class schemas__Diagnosis["Diagnosis"]
    class schemas__FinalValidation["FinalValidation"]
    class schemas__Finding["Finding"]
    class schemas__FitBucket["FitBucket"]
    class schemas__FitDecision["FitDecision"]
    class schemas__GlossaryTerm["GlossaryTerm"]
    class schemas__InterviewTurn["InterviewTurn"]
    class schemas__LandingDiagnosis["LandingDiagnosis"]
    class schemas__LessonDelta["LessonDelta"]
    class schemas__PRD["PRD"]
    class schemas__Part["Part"]
```

## Relaciones con otras páginas

- [domain: roles (parte 3)](classes-process-domain-roles-3.md)
  - `schemas.Delivery` hereda de `schemas.RoleOutput`
  - `schemas.DesignDraft` hereda de `schemas.RoleOutput`
  - `schemas.DesignReview` hereda de `schemas.RoleOutput`
  - `schemas.Diagnosis` hereda de `schemas.RoleOutput`
  - `schemas.FinalValidation` hereda de `schemas.RoleOutput`
  - `schemas.InterviewTurn` hereda de `schemas.RoleOutput`
  - `schemas.LandingDiagnosis` hereda de `schemas.RoleOutput`
  - `schemas.PRD` hereda de `schemas.RoleOutput`
  - `schemas.SolutionTurn` hereda de `schemas.InterviewTurn`
