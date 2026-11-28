Esta página muestra los módulos del adaptador `adapters.git` y los imports que hacen; un nodo `<nombre> (paquete)` es otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera.

- Padre: [Paquetes de process](packages-process.md)
- Hermanas:
  - [adapters.claude_sdk](packages-process-adapters-claude_sdk.md)
  - [adapters.filesystem (parte 1)](packages-process-adapters-filesystem-1.md)
  - [adapters.filesystem (parte 2)](packages-process-adapters-filesystem-2.md)
  - [adapters.github](packages-process-adapters-github.md)
  - [adapters.langgraph](packages-process-adapters-langgraph.md)
  - [adapters.shell](packages-process-adapters-shell.md)
  - [adapters.smtp](packages-process-adapters-smtp.md)
  - [adapters](packages-process-adapters.md)
  - [application](packages-process-application.md)
  - [domain](packages-process-domain.md)

```mermaid
graph TD
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    g_pkg__application["application (paquete)"]
    g_pkg__domain["domain (paquete)"]
    m_adapters__git__ancestry["adapters.git.ancestry"]
    m_adapters__git__guard["adapters.git.guard"]
    m_adapters__git__master_window["adapters.git.master_window"]
    m_adapters__git__workspace["adapters.git.workspace"]
    m_adapters__git__ancestry --> m_adapters__git__master_window
    m_adapters__git__guard --> g_pkg__application
    m_adapters__git__guard --> g_pkg__domain
    m_adapters__git__master_window --> g_pkg__application
    m_adapters__git__master_window --> g_pkg__domain
    m_adapters__git__workspace --> g_pkg__agentic__shared
    m_adapters__git__workspace --> g_pkg__application
    m_adapters__git__workspace --> g_pkg__domain
    m_adapters__git__workspace --> m_adapters__git__guard
```
