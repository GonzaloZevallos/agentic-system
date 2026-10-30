Esta página muestra los módulos del grupo `landing` de `domain` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `domain`, `<nombre> (grupo de domain)` un grupo de `domain` y `<nombre> (paquete)` otro paquete, cada uno con su página.

- Padre: [domain](packages-process-domain.md)
- Hermanas:
  - [domain: checks](packages-process-domain-checks.md)
  - [domain: machine](packages-process-domain-machine.md)
  - [domain: playbook](packages-process-domain-playbook.md)
  - [domain: roles](packages-process-domain-roles.md)
  - [domain: state](packages-process-domain-state.md)

```mermaid
graph TD
    g_domain__checks["checks (grupo)"]
    g_domain__roles["roles (grupo)"]
    g_domain__state["state (grupo)"]
    m_domain__adr_numbers["domain.adr_numbers"]
    m_domain__board["domain.board"]
    m_domain__cost_history["domain.cost_history"]
    m_domain__landing["domain.landing"]
    m_domain__plan_settlement["domain.plan_settlement"]
    m_domain__plan_view["domain.plan_view"]
    m_domain__pull_request["domain.pull_request"]
    m_domain__board --> g_domain__state
    m_domain__cost_history --> g_domain__state
    m_domain__landing --> g_domain__checks
    m_domain__landing --> g_domain__roles
    m_domain__landing --> g_domain__state
    m_domain__landing --> m_domain__adr_numbers
    m_domain__plan_settlement --> g_domain__roles
    m_domain__plan_settlement --> g_domain__state
    m_domain__plan_view --> g_domain__state
```
