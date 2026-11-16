Esta página muestra los módulos de la capa `domain` de `agentic.core.measurement` y los imports que hacen; un nodo `<nombre> (paquete)` es otra capa del paquete o un paquete de fuera, que no se detalla aquí. La capa no cabe en un diagrama: está partida en tandas de módulos consecutivos, y un módulo de otra tanda aparece como nodo sin sus imports.

- Padre: [Paquetes de measurement](packages-measurement.md)
- Hermanas:
  - [measurement: adapters](packages-measurement-adapters.md)
  - [measurement: application](packages-measurement-application.md)
  - [measurement: domain (2)](packages-measurement-domain-2.md)

```mermaid
graph TD
    g_pkg__agentic__core__process["agentic.core.process (paquete)"]
    g_pkg__agentic__shared["agentic.shared (paquete)"]
    m_domain__cycle["domain.cycle"]
    m_domain__dashboards["domain.dashboards"]
    m_domain__evaluation["domain.evaluation"]
    m_domain__findings_report["domain.findings_report"]
    m_domain__issue_listing["domain.issue_listing"]
    m_domain__metrics["domain.metrics"]
    m_domain__parallel_factor["domain.parallel_factor"]
    m_domain__planning["domain.planning"]
    m_domain__scorecard["domain.scorecard"]
    m_domain__task_scores["domain.task_scores"]
    m_domain__cycle --> g_pkg__agentic__core__process
    m_domain__cycle --> m_domain__parallel_factor
    m_domain__dashboards --> m_domain__task_scores
    m_domain__evaluation --> g_pkg__agentic__core__process
    m_domain__evaluation --> m_domain__metrics
    m_domain__evaluation --> m_domain__scorecard
    m_domain__findings_report --> g_pkg__agentic__core__process
    m_domain__issue_listing --> g_pkg__agentic__core__process
    m_domain__metrics --> g_pkg__agentic__core__process
    m_domain__metrics --> g_pkg__agentic__shared
    m_domain__metrics --> m_domain__cycle
    m_domain__parallel_factor --> g_pkg__agentic__core__process
    m_domain__planning --> g_pkg__agentic__core__process
    m_domain__planning --> m_domain__metrics
    m_domain__planning --> m_domain__parallel_factor
```
