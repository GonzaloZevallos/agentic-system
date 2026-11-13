Esta página muestra los módulos del grupo `playbook` de `domain` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera.

- Padre: [domain](packages-process-domain.md)
- Hermanas:
  - [domain: checks](packages-process-domain-checks.md)
  - [domain: landing](packages-process-domain-landing.md)
  - [domain: machine](packages-process-domain-machine.md)
  - [domain: roles](packages-process-domain-roles.md)
  - [domain: state](packages-process-domain-state.md)

```mermaid
graph TD
    g_domain__roles["roles (grupo)"]
    g_domain__state["state (grupo)"]
    m_domain__playbook["domain.playbook"]
    m_domain__playbook_candidates["domain.playbook_candidates"]
    m_domain__system_lessons["domain.system_lessons"]
    m_domain__playbook --> g_domain__roles
    m_domain__playbook --> g_domain__state
    m_domain__playbook_candidates --> g_domain__roles
    m_domain__playbook_candidates --> g_domain__state
    m_domain__playbook_candidates --> m_domain__playbook
    m_domain__system_lessons --> m_domain__playbook
```
