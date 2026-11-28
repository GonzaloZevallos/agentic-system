Esta página muestra los módulos sueltos de `adapters`, los que no están en ningún tipo de adaptador, y los imports que hacen; un nodo `<nombre> (paquete)` es otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera.

- Padre: [Paquetes de process](packages-process.md)
- Hermanas:
  - [adapters.claude_sdk](packages-process-adapters-claude_sdk.md)
  - [adapters.filesystem (parte 1)](packages-process-adapters-filesystem-1.md)
  - [adapters.filesystem (parte 2)](packages-process-adapters-filesystem-2.md)
  - [adapters.git](packages-process-adapters-git.md)
  - [adapters.github](packages-process-adapters-github.md)
  - [adapters.langgraph](packages-process-adapters-langgraph.md)
  - [adapters.shell](packages-process-adapters-shell.md)
  - [adapters.smtp](packages-process-adapters-smtp.md)
  - [application](packages-process-application.md)
  - [domain](packages-process-domain.md)

```mermaid
graph TD
    g_pkg__application["application (paquete)"]
    g_pkg__domain["domain (paquete)"]
    m_adapters__evaluation["adapters.evaluation"]
    m_adapters__offline["adapters.offline"]
    m_adapters__playbook_land_record["adapters.playbook_land_record"]
    m_adapters__plugins["adapters.plugins"]
    m_adapters__telemetry["adapters.telemetry"]
    m_adapters__evaluation --> g_pkg__application
    m_adapters__evaluation --> g_pkg__domain
    m_adapters__playbook_land_record --> g_pkg__domain
    m_adapters__telemetry --> g_pkg__domain
```
