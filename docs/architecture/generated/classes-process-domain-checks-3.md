Esta página muestra las clases públicas de los módulos del grupo `checks` de `agentic.core.process.domain` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 3 de 3, con las clases en orden alfabético por módulo y clase.

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
  - [domain: roles (parte 3)](classes-process-domain-roles-3.md)
  - [domain: state (parte 1)](classes-process-domain-state-1.md)
  - [domain: state (parte 2)](classes-process-domain-state-2.md)
  - [domain: state (parte 3)](classes-process-domain-state-3.md)
  - [domain: state (parte 4)](classes-process-domain-state-4.md)
  - [domain: state (parte 5)](classes-process-domain-state-5.md)

```mermaid
classDiagram
    class presubmit__BudgetBreach["BudgetBreach"]
    class presubmit__CheckResult["CheckResult"]
    class presubmit__RerunForm["RerunForm"]
    class presubmit__WitnessResult["WitnessResult"]
    class run_check__CheckRun["CheckRun"]
    class run_check__Outcome["Outcome"]
    class run_check__Verification["Verification"]
    class run_check__VerificationCall["VerificationCall"]
    class run_check__WallOverCeiling["WallOverCeiling"]
    class suppressions__Forbidden["Forbidden"]
    run_check__VerificationCall --> run_check__Outcome
```
