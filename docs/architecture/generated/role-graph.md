# Grafo de roles

Generado por `scripts/docs/generate.py` desde `agentic.composition:graph` (`get_graph(xray=True).draw_mermaid()`). No se edita a mano.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	classify(classify)
	define(define)
	diagnose(diagnose)
	validate(validate)
	ux_flow(ux_flow)
	ux_walk(ux_walk)
	ui_screens(ui_screens)
	design_review(design_review)
	consolidate(consolidate)
	write_copy(write_copy)
	plan(plan)
	pipeline(pipeline)
	integrate(integrate)
	final_validation(final_validation)
	copy_review(copy_review)
	qa_performance(qa_performance)
	full_checks(full_checks)
	fix_important(fix_important)
	owner_rules(owner_rules)
	deliver(deliver)
	discard(discard)
	split(split)
	tracker_only(tracker_only)
	escalate(escalate)
	pause(pause)
	__end__([<p>__end__</p>]):::last
	__start__ --> classify;
	classify -.-> define;
	classify -.-> diagnose;
	classify -.-> escalate;
	classify -.-> plan;
	consolidate -.-> define;
	consolidate -.-> escalate;
	consolidate -.-> plan;
	consolidate -.-> ux_flow;
	consolidate -.-> write_copy;
	copy_review -.-> escalate;
	copy_review -.-> integrate;
	define -.-> escalate;
	define -.-> validate;
	deliver -.-> __end__;
	deliver -.-> escalate;
	deliver -.-> integrate;
	design_review -.-> consolidate;
	design_review -.-> escalate;
	design_review -.-> plan;
	design_review -.-> ui_screens;
	design_review -.-> write_copy;
	diagnose -.-> escalate;
	diagnose -.-> validate;
	discard -.-> __end__;
	discard -.-> escalate;
	escalate --> pause;
	final_validation -.-> escalate;
	final_validation -.-> integrate;
	fix_important -.-> escalate;
	fix_important -.-> integrate;
	full_checks -.-> escalate;
	full_checks -.-> integrate;
	integrate -.-> __end__;
	integrate -.-> copy_review;
	integrate -.-> deliver;
	integrate -.-> escalate;
	integrate -.-> final_validation;
	integrate -.-> fix_important;
	integrate -.-> full_checks;
	integrate -.-> owner_rules;
	integrate -.-> pipeline;
	integrate -.-> qa_performance;
	owner_rules -.-> escalate;
	owner_rules -.-> integrate;
	pause -.-> __end__;
	pause -.-> classify;
	pause -.-> consolidate;
	pause -.-> define;
	pause -.-> deliver;
	pause -.-> design_review;
	pause -.-> diagnose;
	pause -.-> discard;
	pause -.-> escalate;
	pause -.-> integrate;
	pause -.-> plan;
	pause -.-> split;
	pause -.-> tracker_only;
	pause -.-> ui_screens;
	pause -.-> ux_flow;
	pause -.-> ux_walk;
	pause -.-> validate;
	pause -.-> write_copy;
	pipeline --> integrate;
	plan -.-> escalate;
	plan -.-> integrate;
	plan -.-> split;
	plan -.-> tracker_only;
	qa_performance -.-> escalate;
	qa_performance -.-> integrate;
	split -.-> __end__;
	split -.-> escalate;
	tracker_only -.-> __end__;
	tracker_only -.-> escalate;
	ui_screens -.-> design_review;
	ui_screens -.-> escalate;
	ui_screens -.-> plan;
	ui_screens -.-> write_copy;
	ux_flow -.-> escalate;
	ux_flow -.-> plan;
	ux_flow -.-> ux_walk;
	ux_flow -.-> write_copy;
	ux_walk -.-> escalate;
	ux_walk -.-> plan;
	ux_walk -.-> ui_screens;
	ux_walk -.-> ux_flow;
	ux_walk -.-> write_copy;
	validate -.-> define;
	validate -.-> diagnose;
	validate -.-> discard;
	validate -.-> escalate;
	validate -.-> plan;
	validate -.-> ux_flow;
	validate -.-> write_copy;
	write_copy -.-> escalate;
	write_copy -.-> plan;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

## Rama `pipeline`

El nodo `pipeline` corre, por cada subtarea, este grafo propio (`agentic.core.process.adapters.langgraph.graph:pipeline_branch`), que el grafo de arriba no expande.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	develop(develop)
	review(review)
	__end__([<p>__end__</p>]):::last
	__start__ -.-> develop;
	__start__ -.-> review;
	develop -.-> __end__;
	develop -.-> review;
	review --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```
