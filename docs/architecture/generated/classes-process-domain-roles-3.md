Esta página muestra las clases públicas de los módulos del grupo `roles` de `agentic.core.process.domain` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 3 de 3, con las clases en orden alfabético por módulo y clase.

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
  - [domain: roles (parte 2)](classes-process-domain-roles-2.md)
  - [domain: state (parte 1)](classes-process-domain-state-1.md)
  - [domain: state (parte 2)](classes-process-domain-state-2.md)
  - [domain: state (parte 3)](classes-process-domain-state-3.md)
  - [domain: state (parte 4)](classes-process-domain-state-4.md)
  - [domain: state (parte 5)](classes-process-domain-state-5.md)

```mermaid
classDiagram
    class schemas__PerfBudget["PerfBudget"]
    class schemas__PerfScenario["PerfScenario"]
    class schemas__PlatformAnswers["PlatformAnswers"]
    class schemas__Reflection["Reflection"]
    class schemas__Review["Review"]
    class schemas__RoleOutput["RoleOutput"]
    class schemas__Ruling["Ruling"]
    class schemas__SolutionTurn["SolutionTurn"]
    class schemas__StackProposal["StackProposal"]
    class schemas__Subtask["Subtask"]
    class schemas__TechAnswers["TechAnswers"]
    class schemas__TechPlan["TechPlan"]
    class schemas__Verdict["Verdict"]
    class stack_signals__Signals["Signals"]
    schemas__PlatformAnswers --|> schemas__RoleOutput
    schemas__Reflection --|> schemas__RoleOutput
    schemas__Review --|> schemas__RoleOutput
    schemas__Ruling --|> schemas__RoleOutput
    schemas__StackProposal --|> schemas__RoleOutput
    schemas__TechAnswers --|> schemas__RoleOutput
    schemas__TechPlan --|> schemas__RoleOutput
    schemas__Verdict --|> schemas__RoleOutput
```

## Relaciones con otras páginas

- [domain: roles (parte 1)](classes-process-domain-roles-1.md)
  - `schemas.Answers` hereda de `schemas.RoleOutput`
  - `schemas.Classification` hereda de `schemas.RoleOutput`
  - `schemas.Consolidation` hereda de `schemas.RoleOutput`
  - `schemas.Consultation` hereda de `schemas.RoleOutput`
  - `schemas.CopyDraft` hereda de `schemas.RoleOutput`
  - `schemas.CopyReview` hereda de `schemas.RoleOutput`
- [domain: roles (parte 2)](classes-process-domain-roles-2.md)
  - `schemas.Delivery` hereda de `schemas.RoleOutput`
  - `schemas.DesignDraft` hereda de `schemas.RoleOutput`
  - `schemas.DesignReview` hereda de `schemas.RoleOutput`
  - `schemas.Diagnosis` hereda de `schemas.RoleOutput`
  - `schemas.FinalValidation` hereda de `schemas.RoleOutput`
  - `schemas.InterviewTurn` hereda de `schemas.RoleOutput`
  - `schemas.LandingDiagnosis` hereda de `schemas.RoleOutput`
  - `schemas.PRD` hereda de `schemas.RoleOutput`
  - `schemas.SolutionTurn` hereda de `schemas.InterviewTurn`
