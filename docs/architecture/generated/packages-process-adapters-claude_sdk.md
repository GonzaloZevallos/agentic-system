Esta página muestra los módulos del adaptador `adapters.claude_sdk` y los imports que hacen; un nodo `<nombre> (paquete)` es otro paquete, con su página.

- Padre: [Paquetes de process](packages-process.md)
- Hermanas:
  - [adapters.filesystem](packages-process-adapters-filesystem.md)
  - [adapters.git](packages-process-adapters-git.md)
  - [adapters.github](packages-process-adapters-github.md)
  - [adapters.langgraph](packages-process-adapters-langgraph.md)
  - [adapters.shell](packages-process-adapters-shell.md)
  - [adapters](packages-process-adapters.md)
  - [application](packages-process-application.md)
  - [domain](packages-process-domain.md)

```mermaid
graph TD
    g_pkg__adapters["adapters (paquete)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    g_pkg__application["application (paquete)"]
    g_pkg__domain["domain (paquete)"]
    m_adapters__claude_sdk__check_server["adapters.claude_sdk.check_server"]
    m_adapters__claude_sdk__hooks["adapters.claude_sdk.hooks"]
    m_adapters__claude_sdk__printer["adapters.claude_sdk.printer"]
    m_adapters__claude_sdk__runner["adapters.claude_sdk.runner"]
    m_adapters__claude_sdk__schema["adapters.claude_sdk.schema"]
    m_adapters__claude_sdk__check_server --> g_pkg__application
    m_adapters__claude_sdk__check_server --> g_pkg__domain
    m_adapters__claude_sdk__check_server --> m_adapters__claude_sdk__hooks
    m_adapters__claude_sdk__hooks --> g_pkg__application
    m_adapters__claude_sdk__hooks --> g_pkg__domain
    m_adapters__claude_sdk__runner --> g_pkg__adapters
    m_adapters__claude_sdk__runner --> g_pkg__agentic__shared
    m_adapters__claude_sdk__runner --> g_pkg__application
    m_adapters__claude_sdk__runner --> g_pkg__domain
    m_adapters__claude_sdk__runner --> m_adapters__claude_sdk__check_server
    m_adapters__claude_sdk__runner --> m_adapters__claude_sdk__hooks
    m_adapters__claude_sdk__runner --> m_adapters__claude_sdk__printer
    m_adapters__claude_sdk__runner --> m_adapters__claude_sdk__schema
```
