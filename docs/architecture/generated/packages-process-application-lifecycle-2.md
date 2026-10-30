Esta página muestra los módulos del grupo `lifecycle` de `application` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `application`, `<nombre> (grupo de domain)` un grupo de `domain` y `<nombre> (paquete)` otro paquete, cada uno con su página. El grupo se dividió por cantidad: esta es la parte 2 de 2, con módulos consecutivos en orden alfabético, y las otras partes aparecen como nodos `<grupo> parte <n> (grupo)`.

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
  - [application: reflection](packages-process-application-reflection.md)
  - [application: support](packages-process-application-support.md)

```mermaid
graph TD
    g_application__build["build (grupo)"]
    g_application__definition["definition (grupo)"]
    g_application__landing["landing (grupo)"]
    g_application__lifecycle__1["lifecycle parte 1 (grupo)"]
    g_application__support["support (grupo)"]
    g_domain__landing["landing (grupo de domain)"]
    g_domain__machine["machine (grupo de domain)"]
    g_domain__state["state (grupo de domain)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    m_application__restore["application.restore"]
    m_application__resume["application.resume"]
    m_application__schedule["application.schedule"]
    m_application__scratch_sweep["application.scratch_sweep"]
    m_application__restore --> g_application__landing
    m_application__restore --> g_application__lifecycle__1
    m_application__restore --> g_application__support
    m_application__restore --> g_domain__landing
    m_application__restore --> g_domain__state
    m_application__restore --> m_application__resume
    m_application__resume --> g_application__build
    m_application__resume --> g_application__definition
    m_application__resume --> g_application__landing
    m_application__resume --> g_application__lifecycle__1
    m_application__resume --> g_application__support
    m_application__resume --> g_domain__landing
    m_application__resume --> g_domain__state
    m_application__resume --> g_pkg__agentic__shared
    m_application__schedule --> g_application__support
    m_application__schedule --> g_domain__state
    m_application__schedule --> g_pkg__agentic__shared
    m_application__scratch_sweep --> g_application__support
    m_application__scratch_sweep --> g_domain__machine
    m_application__scratch_sweep --> g_domain__state
    m_application__scratch_sweep --> g_pkg__agentic__shared
```
