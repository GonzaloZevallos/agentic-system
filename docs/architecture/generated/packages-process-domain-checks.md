Esta página muestra los módulos del grupo `checks` de `domain` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `domain`, `<nombre> (grupo de domain)` un grupo de `domain` y `<nombre> (paquete)` otro paquete, cada uno con su página.

- Padre: [domain](packages-process-domain.md)
- Hermanas:
  - [domain: landing](packages-process-domain-landing.md)
  - [domain: machine](packages-process-domain-machine.md)
  - [domain: playbook](packages-process-domain-playbook.md)
  - [domain: roles](packages-process-domain-roles.md)
  - [domain: state](packages-process-domain-state.md)

```mermaid
graph TD
    g_domain__state["state (grupo)"]
    m_domain__check_results["domain.check_results"]
    m_domain__check_schedule["domain.check_schedule"]
    m_domain__ci["domain.ci"]
    m_domain__coverage["domain.coverage"]
    m_domain__master_gain["domain.master_gain"]
    m_domain__performance["domain.performance"]
    m_domain__presubmit["domain.presubmit"]
    m_domain__run_check["domain.run_check"]
    m_domain__suppressions["domain.suppressions"]
    m_domain__ci --> g_domain__state
    m_domain__master_gain --> m_domain__check_results
    m_domain__presubmit --> g_domain__state
    m_domain__run_check --> m_domain__presubmit
```
