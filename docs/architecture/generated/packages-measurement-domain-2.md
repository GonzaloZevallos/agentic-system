Esta página muestra los módulos de la capa `domain` de `agentic.core.measurement` y los imports que hacen; un nodo `<nombre> (paquete)` es otra capa del paquete o un paquete de fuera, que no se detalla aquí. La capa no cabe en un diagrama: está partida en tandas de módulos consecutivos, y un módulo de otra tanda aparece como nodo sin sus imports.

- Padre: [Paquetes de measurement](packages-measurement.md)
- Hermanas:
  - [measurement: adapters](packages-measurement-adapters.md)
  - [measurement: application](packages-measurement-application.md)
  - [measurement: domain (1)](packages-measurement-domain-1.md)

```mermaid
graph TD
    g_pkg__agentic__core__process["agentic.core.process (paquete)"]
    m_domain__cycle["domain.cycle"]
    m_domain__metrics["domain.metrics"]
    m_domain__parallel_factor["domain.parallel_factor"]
    m_domain__planning["domain.planning"]
    m_domain__playbook_window["domain.playbook_window"]
    m_domain__role_usage["domain.role_usage"]
    m_domain__scorecard["domain.scorecard"]
    m_domain__shadow_report["domain.shadow_report"]
    m_domain__stage_report["domain.stage_report"]
    m_domain__stages["domain.stages"]
    m_domain__task_scores["domain.task_scores"]
    m_domain__playbook_window --> g_pkg__agentic__core__process
    m_domain__playbook_window --> m_domain__metrics
    m_domain__role_usage --> g_pkg__agentic__core__process
    m_domain__scorecard --> g_pkg__agentic__core__process
    m_domain__scorecard --> m_domain__cycle
    m_domain__scorecard --> m_domain__metrics
    m_domain__scorecard --> m_domain__planning
    m_domain__shadow_report --> g_pkg__agentic__core__process
    m_domain__shadow_report --> m_domain__cycle
    m_domain__stage_report --> m_domain__stages
    m_domain__stages --> m_domain__cycle
    m_domain__stages --> m_domain__parallel_factor
    m_domain__task_scores --> g_pkg__agentic__core__process
    m_domain__task_scores --> m_domain__metrics
    m_domain__task_scores --> m_domain__stage_report
    m_domain__task_scores --> m_domain__stages
```
