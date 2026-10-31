Esta página muestra las clases públicas de los módulos del grupo `checks` de `agentic.core.process.domain` y sus relaciones (herencia, composición, agregación y asociación); las relaciones con clases de otras páginas no se dibujan y se listan debajo del diagrama. Cada clase se identifica por su módulo y su nombre: si dos clases de la página se llaman igual, la etiqueta incluye el módulo. El grupo se dividió por cantidad: esta es la parte 1 de 3, con las clases en orden alfabético por módulo y clase.

- Padre: [Clases de domain](classes-process-domain.md)
- Hermanas:
  - [domain: checks (parte 2)](classes-process-domain-checks-2.md)
  - [domain: checks (parte 3)](classes-process-domain-checks-3.md)
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
    class check_schedule__CheckPriority["CheckPriority"]
    class check_schedule__Decision["Decision"]
    class check_schedule__Entry["check_schedule.Entry"]
    class check_schedule__Limits["Limits"]
    class check_schedule__Request["Request"]
    class ci__CiCheck["CiCheck"]
    class ci__CiReport["CiReport"]
    class ci__CiUnreadable["CiUnreadable"]
    class ci__CiVerdict["CiVerdict"]
    class ci__FullTierVerdict["FullTierVerdict"]
    class ci__JobLog["JobLog"]
    class coverage__Entry["coverage.Entry"]
    check_schedule__Entry --> check_schedule__CheckPriority
    check_schedule__Request --> check_schedule__CheckPriority
```
