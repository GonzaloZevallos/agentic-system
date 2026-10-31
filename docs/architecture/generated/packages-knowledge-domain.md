Esta página muestra los módulos de la capa `domain` de `agentic.core.knowledge` y los imports que hacen; un nodo `<nombre> (paquete)` es otra capa del paquete o un paquete de fuera, que no se detalla aquí.

- Padre: [Paquetes de knowledge](packages-knowledge.md)
- Hermanas:
  - [knowledge: adapters](packages-knowledge-adapters.md)

```mermaid
graph TD
    g_pkg__agentic__core__process["agentic.core.process (paquete)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    m_domain__flags["domain.flags"]
    m_domain__knowledge["domain.knowledge"]
    m_domain__knowledge_index["domain.knowledge_index"]
    m_domain__owner_rules["domain.owner_rules"]
    m_domain__stack["domain.stack"]
    m_domain__stack_checks["domain.stack_checks"]
    m_domain__knowledge --> g_pkg__agentic__core__process
    m_domain__knowledge --> g_pkg__agentic__shared
    m_domain__knowledge --> m_domain__owner_rules
    m_domain__knowledge_index --> g_pkg__agentic__core__process
    m_domain__knowledge_index --> m_domain__knowledge
    m_domain__knowledge_index --> m_domain__owner_rules
    m_domain__owner_rules --> g_pkg__agentic__core__process
    m_domain__stack --> g_pkg__agentic__core__process
    m_domain__stack --> m_domain__stack_checks
    m_domain__stack_checks --> g_pkg__agentic__core__process
```
