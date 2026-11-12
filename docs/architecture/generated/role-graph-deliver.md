Esta página muestra los nodos de la fase `deliver` del grafo de roles y las aristas que la unen con otras fases; un nodo `<fase> (fase)` es otra fase, que se detalla en su página. Una flecha punteada es una arista condicional y una llena es fija.

- Padre: [Grafo de roles](role-graph.md)
- Hermanas:
  - [build](role-graph-build.md)
  - [classify](role-graph-classify.md)
  - [define](role-graph-define.md)
  - [design](role-graph-design.md)
  - [Escalado y reanudación](role-graph-escalation.md)
  - [land](role-graph-land.md)
  - [plan](role-graph-plan.md)

```mermaid
graph TD
    __end__["__end__"]
    deliver["deliver"]
    evaluated["evaluated"]
    phase_build["build (fase)"]
    phase_define["define (fase)"]
    phase_plan["plan (fase)"]
    deliver -.-> __end__
    deliver -.-> phase_build
    evaluated --> __end__
    phase_build -.-> __end__
    phase_build -.-> deliver
    phase_build -.-> evaluated
    phase_define -.-> __end__
    phase_plan -.-> __end__
```

Pueden escalar: `deliver`
