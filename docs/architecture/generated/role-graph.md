Esta página muestra el grafo de roles de `agentic.composition:graph` reducido a sus fases: cada fase es un nodo y cada flecha resume las aristas entre dos fases. Una flecha punteada es una arista condicional y una llena es fija. Generada por `scripts/docs/generate.py`; no se edita a mano.

- Fases:
  - [build](role-graph-build.md)
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
    __start__["__start__"]
    phase_build["build (fase)"]
    phase_classify["classify (fase)"]
    phase_define["define (fase)"]
    phase_deliver["deliver (fase)"]
    phase_design["design (fase)"]
    phase_escalation["escalation (fase)"]
    phase_plan["plan (fase)"]
    __start__ --> phase_classify
    phase_build -.-> __end__
    phase_build -.-> phase_define
    phase_build -.-> phase_deliver
    phase_build -.-> phase_escalation
    phase_classify -.-> phase_define
    phase_classify -.-> phase_escalation
    phase_classify -.-> phase_plan
    phase_define -.-> __end__
    phase_define -.-> phase_design
    phase_define -.-> phase_escalation
    phase_define -.-> phase_plan
    phase_deliver -.-> __end__
    phase_deliver -.-> phase_build
    phase_deliver -.-> phase_escalation
    phase_design -.-> phase_define
    phase_design -.-> phase_escalation
    phase_design -.-> phase_plan
    phase_escalation -.-> __end__
    phase_escalation -.-> phase_build
    phase_escalation -.-> phase_classify
    phase_escalation -.-> phase_define
    phase_escalation -.-> phase_deliver
    phase_escalation -.-> phase_design
    phase_escalation -.-> phase_plan
    phase_plan -.-> __end__
    phase_plan -.-> phase_build
    phase_plan -.-> phase_define
    phase_plan -.-> phase_escalation
```
