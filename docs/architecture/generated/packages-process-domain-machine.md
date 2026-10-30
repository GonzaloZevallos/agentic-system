Esta página muestra los módulos del grupo `machine` de `domain` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `domain`, `<nombre> (grupo de domain)` un grupo de `domain` y `<nombre> (paquete)` otro paquete, cada uno con su página.

- Padre: [domain](packages-process-domain.md)
- Hermanas:
  - [domain: checks](packages-process-domain-checks.md)
  - [domain: landing](packages-process-domain-landing.md)
  - [domain: playbook](packages-process-domain-playbook.md)
  - [domain: roles](packages-process-domain-roles.md)
  - [domain: state](packages-process-domain-state.md)

```mermaid
graph TD
    g_domain__state["state (grupo)"]
    m_domain__account_reset["domain.account_reset"]
    m_domain__boot["domain.boot"]
    m_domain__reaping["domain.reaping"]
    m_domain__scratch["domain.scratch"]
    m_domain__stall["domain.stall"]
    m_domain__account_reset --> g_domain__state
```
