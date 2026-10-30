Esta página muestra los módulos del adaptador `adapters.github` y los imports que hacen; un nodo `<nombre> (paquete)` es otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera (por ejemplo `agentic.shared`).

- Padre: [Paquetes de process](packages-process.md)
- Hermanas:
  - [adapters.claude_sdk](packages-process-adapters-claude_sdk.md)
  - [adapters.filesystem](packages-process-adapters-filesystem.md)
  - [adapters.git](packages-process-adapters-git.md)
  - [adapters.langgraph](packages-process-adapters-langgraph.md)
  - [adapters.shell](packages-process-adapters-shell.md)
  - [adapters](packages-process-adapters.md)
  - [application](packages-process-application.md)
  - [domain](packages-process-domain.md)

```mermaid
graph TD
    g_pkg__application["application (paquete)"]
    g_pkg__domain["domain (paquete)"]
    m_adapters__github__board["adapters.github.board"]
    m_adapters__github__inbox["adapters.github.inbox"]
    m_adapters__github__run["adapters.github.run"]
    m_adapters__github__tracker["adapters.github.tracker"]
    m_adapters__github__board --> g_pkg__application
    m_adapters__github__board --> g_pkg__domain
    m_adapters__github__board --> m_adapters__github__run
    m_adapters__github__inbox --> g_pkg__domain
    m_adapters__github__inbox --> m_adapters__github__run
    m_adapters__github__inbox --> m_adapters__github__tracker
    m_adapters__github__run --> g_pkg__application
    m_adapters__github__tracker --> g_pkg__application
    m_adapters__github__tracker --> g_pkg__domain
    m_adapters__github__tracker --> m_adapters__github__run
```
