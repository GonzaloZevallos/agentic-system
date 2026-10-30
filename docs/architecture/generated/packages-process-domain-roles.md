Esta página muestra los módulos del grupo `roles` de `domain` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `domain`, `<nombre> (grupo de domain)` un grupo de `domain` y `<nombre> (paquete)` otro paquete, cada uno con su página.

- Padre: [domain](packages-process-domain.md)
- Hermanas:
  - [domain: checks](packages-process-domain-checks.md)
  - [domain: landing](packages-process-domain-landing.md)
  - [domain: machine](packages-process-domain-machine.md)
  - [domain: playbook](packages-process-domain-playbook.md)
  - [domain: state](packages-process-domain-state.md)

```mermaid
graph TD
    g_domain__checks["checks (grupo)"]
    g_domain__landing["landing (grupo)"]
    g_domain__state["state (grupo)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    m_domain__copy["domain.copy"]
    m_domain__denials["domain.denials"]
    m_domain__design["domain.design"]
    m_domain__docs["domain.docs"]
    m_domain__roles["domain.roles"]
    m_domain__schema_guard["domain.schema_guard"]
    m_domain__schemas["domain.schemas"]
    m_domain__stack_review["domain.stack_review"]
    m_domain__stack_signals["domain.stack_signals"]
    m_domain__turn_notice["domain.turn_notice"]
    m_domain__copy --> g_domain__state
    m_domain__copy --> m_domain__schemas
    m_domain__denials --> m_domain__roles
    m_domain__roles --> g_domain__checks
    m_domain__roles --> g_domain__state
    m_domain__roles --> g_pkg__agentic__shared
    m_domain__roles --> m_domain__schemas
    m_domain__schema_guard --> g_domain__state
    m_domain__schemas --> g_domain__checks
    m_domain__schemas --> g_domain__landing
    m_domain__schemas --> g_domain__state
    m_domain__turn_notice --> g_domain__state
    m_domain__turn_notice --> m_domain__roles
```
