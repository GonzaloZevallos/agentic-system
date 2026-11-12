Esta página muestra los módulos del grupo `support` de `application` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `application`, `<nombre> (grupo de domain)` un grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera. El grupo se dividió por cantidad: esta es la parte 2 de 2, con módulos consecutivos en orden alfabético, y las otras partes aparecen como nodos `<grupo> parte <n> (grupo)`.

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
  - [application: reflection](packages-process-application-reflection.md)
  - [application: support (parte 1)](packages-process-application-support-1.md)

```mermaid
graph TD
    g_application__checks["checks (grupo)"]
    g_application__landing["landing (grupo)"]
    g_application__support__1["support parte 1 (grupo)"]
    g_domain__checks["checks (grupo de domain)"]
    g_domain__landing["landing (grupo de domain)"]
    g_domain__machine["machine (grupo de domain)"]
    g_domain__playbook["playbook (grupo de domain)"]
    g_domain__roles["roles (grupo de domain)"]
    g_domain__state["state (grupo de domain)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    m_application__limits["application.limits"]
    m_application__ports["application.ports"]
    m_application__preflight["application.preflight"]
    m_application__release["application.release"]
    m_application__run_role["application.run_role"]
    m_application__ports --> g_domain__checks
    m_application__ports --> g_domain__landing
    m_application__ports --> g_domain__machine
    m_application__ports --> g_domain__playbook
    m_application__ports --> g_domain__roles
    m_application__ports --> g_domain__state
    m_application__ports --> m_application__limits
    m_application__preflight --> g_application__support__1
    m_application__preflight --> g_domain__checks
    m_application__preflight --> g_domain__roles
    m_application__preflight --> g_domain__state
    m_application__preflight --> g_pkg__agentic__shared
    m_application__preflight --> m_application__ports
    m_application__preflight --> m_application__run_role
    m_application__release --> g_domain__machine
    m_application__release --> m_application__ports
    m_application__run_role --> g_application__checks
    m_application__run_role --> g_application__landing
    m_application__run_role --> g_domain__checks
    m_application__run_role --> g_domain__landing
    m_application__run_role --> g_domain__machine
    m_application__run_role --> g_domain__playbook
    m_application__run_role --> g_domain__roles
    m_application__run_role --> g_domain__state
    m_application__run_role --> g_pkg__agentic__shared
    m_application__run_role --> m_application__limits
    m_application__run_role --> m_application__ports
```
