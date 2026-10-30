Esta página muestra los módulos del grupo `build` de `application` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `application`, `<nombre> (grupo de domain)` un grupo de `domain` y `<nombre> (paquete)` otro paquete, cada uno con su página. El grupo se dividió por cantidad: esta es la parte 1 de 2, con módulos consecutivos en orden alfabético, y las otras partes aparecen como nodos `<grupo> parte <n> (grupo)`.

- Padre: [application](packages-process-application.md)
- Hermanas:
  - [application: build (parte 2)](packages-process-application-build-2.md)
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
    g_application__build__2["build parte 2 (grupo)"]
    g_application__checks["checks (grupo)"]
    g_application__support["support (grupo)"]
    g_domain__checks["checks (grupo de domain)"]
    g_domain__landing["landing (grupo de domain)"]
    g_domain__roles["roles (grupo de domain)"]
    g_domain__state["state (grupo de domain)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    m_application__deliver["application.deliver"]
    m_application__delivered["application.delivered"]
    m_application__develop["application.develop"]
    m_application__escalate["application.escalate"]
    m_application__integrate["application.integrate"]
    m_application__deliver --> g_application__build__2
    m_application__deliver --> g_application__support
    m_application__deliver --> g_domain__checks
    m_application__deliver --> g_domain__landing
    m_application__deliver --> g_domain__roles
    m_application__deliver --> g_domain__state
    m_application__deliver --> g_pkg__agentic__shared
    m_application__delivered --> g_application__support
    m_application__delivered --> g_domain__state
    m_application__develop --> g_application__checks
    m_application__develop --> g_application__support
    m_application__develop --> g_domain__checks
    m_application__develop --> g_domain__roles
    m_application__develop --> g_domain__state
    m_application__develop --> g_pkg__agentic__shared
    m_application__escalate --> g_application__support
    m_application__escalate --> g_domain__state
    m_application__escalate --> g_pkg__agentic__shared
    m_application__integrate --> g_application__build__2
    m_application__integrate --> g_application__support
    m_application__integrate --> g_domain__roles
    m_application__integrate --> g_domain__state
    m_application__integrate --> g_pkg__agentic__shared
```
