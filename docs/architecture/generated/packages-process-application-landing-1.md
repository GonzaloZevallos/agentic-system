Esta página muestra los módulos del grupo `landing` de `application` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `application`, `<nombre> (grupo de domain)` un grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera. Los imports hacia `domain` se resumen en un solo nodo `domain (paquete)` porque dibujar un nodo por grupo no cabe en el presupuesto de diagramas. El grupo se dividió por cantidad: esta es la parte 1 de 2, con módulos consecutivos en orden alfabético, y las otras partes aparecen como nodos `<grupo> parte <n> (grupo)`.

- Padre: [application](packages-process-application.md)
- Hermanas:
  - [application: build (parte 1)](packages-process-application-build-1.md)
  - [application: build (parte 2)](packages-process-application-build-2.md)
  - [application: checks (parte 1)](packages-process-application-checks-1.md)
  - [application: checks (parte 2)](packages-process-application-checks-2.md)
  - [application: definition (parte 1)](packages-process-application-definition-1.md)
  - [application: definition (parte 2)](packages-process-application-definition-2.md)
  - [application: landing (parte 2)](packages-process-application-landing-2.md)
  - [application: lifecycle (parte 1)](packages-process-application-lifecycle-1.md)
  - [application: lifecycle (parte 2)](packages-process-application-lifecycle-2.md)
  - [application: reflection](packages-process-application-reflection.md)
  - [application: support](packages-process-application-support.md)

```mermaid
graph TD
    g_application__build["build (grupo)"]
    g_application__checks["checks (grupo)"]
    g_application__landing__2["landing parte 2 (grupo)"]
    g_application__lifecycle["lifecycle (grupo)"]
    g_application__support["support (grupo)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    g_pkg__domain["domain (paquete)"]
    m_application__adr_collision["application.adr_collision"]
    m_application__close_issues["application.close_issues"]
    m_application__closed["application.closed"]
    m_application__landing["application.landing"]
    m_application__landing_adr["application.landing_adr"]
    m_application__landing_fix["application.landing_fix"]
    m_application__adr_collision --> g_application__support
    m_application__adr_collision --> g_pkg__domain
    m_application__close_issues --> g_application__support
    m_application__close_issues --> g_pkg__domain
    m_application__closed --> g_application__support
    m_application__landing --> g_application__build
    m_application__landing --> g_application__landing__2
    m_application__landing --> g_application__lifecycle
    m_application__landing --> g_application__support
    m_application__landing --> g_pkg__agentic__shared
    m_application__landing --> g_pkg__domain
    m_application__landing --> m_application__landing_adr
    m_application__landing --> m_application__landing_fix
    m_application__landing_adr --> g_application__checks
    m_application__landing_adr --> g_application__landing__2
    m_application__landing_adr --> g_application__lifecycle
    m_application__landing_adr --> g_application__support
    m_application__landing_adr --> g_pkg__domain
    m_application__landing_adr --> m_application__adr_collision
    m_application__landing_adr --> m_application__landing_fix
    m_application__landing_fix --> g_application__build
    m_application__landing_fix --> g_application__landing__2
    m_application__landing_fix --> g_application__lifecycle
    m_application__landing_fix --> g_application__support
    m_application__landing_fix --> g_pkg__domain
```
