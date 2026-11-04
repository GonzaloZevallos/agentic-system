Esta página muestra los módulos de la capa `application` de `agentic.core.measurement` y los imports que hacen; un nodo `<nombre> (paquete)` es otra capa del paquete o un paquete de fuera, que no se detalla aquí.

- Padre: [Paquetes de measurement](packages-measurement.md)
- Hermanas:
  - [measurement: adapters](packages-measurement-adapters.md)
  - [measurement: domain](packages-measurement-domain.md)

```mermaid
graph TD
    g_pkg__agentic__core__process["agentic.core.process (paquete)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    g_pkg__domain["domain (paquete)"]
    m_application__dashboards["application.dashboards"]
    m_application__eval_commands["application.eval_commands"]
    m_application__ports["application.ports"]
    m_application__dashboards --> g_pkg__domain
    m_application__eval_commands --> g_pkg__agentic__core__process
    m_application__eval_commands --> g_pkg__agentic__shared
    m_application__eval_commands --> g_pkg__domain
    m_application__eval_commands --> m_application__ports
```
