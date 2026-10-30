Esta página muestra las clases públicas de los módulos del grupo `playbook` de `agentic.core.process.domain` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo.

- Padre: [Clases de domain](classes-process-domain.md)
- Hermanas:
  - [domain: checks (parte 1)](classes-process-domain-checks-1.md)
  - [domain: checks (parte 2)](classes-process-domain-checks-2.md)
  - [domain: landing (parte 1)](classes-process-domain-landing-1.md)
  - [domain: landing (parte 2)](classes-process-domain-landing-2.md)
  - [domain: machine (parte 1)](classes-process-domain-machine-1.md)
  - [domain: machine (parte 2)](classes-process-domain-machine-2.md)
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
    class playbook__Applied["Applied"]
    class playbook__Archived["Archived"]
    class playbook__Backing["Backing"]
    class playbook__Delta["Delta"]
    class playbook__Evidence["Evidence"]
    class playbook__Item["Item"]
    class playbook__Pending["Pending"]
    class playbook__Playbook["Playbook"]
    class playbook__Refusal["Refusal"]
    class playbook__Rejected["Rejected"]
    class playbook_candidates__Add["Add"]
    class playbook_candidates__Candidate["Candidate"]
    class playbook_candidates__Entry["Entry"]
    class playbook_candidates__Ledger["Ledger"]
    class playbook_candidates__Record["Record"]
    playbook__Applied --> playbook__Playbook
    playbook__Archived --> playbook__Item
    playbook__Refusal --> playbook__Delta
    playbook_candidates__Candidate --> playbook_candidates__Record
```
