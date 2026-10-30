Esta página muestra los módulos del grupo `support` de `application` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `application`, `<nombre> (grupo de domain)` un grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera (por ejemplo `agentic.shared`). Los imports hacia `domain` se resumen en un solo nodo `domain (paquete)` porque dibujar un nodo por grupo no cabe en el presupuesto de diagramas.

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

```mermaid
graph TD
    g_application__checks["checks (grupo)"]
    g_application__landing["landing (grupo)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    g_pkg__domain["domain (paquete)"]
    m_application__checkouts["application.checkouts"]
    m_application__limits["application.limits"]
    m_application__ports["application.ports"]
    m_application__preflight["application.preflight"]
    m_application__release["application.release"]
    m_application__run_role["application.run_role"]
    m_application__checkouts --> g_pkg__agentic__shared
    m_application__checkouts --> g_pkg__domain
    m_application__checkouts --> m_application__ports
    m_application__checkouts --> m_application__release
    m_application__ports --> g_pkg__domain
    m_application__ports --> m_application__limits
    m_application__preflight --> g_pkg__agentic__shared
    m_application__preflight --> g_pkg__domain
    m_application__preflight --> m_application__checkouts
    m_application__preflight --> m_application__ports
    m_application__preflight --> m_application__run_role
    m_application__release --> g_pkg__domain
    m_application__release --> m_application__ports
    m_application__run_role --> g_application__checks
    m_application__run_role --> g_application__landing
    m_application__run_role --> g_pkg__agentic__shared
    m_application__run_role --> g_pkg__domain
    m_application__run_role --> m_application__limits
    m_application__run_role --> m_application__ports
```
