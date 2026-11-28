Esta página muestra los módulos del grupo `reflection` de `application` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `application`, `<nombre> (grupo de domain)` un grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera. Los imports hacia `domain` se resumen en un solo nodo `domain (paquete)` porque dibujar un nodo por grupo no cabe en el presupuesto de diagramas. El grupo se dividió por cantidad: esta es la parte 1 de 2, con módulos consecutivos en orden alfabético, y las otras partes aparecen como nodos `<grupo> parte <n> (grupo)`.

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
  - [application: reflection (parte 2)](packages-process-application-reflection-2.md)
  - [application: support (parte 1)](packages-process-application-support-1.md)
  - [application: support (parte 2)](packages-process-application-support-2.md)

```mermaid
graph TD
    g_application__landing["landing (grupo)"]
    g_application__lifecycle["lifecycle (grupo)"]
    g_application__support["support (grupo)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    g_pkg__domain["domain (paquete)"]
    m_application__board_sync["application.board_sync"]
    m_application__early_size["application.early_size"]
    m_application__owner_rules["application.owner_rules"]
    m_application__playbook_land["application.playbook_land"]
    m_application__reflect["application.reflect"]
    m_application__reflect_run["application.reflect_run"]
    m_application__board_sync --> g_application__lifecycle
    m_application__board_sync --> g_application__support
    m_application__board_sync --> g_pkg__domain
    m_application__early_size --> g_application__landing
    m_application__early_size --> g_application__support
    m_application__early_size --> g_pkg__agentic__shared
    m_application__early_size --> g_pkg__domain
    m_application__owner_rules --> g_application__support
    m_application__owner_rules --> g_pkg__agentic__shared
    m_application__owner_rules --> g_pkg__domain
    m_application__playbook_land --> g_application__support
    m_application__playbook_land --> g_pkg__domain
    m_application__playbook_land --> m_application__reflect_run
    m_application__reflect --> g_application__support
    m_application__reflect --> g_pkg__agentic__shared
    m_application__reflect --> g_pkg__domain
    m_application__reflect_run --> g_application__landing
    m_application__reflect_run --> g_application__support
    m_application__reflect_run --> g_pkg__agentic__shared
    m_application__reflect_run --> g_pkg__domain
    m_application__reflect_run --> m_application__reflect
```
