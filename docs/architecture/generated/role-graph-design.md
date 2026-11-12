Esta página muestra los nodos de la fase `design` del grafo de roles y las aristas que la unen con otras fases; un nodo `<fase> (fase)` es otra fase, que se detalla en su página. Una flecha punteada es una arista condicional y una llena es fija.

- Padre: [Grafo de roles](role-graph.md)
- Hermanas:
  - [build](role-graph-build.md)
  - [classify](role-graph-classify.md)
  - [define](role-graph-define.md)
  - [deliver](role-graph-deliver.md)
  - [Escalado y reanudación](role-graph-escalation.md)
  - [land](role-graph-land.md)
  - [plan](role-graph-plan.md)

```mermaid
graph TD
    design_review["design_review"]
    phase_define["define (fase)"]
    phase_plan["plan (fase)"]
    ui_screens["ui_screens"]
    ux_flow["ux_flow"]
    ux_walk["ux_walk"]
    write_copy["write_copy"]
    design_review -.-> phase_define
    design_review -.-> phase_plan
    design_review -.-> ui_screens
    design_review -.-> write_copy
    phase_define -.-> ux_flow
    phase_define -.-> write_copy
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
    write_copy -.-> phase_plan
```

Pueden escalar: `design_review`, `ui_screens`, `ux_flow`, `ux_walk`, `write_copy`
