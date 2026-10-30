Esta página muestra los módulos del adaptador `adapters.langgraph` y los imports que hacen; un nodo `<nombre> (paquete)` es otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera.

- Padre: [Paquetes de process](packages-process.md)
- Hermanas:
  - [adapters.claude_sdk](packages-process-adapters-claude_sdk.md)
  - [adapters.filesystem](packages-process-adapters-filesystem.md)
  - [adapters.git](packages-process-adapters-git.md)
  - [adapters.github](packages-process-adapters-github.md)
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
    m_adapters__langgraph__graph["adapters.langgraph.graph"]
    m_adapters__langgraph__graph --> g_pkg__adapters
    m_adapters__langgraph__graph --> g_pkg__agentic__shared
    m_adapters__langgraph__graph --> g_pkg__application
    m_adapters__langgraph__graph --> g_pkg__domain
```
