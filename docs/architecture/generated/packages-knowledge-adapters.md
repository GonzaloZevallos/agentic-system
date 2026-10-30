Esta página muestra los módulos de la capa `adapters` de `agentic.core.knowledge` y los imports que hacen; un nodo `<nombre> (paquete)` es otra capa del paquete o un paquete de fuera, que no se detalla aquí.

- Padre: [Paquetes de knowledge](packages-knowledge.md)
- Hermanas:
  - [knowledge: domain](packages-knowledge-domain.md)

```mermaid
graph TD
    g_pkg__agentic__core__process["agentic.core.process (paquete)"]
    g_pkg__domain["domain (paquete)"]
    m_adapters__filesystem__flags["adapters.filesystem.flags"]
    m_adapters__filesystem__knowledge["adapters.filesystem.knowledge"]
    m_adapters__git__stack["adapters.git.stack"]
    m_adapters__filesystem__flags --> g_pkg__domain
    m_adapters__filesystem__knowledge --> g_pkg__agentic__core__process
    m_adapters__filesystem__knowledge --> g_pkg__domain
    m_adapters__git__stack --> g_pkg__agentic__core__process
    m_adapters__git__stack --> g_pkg__domain
```
