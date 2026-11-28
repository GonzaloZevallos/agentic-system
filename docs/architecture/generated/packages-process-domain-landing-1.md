Esta página muestra los módulos del grupo `landing` de `domain` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera. El grupo se dividió por cantidad: esta es la parte 1 de 2, con módulos consecutivos en orden alfabético, y las otras partes aparecen como nodos `<grupo> parte <n> (grupo)`.

- Padre: [domain](packages-process-domain.md)
- Hermanas:
  - [domain: checks](packages-process-domain-checks.md)
  - [domain: landing (parte 2)](packages-process-domain-landing-2.md)
  - [domain: machine (parte 1)](packages-process-domain-machine-1.md)
  - [domain: machine (parte 2)](packages-process-domain-machine-2.md)
  - [domain: playbook](packages-process-domain-playbook.md)
  - [domain: roles](packages-process-domain-roles.md)
  - [domain: state (parte 1)](packages-process-domain-state-1.md)
  - [domain: state (parte 2)](packages-process-domain-state-2.md)

```mermaid
graph TD
    g_domain__checks["checks (grupo)"]
    g_domain__landing__2["landing parte 2 (grupo)"]
    g_domain__roles["roles (grupo)"]
    g_domain__state["state (grupo)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    m_domain__adr_dated["domain.adr_dated"]
    m_domain__adr_numbers["domain.adr_numbers"]
    m_domain__board["domain.board"]
    m_domain__board_schema["domain.board_schema"]
    m_domain__cost_history["domain.cost_history"]
    m_domain__gate_priority["domain.gate_priority"]
    m_domain__hold["domain.hold"]
    m_domain__landing["domain.landing"]
    m_domain__adr_dated --> m_domain__adr_numbers
    m_domain__board --> g_domain__landing__2
    m_domain__board --> g_domain__roles
    m_domain__board --> g_domain__state
    m_domain__board --> m_domain__hold
    m_domain__board_schema --> m_domain__board
    m_domain__cost_history --> g_domain__state
    m_domain__cost_history --> g_pkg__agentic__shared
    m_domain__gate_priority --> g_domain__checks
    m_domain__gate_priority --> g_domain__state
    m_domain__landing --> g_domain__checks
    m_domain__landing --> g_domain__landing__2
    m_domain__landing --> g_domain__roles
    m_domain__landing --> g_domain__state
    m_domain__landing --> m_domain__adr_numbers
```
