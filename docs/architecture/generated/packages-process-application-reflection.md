Esta página muestra los módulos del grupo `reflection` de `application` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `application`, `<nombre> (grupo de domain)` un grupo de `domain` y `<nombre> (paquete)` otro paquete, cada uno con su página.

- Padre: [application](packages-process-application.md)
- Hermanas:
  - [application: build (parte 1)](packages-process-application-build-1.md)
  - [application: build (parte 2)](packages-process-application-build-2.md)
  - [application: checks](packages-process-application-checks.md)
  - [application: definition (parte 1)](packages-process-application-definition-1.md)
  - [application: definition (parte 2)](packages-process-application-definition-2.md)
  - [application: landing (parte 1)](packages-process-application-landing-1.md)
  - [application: landing (parte 2)](packages-process-application-landing-2.md)
  - [application: lifecycle (parte 1)](packages-process-application-lifecycle-1.md)
  - [application: lifecycle (parte 2)](packages-process-application-lifecycle-2.md)
  - [application: support](packages-process-application-support.md)

```mermaid
graph TD
    g_application__definition["definition (grupo)"]
    g_application__landing["landing (grupo)"]
    g_application__support["support (grupo)"]
    g_domain__machine["machine (grupo de domain)"]
    g_domain__playbook["playbook (grupo de domain)"]
    g_domain__roles["roles (grupo de domain)"]
    g_domain__state["state (grupo de domain)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    m_application__owner_rules["application.owner_rules"]
    m_application__reflect["application.reflect"]
    m_application__reflect_run["application.reflect_run"]
    m_application__stack_review["application.stack_review"]
    m_application__owner_rules --> g_application__support
    m_application__owner_rules --> g_domain__roles
    m_application__owner_rules --> g_domain__state
    m_application__owner_rules --> g_pkg__agentic__shared
    m_application__reflect --> g_application__support
    m_application__reflect --> g_domain__playbook
    m_application__reflect --> g_domain__roles
    m_application__reflect --> g_domain__state
    m_application__reflect --> g_pkg__agentic__shared
    m_application__reflect_run --> g_application__landing
    m_application__reflect_run --> g_application__support
    m_application__reflect_run --> g_domain__machine
    m_application__reflect_run --> g_domain__playbook
    m_application__reflect_run --> g_domain__state
    m_application__reflect_run --> g_pkg__agentic__shared
    m_application__reflect_run --> m_application__reflect
    m_application__stack_review --> g_application__definition
    m_application__stack_review --> g_application__support
    m_application__stack_review --> g_domain__roles
    m_application__stack_review --> g_domain__state
    m_application__stack_review --> g_pkg__agentic__shared
```
