Esta página muestra los módulos del grupo `definition` de `application` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `application`, `<nombre> (grupo de domain)` un grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera. El grupo se dividió por cantidad: esta es la parte 1 de 2, con módulos consecutivos en orden alfabético, y las otras partes aparecen como nodos `<grupo> parte <n> (grupo)`.

- Padre: [application](packages-process-application.md)
- Hermanas:
  - [application: build (parte 1)](packages-process-application-build-1.md)
  - [application: build (parte 2)](packages-process-application-build-2.md)
  - [application: checks](packages-process-application-checks.md)
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
    g_application__definition__2["definition parte 2 (grupo)"]
    g_application__support["support (grupo)"]
    g_domain__checks["checks (grupo de domain)"]
    g_domain__roles["roles (grupo de domain)"]
    g_domain__state["state (grupo de domain)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    m_application__classify["application.classify"]
    m_application__copy["application.copy"]
    m_application__define["application.define"]
    m_application__design["application.design"]
    m_application__diagnose["application.diagnose"]
    m_application__classify --> g_application__support
    m_application__classify --> g_domain__roles
    m_application__classify --> g_domain__state
    m_application__classify --> g_pkg__agentic__shared
    m_application__copy --> g_application__support
    m_application__copy --> g_domain__roles
    m_application__copy --> g_domain__state
    m_application__copy --> g_pkg__agentic__shared
    m_application__define --> g_application__definition__2
    m_application__define --> g_application__support
    m_application__define --> g_domain__roles
    m_application__define --> g_domain__state
    m_application__define --> g_pkg__agentic__shared
    m_application__design --> g_application__support
    m_application__design --> g_domain__roles
    m_application__design --> g_domain__state
    m_application__design --> g_pkg__agentic__shared
    m_application__diagnose --> g_application__checks
    m_application__diagnose --> g_application__support
    m_application__diagnose --> g_domain__checks
    m_application__diagnose --> g_domain__roles
    m_application__diagnose --> g_domain__state
    m_application__diagnose --> g_pkg__agentic__shared
```
