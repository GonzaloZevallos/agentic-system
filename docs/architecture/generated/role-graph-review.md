Esta página muestra los nodos de la fase `review` del grafo de roles y las aristas que la unen con otras fases; un nodo `<fase> (fase)` es otra fase, que se detalla en su página. Una flecha punteada es una arista condicional y una llena es fija.

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
    fix_important["fix_important"]
    owner_rules["owner_rules"]
    phase_build["build (fase)"]
    verify["verify"]
    fix_important -.-> phase_build
    owner_rules -.-> phase_build
    phase_build -.-> fix_important
    phase_build -.-> owner_rules
    phase_build -.-> verify
    verify -.-> phase_build
```

Pueden escalar: `fix_important`, `owner_rules`, `verify`
