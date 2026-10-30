Esta página muestra los módulos del grupo `state` de `domain` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera (por ejemplo `agentic.shared`).

- Padre: [domain](packages-process-domain.md)
- Hermanas:
  - [domain: checks](packages-process-domain-checks.md)
  - [domain: landing](packages-process-domain-landing.md)
  - [domain: machine](packages-process-domain-machine.md)
  - [domain: playbook](packages-process-domain-playbook.md)
  - [domain: roles](packages-process-domain-roles.md)

```mermaid
graph TD
    g_domain__checks["checks (grupo)"]
    g_domain__landing["landing (grupo)"]
    g_domain__machine["machine (grupo)"]
    g_domain__roles["roles (grupo)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    m_domain__document["domain.document"]
    m_domain__legacy_names["domain.legacy_names"]
    m_domain__returns["domain.returns"]
    m_domain__rules["domain.rules"]
    m_domain__signals["domain.signals"]
    m_domain__task["domain.task"]
    m_domain__document --> m_domain__legacy_names
    m_domain__document --> m_domain__task
    m_domain__returns --> m_domain__rules
    m_domain__returns --> m_domain__task
    m_domain__rules --> g_domain__checks
    m_domain__rules --> g_domain__landing
    m_domain__rules --> g_domain__machine
    m_domain__rules --> g_domain__roles
    m_domain__rules --> g_pkg__agentic__shared
    m_domain__rules --> m_domain__document
    m_domain__rules --> m_domain__task
    m_domain__task --> g_domain__checks
    m_domain__task --> g_domain__machine
```
