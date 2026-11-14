Esta página muestra los módulos del grupo `build` de `application` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `application`, `<nombre> (grupo de domain)` un grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera. Los imports hacia `domain` se resumen en un solo nodo `domain (paquete)` porque dibujar un nodo por grupo no cabe en el presupuesto de diagramas. El grupo se dividió por cantidad: esta es la parte 2 de 2, con módulos consecutivos en orden alfabético, y las otras partes aparecen como nodos `<grupo> parte <n> (grupo)`.

- Padre: [application](packages-process-application.md)
- Hermanas:
  - [application: build (parte 1)](packages-process-application-build-1.md)
  - [application: checks](packages-process-application-checks.md)
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
    g_application__checks["checks (grupo)"]
    g_application__landing["landing (grupo)"]
    g_application__lifecycle["lifecycle (grupo)"]
    g_application__support["support (grupo)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    g_pkg__domain["domain (paquete)"]
    m_application__pre_pr_fix["application.pre_pr_fix"]
    m_application__promotion["application.promotion"]
    m_application__review["application.review"]
    m_application__review_scope["application.review_scope"]
    m_application__shadow_review["application.shadow_review"]
    m_application__split["application.split"]
    m_application__tracker_only["application.tracker_only"]
    m_application__pre_pr_fix --> g_application__landing
    m_application__pre_pr_fix --> g_application__support
    m_application__pre_pr_fix --> g_pkg__agentic__shared
    m_application__pre_pr_fix --> g_pkg__domain
    m_application__promotion --> g_pkg__domain
    m_application__review --> g_application__checks
    m_application__review --> g_application__support
    m_application__review --> g_pkg__agentic__shared
    m_application__review --> g_pkg__domain
    m_application__review --> m_application__promotion
    m_application__review --> m_application__review_scope
    m_application__review --> m_application__shadow_review
    m_application__review_scope --> g_application__support
    m_application__review_scope --> g_pkg__domain
    m_application__shadow_review --> g_application__support
    m_application__shadow_review --> g_pkg__agentic__shared
    m_application__shadow_review --> g_pkg__domain
    m_application__shadow_review --> m_application__review
    m_application__split --> g_application__lifecycle
    m_application__split --> g_application__support
    m_application__split --> g_pkg__agentic__shared
    m_application__split --> g_pkg__domain
    m_application__split --> m_application__tracker_only
    m_application__tracker_only --> g_application__landing
    m_application__tracker_only --> g_application__support
    m_application__tracker_only --> g_pkg__agentic__shared
    m_application__tracker_only --> g_pkg__domain
```
