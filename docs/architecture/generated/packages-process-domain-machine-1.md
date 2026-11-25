Esta página muestra los módulos del grupo `machine` de `domain` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera. El grupo se dividió por cantidad: esta es la parte 1 de 2, con módulos consecutivos en orden alfabético, y las otras partes aparecen como nodos `<grupo> parte <n> (grupo)`.

- Padre: [domain](packages-process-domain.md)
- Hermanas:
  - [domain: checks](packages-process-domain-checks.md)
  - [domain: landing](packages-process-domain-landing.md)
  - [domain: machine (parte 2)](packages-process-domain-machine-2.md)
  - [domain: playbook](packages-process-domain-playbook.md)
  - [domain: roles](packages-process-domain-roles.md)
  - [domain: state (parte 1)](packages-process-domain-state-1.md)
  - [domain: state (parte 2)](packages-process-domain-state-2.md)

```mermaid
graph TD
    g_domain__landing["landing (grupo)"]
    g_domain__machine__2["machine parte 2 (grupo)"]
    g_domain__state["state (grupo)"]
    m_domain__account_reset["domain.account_reset"]
    m_domain__boot["domain.boot"]
    m_domain__exempt_queue["domain.exempt_queue"]
    m_domain__github_access["domain.github_access"]
    m_domain__github_budget["domain.github_budget"]
    m_domain__launch_order["domain.launch_order"]
    m_domain__notify["domain.notify"]
    m_domain__notify_message["domain.notify_message"]
    m_domain__output_repair["domain.output_repair"]
    m_domain__poller_units["domain.poller_units"]
    m_domain__account_reset --> g_domain__state
    m_domain__exempt_queue --> g_domain__state
    m_domain__notify --> g_domain__state
    m_domain__notify_message --> g_domain__state
    m_domain__notify_message --> m_domain__notify
    m_domain__output_repair --> g_domain__landing
    m_domain__output_repair --> g_domain__machine__2
    m_domain__output_repair --> g_domain__state
    m_domain__poller_units --> g_domain__machine__2
```
