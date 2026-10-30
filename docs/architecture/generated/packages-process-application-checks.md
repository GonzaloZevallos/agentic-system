Esta página muestra los módulos del grupo `checks` de `application` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `application`, `<nombre> (grupo de domain)` un grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera (por ejemplo `agentic.shared`).

- Padre: [application](packages-process-application.md)
- Hermanas:
  - [application: build (parte 1)](packages-process-application-build-1.md)
  - [application: build (parte 2)](packages-process-application-build-2.md)
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
    g_application__lifecycle["lifecycle (grupo)"]
    g_application__support["support (grupo)"]
    g_domain__checks["checks (grupo de domain)"]
    g_domain__roles["roles (grupo de domain)"]
    g_domain__state["state (grupo de domain)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    m_application__ci_ref_sweep["application.ci_ref_sweep"]
    m_application__ci_review["application.ci_review"]
    m_application__coverage_guard["application.coverage_guard"]
    m_application__full_checks["application.full_checks"]
    m_application__perf["application.perf"]
    m_application__presubmit["application.presubmit"]
    m_application__run_check["application.run_check"]
    m_application__schema_guard["application.schema_guard"]
    m_application__ci_ref_sweep --> g_application__lifecycle
    m_application__ci_ref_sweep --> g_application__support
    m_application__ci_ref_sweep --> g_domain__state
    m_application__ci_review --> g_application__support
    m_application__ci_review --> g_domain__checks
    m_application__ci_review --> g_domain__state
    m_application__coverage_guard --> g_application__support
    m_application__coverage_guard --> g_domain__checks
    m_application__full_checks --> g_application__support
    m_application__full_checks --> g_domain__checks
    m_application__full_checks --> g_domain__roles
    m_application__full_checks --> g_domain__state
    m_application__full_checks --> g_pkg__agentic__shared
    m_application__full_checks --> m_application__presubmit
    m_application__perf --> g_application__support
    m_application__perf --> g_domain__checks
    m_application__perf --> g_pkg__agentic__shared
    m_application__presubmit --> g_application__support
    m_application__presubmit --> g_domain__checks
    m_application__presubmit --> g_domain__state
    m_application__presubmit --> g_pkg__agentic__shared
    m_application__run_check --> g_domain__checks
    m_application__run_check --> m_application__presubmit
    m_application__schema_guard --> g_domain__roles
```
