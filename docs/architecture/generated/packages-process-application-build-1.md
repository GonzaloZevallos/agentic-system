Esta página muestra los módulos del grupo `build` de `application` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `application`, `<nombre> (grupo de domain)` un grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera. Los imports hacia `domain` se resumen en un solo nodo `domain (paquete)` porque dibujar un nodo por grupo no cabe en el presupuesto de diagramas. El grupo se dividió por cantidad: esta es la parte 1 de 2, con módulos consecutivos en orden alfabético, y las otras partes aparecen como nodos `<grupo> parte <n> (grupo)`.

- Padre: [application](packages-process-application.md)
- Hermanas:
  - [application: build (parte 2)](packages-process-application-build-2.md)
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
    g_application__build__2["build parte 2 (grupo)"]
    g_application__checks["checks (grupo)"]
    g_application__lifecycle["lifecycle (grupo)"]
    g_application__support["support (grupo)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    g_pkg__domain["domain (paquete)"]
    m_application__api_break["application.api_break"]
    m_application__deliver["application.deliver"]
    m_application__delivered["application.delivered"]
    m_application__develop["application.develop"]
    m_application__escalate["application.escalate"]
    m_application__evaluated["application.evaluated"]
    m_application__integrate["application.integrate"]
    m_application__api_break --> g_application__support
    m_application__api_break --> g_pkg__domain
    m_application__deliver --> g_application__build__2
    m_application__deliver --> g_application__support
    m_application__deliver --> g_pkg__agentic__shared
    m_application__deliver --> g_pkg__domain
    m_application__deliver --> m_application__api_break
    m_application__delivered --> g_application__support
    m_application__delivered --> g_pkg__domain
    m_application__develop --> g_application__checks
    m_application__develop --> g_application__lifecycle
    m_application__develop --> g_application__support
    m_application__develop --> g_pkg__agentic__shared
    m_application__develop --> g_pkg__domain
    m_application__escalate --> g_application__support
    m_application__escalate --> g_pkg__agentic__shared
    m_application__escalate --> g_pkg__domain
    m_application__evaluated --> g_application__support
    m_application__evaluated --> g_pkg__agentic__shared
    m_application__evaluated --> g_pkg__domain
    m_application__integrate --> g_application__build__2
    m_application__integrate --> g_application__support
    m_application__integrate --> g_pkg__agentic__shared
    m_application__integrate --> g_pkg__domain
```
