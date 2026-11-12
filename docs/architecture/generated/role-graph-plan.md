Esta página muestra los nodos de la fase `plan` del grafo de roles y las aristas que la unen con otras fases; un nodo `<fase> (fase)` es otra fase, que se detalla en su página. Una flecha punteada es una arista condicional y una llena es fija.

- Padre: [Grafo de roles](role-graph.md)
- Hermanas:
  - [build](role-graph-build.md)
  - [classify](role-graph-classify.md)
  - [define](role-graph-define.md)
  - [deliver](role-graph-deliver.md)
  - [design](role-graph-design.md)
  - [Escalado y reanudación](role-graph-escalation.md)
  - [land](role-graph-land.md)

```mermaid
graph TD
    __end__["__end__"]
    phase_build["build (fase)"]
    phase_classify["classify (fase)"]
    phase_define["define (fase)"]
    phase_design["design (fase)"]
    plan["plan"]
    split["split"]
    tracker_only["tracker_only"]
    phase_classify -.-> plan
    phase_define -.-> plan
    phase_design -.-> plan
    plan -.-> phase_build
    plan -.-> phase_define
    plan -.-> split
    plan -.-> tracker_only
    split -.-> __end__
    tracker_only -.-> __end__
```

Pueden escalar: `plan`, `split`, `tracker_only`
