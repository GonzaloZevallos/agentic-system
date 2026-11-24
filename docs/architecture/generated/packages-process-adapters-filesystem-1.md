Esta página muestra la parte 1 de 2 de los módulos del adaptador `adapters.filesystem`, con módulos consecutivos en orden alfabético, y los imports que hacen; un nodo `<nombre> parte <n> (grupo)` es otra parte del adaptador y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera.

- Padre: [adapters.filesystem](packages-process-adapters-filesystem.md)
- Hermanas:
  - [adapters.filesystem (parte 2)](packages-process-adapters-filesystem-2.md)

```mermaid
graph TD
    g_adapters__filesystem__2["adapters.filesystem parte 2 (grupo)"]
    g_pkg__adapters["adapters (paquete)"]
    g_pkg__application["application (paquete)"]
    g_pkg__domain["domain (paquete)"]
    m_adapters__filesystem__board_sent["adapters.filesystem.board_sent"]
    m_adapters__filesystem__check_ledger["adapters.filesystem.check_ledger"]
    m_adapters__filesystem__check_results["adapters.filesystem.check_results"]
    m_adapters__filesystem__documents["adapters.filesystem.documents"]
    m_adapters__filesystem__experiment_file["adapters.filesystem.experiment_file"]
    m_adapters__filesystem__file_inbox["adapters.filesystem.file_inbox"]
    m_adapters__filesystem__free_space["adapters.filesystem.free_space"]
    m_adapters__filesystem__github_cache["adapters.filesystem.github_cache"]
    m_adapters__filesystem__heartbeats["adapters.filesystem.heartbeats"]
    m_adapters__filesystem__board_sent --> g_pkg__application
    m_adapters__filesystem__board_sent --> g_pkg__domain
    m_adapters__filesystem__check_ledger --> g_adapters__filesystem__2
    m_adapters__filesystem__check_ledger --> g_pkg__application
    m_adapters__filesystem__check_ledger --> g_pkg__domain
    m_adapters__filesystem__check_results --> g_pkg__domain
    m_adapters__filesystem__documents --> g_pkg__adapters
    m_adapters__filesystem__documents --> g_pkg__domain
    m_adapters__filesystem__file_inbox --> g_pkg__domain
    m_adapters__filesystem__free_space --> g_pkg__domain
    m_adapters__filesystem__github_cache --> g_pkg__domain
    m_adapters__filesystem__heartbeats --> g_pkg__domain
```
