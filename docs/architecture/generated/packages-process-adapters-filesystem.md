Esta página muestra los módulos del adaptador `adapters.filesystem` y los imports que hacen; un nodo `<nombre> (paquete)` es otro paquete, con su página.

- Padre: [Paquetes de process](packages-process.md)
- Hermanas:
  - [adapters.claude_sdk](packages-process-adapters-claude_sdk.md)
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
    g_pkg__application["application (paquete)"]
    g_pkg__domain["domain (paquete)"]
    m_adapters__filesystem__board_sent["adapters.filesystem.board_sent"]
    m_adapters__filesystem__check_ledger["adapters.filesystem.check_ledger"]
    m_adapters__filesystem__check_results["adapters.filesystem.check_results"]
    m_adapters__filesystem__documents["adapters.filesystem.documents"]
    m_adapters__filesystem__free_space["adapters.filesystem.free_space"]
    m_adapters__filesystem__liveness["adapters.filesystem.liveness"]
    m_adapters__filesystem__lock["adapters.filesystem.lock"]
    m_adapters__filesystem__perf_lock["adapters.filesystem.perf_lock"]
    m_adapters__filesystem__prompts["adapters.filesystem.prompts"]
    m_adapters__filesystem__run_registry["adapters.filesystem.run_registry"]
    m_adapters__filesystem__scratch["adapters.filesystem.scratch"]
    m_adapters__filesystem__board_sent --> g_pkg__application
    m_adapters__filesystem__board_sent --> g_pkg__domain
    m_adapters__filesystem__check_ledger --> g_pkg__application
    m_adapters__filesystem__check_ledger --> g_pkg__domain
    m_adapters__filesystem__check_ledger --> m_adapters__filesystem__liveness
    m_adapters__filesystem__check_results --> g_pkg__domain
    m_adapters__filesystem__documents --> g_pkg__adapters
    m_adapters__filesystem__documents --> g_pkg__domain
    m_adapters__filesystem__free_space --> g_pkg__domain
    m_adapters__filesystem__liveness --> g_pkg__domain
    m_adapters__filesystem__lock --> g_pkg__application
    m_adapters__filesystem__lock --> m_adapters__filesystem__liveness
    m_adapters__filesystem__perf_lock --> g_pkg__application
    m_adapters__filesystem__perf_lock --> m_adapters__filesystem__liveness
    m_adapters__filesystem__prompts --> g_pkg__domain
    m_adapters__filesystem__run_registry --> g_pkg__application
    m_adapters__filesystem__run_registry --> g_pkg__domain
    m_adapters__filesystem__run_registry --> m_adapters__filesystem__liveness
    m_adapters__filesystem__scratch --> g_pkg__domain
    m_adapters__filesystem__scratch --> m_adapters__filesystem__liveness
```
