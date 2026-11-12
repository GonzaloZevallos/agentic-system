Esta página muestra los nodos de la fase `define` del grafo de roles y las aristas que la unen con otras fases; un nodo `<fase> (fase)` es otra fase, que se detalla en su página. Una flecha punteada es una arista condicional y una llena es fija.

- Padre: [Grafo de roles](role-graph.md)
- Hermanas:
  - [build](role-graph-build.md)
  - [classify](role-graph-classify.md)
  - [deliver](role-graph-deliver.md)
  - [design](role-graph-design.md)
  - [Escalado y reanudación](role-graph-escalation.md)
  - [land](role-graph-land.md)
  - [plan](role-graph-plan.md)

```mermaid
graph TD
    __end__["__end__"]
    consolidate["consolidate"]
    define["define"]
    diagnose["diagnose"]
    discard["discard"]
    phase_build["build (fase)"]
    phase_classify["classify (fase)"]
    phase_design["design (fase)"]
    phase_plan["plan (fase)"]
    validate["validate"]
    consolidate -.-> define
    consolidate -.-> phase_design
    consolidate -.-> phase_plan
    define -.-> validate
    diagnose -.-> validate
    discard -.-> __end__
    phase_build -.-> define
    phase_build -.-> diagnose
    phase_classify -.-> define
    phase_classify -.-> diagnose
    phase_design -.-> consolidate
    phase_plan -.-> define
    phase_plan -.-> diagnose
    validate -.-> define
    validate -.-> diagnose
    validate -.-> discard
    validate -.-> phase_design
    validate -.-> phase_plan
```

Pueden escalar: `consolidate`, `define`, `diagnose`, `discard`, `validate`
