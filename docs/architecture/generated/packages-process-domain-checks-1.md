Esta página muestra los módulos del grupo `checks` de `domain` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera. El grupo se dividió por cantidad: esta es la parte 1 de 2, con módulos consecutivos en orden alfabético, y las otras partes aparecen como nodos `<grupo> parte <n> (grupo)`.

- Padre: [domain](packages-process-domain.md)
- Hermanas:
  - [domain: checks (parte 2)](packages-process-domain-checks-2.md)
  - [domain: landing](packages-process-domain-landing.md)
  - [domain: machine](packages-process-domain-machine.md)
  - [domain: playbook](packages-process-domain-playbook.md)
  - [domain: roles](packages-process-domain-roles.md)
  - [domain: state (parte 1)](packages-process-domain-state-1.md)
  - [domain: state (parte 2)](packages-process-domain-state-2.md)

```mermaid
graph TD
    g_domain__checks__2["checks parte 2 (grupo)"]
    g_domain__state["state (grupo)"]
    m_domain__check_results["domain.check_results"]
    m_domain__check_runs["domain.check_runs"]
    m_domain__check_schedule["domain.check_schedule"]
    m_domain__ci["domain.ci"]
    m_domain__ci_push["domain.ci_push"]
    m_domain__ci_workflow["domain.ci_workflow"]
    m_domain__coverage["domain.coverage"]
    m_domain__gate_priority["domain.gate_priority"]
    m_domain__check_runs --> g_domain__checks__2
    m_domain__ci --> g_domain__checks__2
    m_domain__ci --> g_domain__state
    m_domain__gate_priority --> g_domain__state
    m_domain__gate_priority --> m_domain__check_schedule
```
