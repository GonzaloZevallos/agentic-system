Esta página muestra los nodos de la fase `define` del grafo de roles y las aristas que la unen con otras fases; un nodo `<fase> (fase)` es otra fase, que se detalla en su página. Una flecha punteada es una arista condicional y una llena es fija.

- Padre: [Grafo de roles](role-graph.md)
- Hermanas:
  - [build](role-graph-build.md)
  - [classify](role-graph-classify.md)
  - [deliver](role-graph-deliver.md)
  - [Escalado y reanudación](role-graph-escalation.md)
  - [land](role-graph-land.md)
  - [plan](role-graph-plan.md)
  - [review](role-graph-review.md)

```mermaid
graph TD
    __end__["__end__"]
    consolidate["consolidate"]
    define["define"]
    design_review["design_review"]
    diagnose["diagnose"]
    discard["discard"]
    phase_classify["classify (fase)"]
    phase_plan["plan (fase)"]
    ui_screens["ui_screens"]
    ux_flow["ux_flow"]
    ux_walk["ux_walk"]
    validate["validate"]
    write_copy["write_copy"]
    consolidate -.-> define
    consolidate -.-> phase_plan
    consolidate -.-> ux_flow
    consolidate -.-> write_copy
    define -.-> validate
    design_review -.-> consolidate
    design_review -.-> phase_plan
    design_review -.-> ui_screens
    design_review -.-> write_copy
    diagnose -.-> validate
    discard -.-> __end__
    phase_classify -.-> define
    phase_classify -.-> diagnose
    ui_screens -.-> design_review
    ui_screens -.-> phase_plan
    ui_screens -.-> write_copy
    ux_flow -.-> phase_plan
    ux_flow -.-> ux_walk
    ux_flow -.-> write_copy
    ux_walk -.-> phase_plan
    ux_walk -.-> ui_screens
    ux_walk -.-> ux_flow
    ux_walk -.-> write_copy
    validate -.-> define
    validate -.-> diagnose
    validate -.-> discard
    validate -.-> phase_plan
    validate -.-> ux_flow
    validate -.-> write_copy
    write_copy -.-> phase_plan
```

Pueden escalar: `consolidate`, `define`, `design_review`, `diagnose`, `discard`, `ui_screens`, `ux_flow`, `ux_walk`, `validate`, `write_copy`
