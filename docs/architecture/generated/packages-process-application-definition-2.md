Esta página muestra los módulos del grupo `definition` de `application` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `application`, `<nombre> (grupo de domain)` un grupo de `domain` y `<nombre> (paquete)` otro paquete, cada uno con su página. El grupo se dividió por cantidad: esta es la parte 2 de 2, con módulos consecutivos en orden alfabético, y las otras partes aparecen como nodos `<grupo> parte <n> (grupo)`.

- Padre: [application](packages-process-application.md)
- Hermanas:
  - [application: build (parte 1)](packages-process-application-build-1.md)
  - [application: build (parte 2)](packages-process-application-build-2.md)
  - [application: checks](packages-process-application-checks.md)
  - [application: definition (parte 1)](packages-process-application-definition-1.md)
  - [application: landing (parte 1)](packages-process-application-landing-1.md)
  - [application: landing (parte 2)](packages-process-application-landing-2.md)
  - [application: lifecycle (parte 1)](packages-process-application-lifecycle-1.md)
  - [application: lifecycle (parte 2)](packages-process-application-lifecycle-2.md)
  - [application: reflection](packages-process-application-reflection.md)
  - [application: support](packages-process-application-support.md)

```mermaid
graph TD
    g_application__checks["checks (grupo)"]
    g_application__support["support (grupo)"]
    g_domain__checks["checks (grupo de domain)"]
    g_domain__landing["landing (grupo de domain)"]
    g_domain__roles["roles (grupo de domain)"]
    g_domain__state["state (grupo de domain)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    m_application__discard["application.discard"]
    m_application__final_validation["application.final_validation"]
    m_application__interview["application.interview"]
    m_application__qa_performance["application.qa_performance"]
    m_application__validate_prd["application.validate_prd"]
    m_application__discard --> g_application__support
    m_application__discard --> g_domain__roles
    m_application__discard --> g_domain__state
    m_application__discard --> g_pkg__agentic__shared
    m_application__final_validation --> g_application__support
    m_application__final_validation --> g_domain__landing
    m_application__final_validation --> g_domain__roles
    m_application__final_validation --> g_domain__state
    m_application__final_validation --> g_pkg__agentic__shared
    m_application__interview --> g_application__support
    m_application__interview --> g_domain__roles
    m_application__interview --> g_domain__state
    m_application__interview --> g_pkg__agentic__shared
    m_application__qa_performance --> g_application__checks
    m_application__qa_performance --> g_application__support
    m_application__qa_performance --> g_domain__checks
    m_application__qa_performance --> g_domain__roles
    m_application__qa_performance --> g_domain__state
    m_application__qa_performance --> g_pkg__agentic__shared
    m_application__validate_prd --> g_application__support
    m_application__validate_prd --> g_domain__roles
    m_application__validate_prd --> g_domain__state
    m_application__validate_prd --> g_pkg__agentic__shared
```
