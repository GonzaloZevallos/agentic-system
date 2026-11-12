Esta página muestra los módulos del grupo `support` de `application` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `application`, `<nombre> (grupo de domain)` un grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera. El grupo se dividió por cantidad: esta es la parte 1 de 2, con módulos consecutivos en orden alfabético, y las otras partes aparecen como nodos `<grupo> parte <n> (grupo)`.

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
  - [application: support (parte 2)](packages-process-application-support-2.md)

```mermaid
graph TD
    g_application__landing["landing (grupo)"]
    g_application__lifecycle["lifecycle (grupo)"]
    g_application__support__2["support parte 2 (grupo)"]
    g_domain__checks["checks (grupo de domain)"]
    g_domain__landing["landing (grupo de domain)"]
    g_domain__roles["roles (grupo de domain)"]
    g_domain__state["state (grupo de domain)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    m_application__api_break["application.api_break"]
    m_application__checkouts["application.checkouts"]
    m_application__ci_push["application.ci_push"]
    m_application__ci_trust["application.ci_trust"]
    m_application__docs_settlement["application.docs_settlement"]
    m_application__landing_body["application.landing_body"]
    m_application__api_break --> g_application__support__2
    m_application__api_break --> g_domain__landing
    m_application__checkouts --> g_application__support__2
    m_application__checkouts --> g_domain__checks
    m_application__checkouts --> g_domain__roles
    m_application__checkouts --> g_domain__state
    m_application__checkouts --> g_pkg__agentic__shared
    m_application__ci_push --> g_application__support__2
    m_application__ci_push --> g_domain__checks
    m_application__ci_trust --> g_application__support__2
    m_application__ci_trust --> g_domain__checks
    m_application__ci_trust --> g_domain__state
    m_application__docs_settlement --> g_application__support__2
    m_application__docs_settlement --> g_domain__roles
    m_application__landing_body --> g_application__landing
    m_application__landing_body --> g_application__lifecycle
    m_application__landing_body --> g_domain__landing
    m_application__landing_body --> g_domain__state
    m_application__landing_body --> m_application__api_break
    m_application__landing_body --> m_application__checkouts
```
