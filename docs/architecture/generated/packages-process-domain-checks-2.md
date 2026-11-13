Esta página muestra los módulos del grupo `checks` de `domain` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera. El grupo se dividió por cantidad: esta es la parte 2 de 2, con módulos consecutivos en orden alfabético, y las otras partes aparecen como nodos `<grupo> parte <n> (grupo)`.

- Padre: [domain](packages-process-domain.md)
- Hermanas:
  - [domain: checks (parte 1)](packages-process-domain-checks-1.md)
  - [domain: landing](packages-process-domain-landing.md)
  - [domain: machine](packages-process-domain-machine.md)
  - [domain: playbook](packages-process-domain-playbook.md)
  - [domain: roles](packages-process-domain-roles.md)
  - [domain: state (parte 1)](packages-process-domain-state-1.md)
  - [domain: state (parte 2)](packages-process-domain-state-2.md)

```mermaid
graph TD
    g_domain__checks__1["checks parte 1 (grupo)"]
    g_domain__state["state (grupo)"]
    m_domain__master_gain["domain.master_gain"]
    m_domain__merge_ci["domain.merge_ci"]
    m_domain__merge_conflict["domain.merge_conflict"]
    m_domain__performance["domain.performance"]
    m_domain__presubmit["domain.presubmit"]
    m_domain__run_check["domain.run_check"]
    m_domain__speculation["domain.speculation"]
    m_domain__suppressions["domain.suppressions"]
    m_domain__master_gain --> g_domain__checks__1
    m_domain__merge_ci --> g_domain__state
    m_domain__merge_ci --> m_domain__master_gain
    m_domain__merge_conflict --> g_domain__state
    m_domain__presubmit --> g_domain__state
    m_domain__run_check --> m_domain__presubmit
    m_domain__speculation --> g_domain__state
```
