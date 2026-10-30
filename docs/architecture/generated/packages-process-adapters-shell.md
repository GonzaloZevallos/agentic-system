Esta página muestra los módulos del adaptador `adapters.shell` y los imports que hacen; un nodo `<nombre> (paquete)` es otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera (por ejemplo `agentic.shared`).

- Padre: [Paquetes de process](packages-process.md)
- Hermanas:
  - [adapters.claude_sdk](packages-process-adapters-claude_sdk.md)
  - [adapters.filesystem](packages-process-adapters-filesystem.md)
  - [adapters.git](packages-process-adapters-git.md)
  - [adapters.github](packages-process-adapters-github.md)
  - [adapters.langgraph](packages-process-adapters-langgraph.md)
  - [adapters](packages-process-adapters.md)
  - [application](packages-process-application.md)
  - [domain](packages-process-domain.md)

```mermaid
graph TD
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    g_pkg__application["application (paquete)"]
    g_pkg__domain["domain (paquete)"]
    m_adapters__shell__commands["adapters.shell.commands"]
    m_adapters__shell__inhibitor["adapters.shell.inhibitor"]
    m_adapters__shell__perf_runner["adapters.shell.perf_runner"]
    m_adapters__shell__reaper["adapters.shell.reaper"]
    m_adapters__shell__sample_app["adapters.shell.sample_app"]
    m_adapters__shell__stop["adapters.shell.stop"]
    m_adapters__shell__commands --> g_pkg__agentic__shared
    m_adapters__shell__commands --> g_pkg__application
    m_adapters__shell__commands --> g_pkg__domain
    m_adapters__shell__perf_runner --> g_pkg__application
    m_adapters__shell__perf_runner --> g_pkg__domain
    m_adapters__shell__reaper --> g_pkg__domain
    m_adapters__shell__sample_app --> g_pkg__agentic__shared
    m_adapters__shell__sample_app --> g_pkg__application
    m_adapters__shell__sample_app --> g_pkg__domain
```
