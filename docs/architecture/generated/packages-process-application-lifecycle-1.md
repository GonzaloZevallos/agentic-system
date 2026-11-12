Esta página muestra los módulos del grupo `lifecycle` de `application` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `application`, `<nombre> (grupo de domain)` un grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera. Los imports hacia `domain` se resumen en un solo nodo `domain (paquete)` porque dibujar un nodo por grupo no cabe en el presupuesto de diagramas. El grupo se dividió por cantidad: esta es la parte 1 de 2, con módulos consecutivos en orden alfabético, y las otras partes aparecen como nodos `<grupo> parte <n> (grupo)`.

- Padre: [application](packages-process-application.md)
- Hermanas:
  - [application: build (parte 1)](packages-process-application-build-1.md)
  - [application: build (parte 2)](packages-process-application-build-2.md)
  - [application: checks](packages-process-application-checks.md)
  - [application: definition (parte 1)](packages-process-application-definition-1.md)
  - [application: definition (parte 2)](packages-process-application-definition-2.md)
  - [application: landing (parte 1)](packages-process-application-landing-1.md)
  - [application: landing (parte 2)](packages-process-application-landing-2.md)
  - [application: lifecycle (parte 2)](packages-process-application-lifecycle-2.md)
  - [application: reflection](packages-process-application-reflection.md)
  - [application: support](packages-process-application-support.md)

```mermaid
graph TD
    g_application__build["build (grupo)"]
    g_application__checks["checks (grupo)"]
    g_application__definition["definition (grupo)"]
    g_application__landing["landing (grupo)"]
    g_application__lifecycle__2["lifecycle parte 2 (grupo)"]
    g_application__reflection["reflection (grupo)"]
    g_application__support["support (grupo)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    g_pkg__domain["domain (paquete)"]
    m_application__archive["application.archive"]
    m_application__board_sync["application.board_sync"]
    m_application__poll["application.poll"]
    m_application__registration["application.registration"]
    m_application__relabel["application.relabel"]
    m_application__restore["application.restore"]
    m_application__archive --> g_application__support
    m_application__archive --> g_pkg__domain
    m_application__board_sync --> g_application__support
    m_application__board_sync --> g_pkg__domain
    m_application__poll --> g_application__build
    m_application__poll --> g_application__checks
    m_application__poll --> g_application__landing
    m_application__poll --> g_application__lifecycle__2
    m_application__poll --> g_application__reflection
    m_application__poll --> g_application__support
    m_application__poll --> g_pkg__agentic__shared
    m_application__poll --> g_pkg__domain
    m_application__poll --> m_application__board_sync
    m_application__poll --> m_application__registration
    m_application__registration --> g_application__support
    m_application__registration --> g_pkg__domain
    m_application__relabel --> g_application__definition
    m_application__relabel --> g_application__support
    m_application__relabel --> g_pkg__domain
    m_application__restore --> g_application__landing
    m_application__restore --> g_application__lifecycle__2
    m_application__restore --> g_application__support
    m_application__restore --> g_pkg__domain
    m_application__restore --> m_application__archive
```
