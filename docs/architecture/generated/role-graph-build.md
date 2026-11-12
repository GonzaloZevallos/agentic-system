Esta página muestra los nodos de la fase `build` del grafo de roles y las aristas que la unen con otras fases; un nodo `<fase> (fase)` es otra fase, que se detalla en su página. Una flecha punteada es una arista condicional y una llena es fija.

- Padre: [Grafo de roles](role-graph.md)
- Hermanas:
  - [classify](role-graph-classify.md)
  - [define](role-graph-define.md)
  - [deliver](role-graph-deliver.md)
  - [design](role-graph-design.md)
  - [Escalado y reanudación](role-graph-escalation.md)
  - [land](role-graph-land.md)
  - [plan](role-graph-plan.md)

```mermaid
graph TD
    __end__["__end__"]
    fix_important["fix_important"]
    integrate["integrate"]
    owner_rules["owner_rules"]
    phase_define["define (fase)"]
    phase_deliver["deliver (fase)"]
    phase_plan["plan (fase)"]
    pipeline["pipeline"]
    verify["verify"]
    fix_important -.-> integrate
    integrate -.-> __end__
    integrate -.-> fix_important
    integrate -.-> owner_rules
    integrate -.-> phase_define
    integrate -.-> phase_deliver
    integrate -.-> pipeline
    integrate -.-> verify
    owner_rules -.-> integrate
    phase_deliver -.-> integrate
    phase_plan -.-> integrate
    pipeline --> integrate
    verify -.-> integrate
```

Pueden escalar: `fix_important`, `integrate`, `owner_rules`, `verify`

## Rama `pipeline`

El nodo `pipeline` corre, por cada subtarea, este grafo propio (`agentic.core.process.adapters.langgraph.graph:pipeline_branch`), que el grafo de arriba no expande.

```mermaid
graph TD
    branch___end__["__end__"]
    branch___start__["__start__"]
    branch_develop["develop"]
    branch_review["review"]
    branch_risk_net["risk_net"]
    branch___start__ -.-> branch_develop
    branch___start__ -.-> branch_review
    branch_develop -.-> branch___end__
    branch_develop -.-> branch_risk_net
    branch_review --> branch___end__
    branch_risk_net --> branch_review
```
