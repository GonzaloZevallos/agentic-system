Esta página muestra los módulos del grupo `machine` de `domain` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera.

- Padre: [domain](packages-process-domain.md)
- Hermanas:
  - [domain: checks](packages-process-domain-checks.md)
  - [domain: landing](packages-process-domain-landing.md)
  - [domain: playbook](packages-process-domain-playbook.md)
  - [domain: roles](packages-process-domain-roles.md)
  - [domain: state (parte 1)](packages-process-domain-state-1.md)
  - [domain: state (parte 2)](packages-process-domain-state-2.md)

```mermaid
graph TD
    g_domain__state["state (grupo)"]
    m_domain__account_reset["domain.account_reset"]
    m_domain__boot["domain.boot"]
    m_domain__exempt_queue["domain.exempt_queue"]
    m_domain__reaping["domain.reaping"]
    m_domain__retakes["domain.retakes"]
    m_domain__scratch["domain.scratch"]
    m_domain__stage0_rotation["domain.stage0_rotation"]
    m_domain__stall["domain.stall"]
    m_domain__account_reset --> g_domain__state
    m_domain__exempt_queue --> g_domain__state
    m_domain__stage0_rotation --> g_domain__state
```
