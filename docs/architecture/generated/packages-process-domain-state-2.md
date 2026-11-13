Esta página muestra los módulos del grupo `state` de `domain` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera. El grupo se dividió por cantidad: esta es la parte 2 de 2, con módulos consecutivos en orden alfabético, y las otras partes aparecen como nodos `<grupo> parte <n> (grupo)`.

- Padre: [domain](packages-process-domain.md)
- Hermanas:
  - [domain: checks (parte 1)](packages-process-domain-checks-1.md)
  - [domain: checks (parte 2)](packages-process-domain-checks-2.md)
  - [domain: landing](packages-process-domain-landing.md)
  - [domain: machine](packages-process-domain-machine.md)
  - [domain: playbook](packages-process-domain-playbook.md)
  - [domain: roles](packages-process-domain-roles.md)
  - [domain: state (parte 1)](packages-process-domain-state-1.md)

```mermaid
graph TD
    g_domain__checks["checks (grupo)"]
    g_domain__landing["landing (grupo)"]
    g_domain__machine["machine (grupo)"]
    g_domain__roles["roles (grupo)"]
    g_domain__state__1["state parte 1 (grupo)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    m_domain__returns["domain.returns"]
    m_domain__rules["domain.rules"]
    m_domain__signals["domain.signals"]
    m_domain__size_budget["domain.size_budget"]
    m_domain__spec_delivery["domain.spec_delivery"]
    m_domain__task["domain.task"]
    m_domain__task_path["domain.task_path"]
    m_domain__returns --> m_domain__rules
    m_domain__returns --> m_domain__task
    m_domain__rules --> g_domain__checks
    m_domain__rules --> g_domain__landing
    m_domain__rules --> g_domain__machine
    m_domain__rules --> g_domain__roles
    m_domain__rules --> g_domain__state__1
    m_domain__rules --> g_pkg__agentic__shared
    m_domain__rules --> m_domain__size_budget
    m_domain__rules --> m_domain__task
    m_domain__rules --> m_domain__task_path
    m_domain__size_budget --> g_domain__checks
    m_domain__task --> g_domain__checks
    m_domain__task --> g_domain__landing
    m_domain__task --> g_domain__machine
    m_domain__task --> g_domain__roles
    m_domain__task --> g_domain__state__1
    m_domain__task --> m_domain__size_budget
    m_domain__task --> m_domain__task_path
```
