Esta página muestra los módulos del adaptador `adapters.smtp` y los imports que hacen; un nodo `<nombre> (paquete)` es otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera.

- Padre: [Paquetes de process](packages-process.md)
- Hermanas:
  - [adapters.claude_sdk](packages-process-adapters-claude_sdk.md)
  - [adapters.filesystem (parte 1)](packages-process-adapters-filesystem-1.md)
  - [adapters.filesystem (parte 2)](packages-process-adapters-filesystem-2.md)
  - [adapters.git](packages-process-adapters-git.md)
  - [adapters.github](packages-process-adapters-github.md)
  - [adapters.langgraph](packages-process-adapters-langgraph.md)
  - [adapters.shell](packages-process-adapters-shell.md)
  - [adapters](packages-process-adapters.md)
  - [application](packages-process-application.md)
  - [domain](packages-process-domain.md)

```mermaid
graph TD
    g_pkg__application["application (paquete)"]
    m_adapters__smtp__config["adapters.smtp.config"]
    m_adapters__smtp__notifier["adapters.smtp.notifier"]
    m_adapters__smtp__notifier --> g_pkg__application
    m_adapters__smtp__notifier --> m_adapters__smtp__config
```
