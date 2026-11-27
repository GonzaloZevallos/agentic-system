Esta página muestra los módulos del grupo `support` de `application` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `application`, `<nombre> (grupo de domain)` un grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera. Los imports hacia `domain` se resumen en un solo nodo `domain (paquete)` porque dibujar un nodo por grupo no cabe en el presupuesto de diagramas. El grupo se dividió por cantidad: esta es la parte 1 de 2, con módulos consecutivos en orden alfabético, y las otras partes aparecen como nodos `<grupo> parte <n> (grupo)`.

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
  - [application: support (parte 2)](packages-process-application-support-2.md)

```mermaid
graph TD
    g_application__checks["checks (grupo)"]
    g_application__landing["landing (grupo)"]
    g_application__lifecycle["lifecycle (grupo)"]
    g_application__support__2["support parte 2 (grupo)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    g_pkg__domain["domain (paquete)"]
    m_application__checkouts["application.checkouts"]
    m_application__ci_excerpt["application.ci_excerpt"]
    m_application__ci_push["application.ci_push"]
    m_application__ci_trust["application.ci_trust"]
    m_application__docs_settlement["application.docs_settlement"]
    m_application__format_hold["application.format_hold"]
    m_application__format_issue["application.format_issue"]
    m_application__format_release["application.format_release"]
    m_application__github_budget["application.github_budget"]
    m_application__landing_body["application.landing_body"]
    m_application__checkouts --> g_application__support__2
    m_application__checkouts --> g_pkg__agentic__shared
    m_application__checkouts --> g_pkg__domain
    m_application__ci_excerpt --> g_application__support__2
    m_application__ci_excerpt --> g_pkg__domain
    m_application__ci_push --> g_application__support__2
    m_application__ci_push --> g_pkg__domain
    m_application__ci_trust --> g_application__support__2
    m_application__ci_trust --> g_pkg__domain
    m_application__docs_settlement --> g_application__support__2
    m_application__docs_settlement --> g_pkg__domain
    m_application__format_hold --> g_pkg__domain
    m_application__format_issue --> g_application__support__2
    m_application__format_issue --> g_pkg__domain
    m_application__format_release --> g_application__support__2
    m_application__format_release --> g_pkg__domain
    m_application__github_budget --> g_application__support__2
    m_application__github_budget --> g_pkg__domain
    m_application__landing_body --> g_application__checks
    m_application__landing_body --> g_application__landing
    m_application__landing_body --> g_application__lifecycle
    m_application__landing_body --> g_pkg__domain
    m_application__landing_body --> m_application__checkouts
```
