Esta página muestra los módulos del grupo `landing` de `domain` y los imports que hacen; un nodo `<nombre> (grupo)` es otro grupo de `domain` (cada grupo tiene su página) y `<nombre> (paquete)` otro paquete: con su página si es un paquete de `process`, sin ella si es de fuera. El grupo se dividió por cantidad: esta es la parte 2 de 2, con módulos consecutivos en orden alfabético, y las otras partes aparecen como nodos `<grupo> parte <n> (grupo)`.

- Padre: [domain](packages-process-domain.md)
- Hermanas:
  - [domain: checks](packages-process-domain-checks.md)
  - [domain: landing (parte 1)](packages-process-domain-landing-1.md)
  - [domain: machine (parte 1)](packages-process-domain-machine-1.md)
  - [domain: machine (parte 2)](packages-process-domain-machine-2.md)
  - [domain: playbook](packages-process-domain-playbook.md)
  - [domain: roles](packages-process-domain-roles.md)
  - [domain: state (parte 1)](packages-process-domain-state-1.md)
  - [domain: state (parte 2)](packages-process-domain-state-2.md)

```mermaid
graph TD
    g_domain__landing__1["landing parte 1 (grupo)"]
    g_domain__roles["roles (grupo)"]
    g_domain__state["state (grupo)"]
    m_domain__landing_order["domain.landing_order"]
    m_domain__merge_hold["domain.merge_hold"]
    m_domain__plan_revision["domain.plan_revision"]
    m_domain__plan_route["domain.plan_route"]
    m_domain__plan_settlement["domain.plan_settlement"]
    m_domain__plan_view["domain.plan_view"]
    m_domain__pull_request["domain.pull_request"]
    m_domain__stacking["domain.stacking"]
    m_domain__landing_order --> g_domain__state
    m_domain__plan_revision --> g_domain__state
    m_domain__plan_settlement --> g_domain__roles
    m_domain__plan_settlement --> g_domain__state
    m_domain__plan_view --> g_domain__roles
    m_domain__plan_view --> g_domain__state
    m_domain__pull_request --> g_domain__landing__1
    m_domain__pull_request --> g_domain__state
    m_domain__stacking --> g_domain__state
```
