Esta página muestra los nodos de la fase `classify` del grafo de roles y las aristas que la unen con otras fases; un nodo `<fase> (fase)` es otra fase, que se detalla en su página. Una flecha punteada es una arista condicional y una llena es fija.

- Padre: [Grafo de roles](role-graph.md)
- Hermanas:
  - [build](role-graph-build.md)
  - [define](role-graph-define.md)
  - [deliver](role-graph-deliver.md)
  - [Escalado y reanudación](role-graph-escalation.md)
  - [land](role-graph-land.md)
  - [plan](role-graph-plan.md)
  - [review](role-graph-review.md)

```mermaid
graph TD
    __start__["__start__"]
    classify["classify"]
    phase_define["define (fase)"]
    phase_plan["plan (fase)"]
    __start__ --> classify
    classify -.-> phase_define
    classify -.-> phase_plan
```

Pueden escalar: `classify`
