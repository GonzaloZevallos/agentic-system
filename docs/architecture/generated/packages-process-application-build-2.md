Esta página muestra los módulos del grupo `build` de `application` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `application`, `<nombre> (grupo de domain)` un grupo de `domain` y `<nombre> (paquete)` otro paquete, cada uno con su página. El grupo se dividió por cantidad: esta es la parte 2 de 2, con módulos consecutivos en orden alfabético, y las otras partes aparecen como nodos `<grupo> parte <n> (grupo)`.

- Padre: [application](packages-process-application.md)
- Hermanas:
  - [application: build (parte 1)](packages-process-application-build-1.md)
  - [application: checks](packages-process-application-checks.md)
  - [application: definition (parte 1)](packages-process-application-definition-1.md)
  - [application: definition (parte 2)](packages-process-application-definition-2.md)
  - [application: landing (parte 1)](packages-process-application-landing-1.md)
  - [application: landing (parte 2)](packages-process-application-landing-2.md)
  - [application: lifecycle (parte 1)](packages-process-application-lifecycle-1.md)
  - [application: lifecycle (parte 2)](packages-process-application-lifecycle-2.md)
  - [application: reflection](packages-process-application-reflection.md)
  - [application: support](packages-process-application-support.md)

```mermaid
graph TD
    g_application__checks["checks (grupo)"]
    g_application__definition["definition (grupo)"]
    g_application__landing["landing (grupo)"]
    g_application__reflection["reflection (grupo)"]
    g_application__support["support (grupo)"]
    g_domain__checks["checks (grupo de domain)"]
    g_domain__landing["landing (grupo de domain)"]
    g_domain__roles["roles (grupo de domain)"]
    g_domain__state["state (grupo de domain)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    m_application__plan["application.plan"]
    m_application__pre_pr_fix["application.pre_pr_fix"]
    m_application__review["application.review"]
    m_application__split["application.split"]
    m_application__tracker_only["application.tracker_only"]
    m_application__plan --> g_application__definition
    m_application__plan --> g_application__reflection
    m_application__plan --> g_application__support
    m_application__plan --> g_domain__landing
    m_application__plan --> g_domain__roles
    m_application__plan --> g_domain__state
    m_application__plan --> g_pkg__agentic__shared
    m_application__pre_pr_fix --> g_application__landing
    m_application__pre_pr_fix --> g_application__support
    m_application__pre_pr_fix --> g_domain__landing
    m_application__pre_pr_fix --> g_domain__state
    m_application__pre_pr_fix --> g_pkg__agentic__shared
    m_application__review --> g_application__checks
    m_application__review --> g_application__support
    m_application__review --> g_domain__checks
    m_application__review --> g_domain__roles
    m_application__review --> g_domain__state
    m_application__review --> g_pkg__agentic__shared
    m_application__split --> g_application__support
    m_application__split --> g_domain__roles
    m_application__split --> g_domain__state
    m_application__split --> g_pkg__agentic__shared
    m_application__split --> m_application__tracker_only
    m_application__tracker_only --> g_application__support
    m_application__tracker_only --> g_domain__roles
    m_application__tracker_only --> g_domain__state
    m_application__tracker_only --> g_pkg__agentic__shared
```
