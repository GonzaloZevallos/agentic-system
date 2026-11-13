Esta página muestra los módulos del grupo `state` de `domain` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera. El grupo se dividió por cantidad: esta es la parte 1 de 2, con módulos consecutivos en orden alfabético, y las otras partes aparecen como nodos `<grupo> parte <n> (grupo)`.

- Padre: [domain](packages-process-domain.md)
- Hermanas:
  - [domain: checks](packages-process-domain-checks.md)
  - [domain: landing](packages-process-domain-landing.md)
  - [domain: machine](packages-process-domain-machine.md)
  - [domain: playbook](packages-process-domain-playbook.md)
  - [domain: roles](packages-process-domain-roles.md)
  - [domain: state (parte 2)](packages-process-domain-state-2.md)

```mermaid
graph TD
    g_domain__roles["roles (grupo)"]
    g_domain__state__2["state parte 2 (grupo)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    m_domain__document["domain.document"]
    m_domain__escalation_prompts["domain.escalation_prompts"]
    m_domain__experiment["domain.experiment"]
    m_domain__finding_location["domain.finding_location"]
    m_domain__lead_time["domain.lead_time"]
    m_domain__legacy_names["domain.legacy_names"]
    m_domain__restart["domain.restart"]
    m_domain__document --> g_domain__state__2
    m_domain__document --> m_domain__legacy_names
    m_domain__escalation_prompts --> g_domain__state__2
    m_domain__experiment --> g_domain__state__2
    m_domain__experiment --> g_pkg__agentic__shared
    m_domain__restart --> g_domain__roles
    m_domain__restart --> g_domain__state__2
```
