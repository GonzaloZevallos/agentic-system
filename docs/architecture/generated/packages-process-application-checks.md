Esta página muestra los módulos del grupo `checks` de `application` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `application`, `<nombre> (grupo de domain)` un grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera. Los imports hacia `domain` se resumen en un solo nodo `domain (paquete)` porque dibujar un nodo por grupo no cabe en el presupuesto de diagramas.

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
    g_application__build["build (grupo)"]
    g_application__definition["definition (grupo)"]
    g_application__lifecycle["lifecycle (grupo)"]
    g_application__support["support (grupo)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    g_pkg__domain["domain (paquete)"]
    m_application__ci_flakes["application.ci_flakes"]
    m_application__ci_ref_sweep["application.ci_ref_sweep"]
    m_application__ci_review["application.ci_review"]
    m_application__coverage_guard["application.coverage_guard"]
    m_application__full_checks["application.full_checks"]
    m_application__perf["application.perf"]
    m_application__presubmit["application.presubmit"]
    m_application__run_check["application.run_check"]
    m_application__schema_guard["application.schema_guard"]
    m_application__verifiers["application.verifiers"]
    m_application__ci_flakes --> g_application__support
    m_application__ci_flakes --> g_pkg__domain
    m_application__ci_ref_sweep --> g_application__lifecycle
    m_application__ci_ref_sweep --> g_application__support
    m_application__ci_ref_sweep --> g_pkg__domain
    m_application__ci_review --> g_application__support
    m_application__ci_review --> g_pkg__domain
    m_application__coverage_guard --> g_application__support
    m_application__coverage_guard --> g_pkg__domain
    m_application__full_checks --> g_application__support
    m_application__full_checks --> g_pkg__agentic__shared
    m_application__full_checks --> g_pkg__domain
    m_application__full_checks --> m_application__ci_flakes
    m_application__full_checks --> m_application__presubmit
    m_application__perf --> g_application__support
    m_application__perf --> g_pkg__agentic__shared
    m_application__perf --> g_pkg__domain
    m_application__presubmit --> g_application__build
    m_application__presubmit --> g_application__support
    m_application__presubmit --> g_pkg__agentic__shared
    m_application__presubmit --> g_pkg__domain
    m_application__run_check --> g_pkg__domain
    m_application__run_check --> m_application__presubmit
    m_application__schema_guard --> g_pkg__domain
    m_application__verifiers --> g_application__definition
    m_application__verifiers --> g_application__support
    m_application__verifiers --> g_pkg__agentic__shared
    m_application__verifiers --> g_pkg__domain
    m_application__verifiers --> m_application__full_checks
```
