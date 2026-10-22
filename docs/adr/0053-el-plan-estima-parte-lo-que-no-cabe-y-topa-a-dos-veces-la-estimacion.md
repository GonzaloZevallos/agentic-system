---
status: accepted
date: 2026-10-22
---

# El plan estima, parte lo que no cabe y topa a dos veces la estimación

GH-53 de apostrophe ("la app entera en inglés") fue una rama de 400 archivos y 12
subtareas que vivió días mientras master cambiaba las mismas pantallas, falló sus chequeos
en cada sincronización y se cerró tras ~$560; el segundo intento costó ~$300 más. Ningún
rol estima hoy, así que el tamaño se descubre construyendo. Decisión del dueño
(2026-10-21): estimar antes de construir y partir lo que no cabe.

## Decisión

- **La estimación es un dato del plan.** `TechPlan` lleva `estimated_cost_usd` (el costo
  total de la tarea, lo ya gastado incluido) y `estimated_files` (los `changedFiles` del PR,
  tests, ADRs y generados incluidos). `plan_check` rechaza archivos < 1, un costo menor que
  lo ya gastado y, con al menos 5 tareas en el balde de su cantidad de subtareas, un costo
  menor que la mitad de la mediana del balde. El histórico lo arma el código y llega al
  prompt como placeholder `{{cost_history}}`; la prosa `### Estimate` sólo lo explica.
- **Una sola función decide.** `rules.estimate_fits` usa $150 y 40 archivos (`>`, constantes
  del módulo) sobre el mayor entre la estimación y el p90 del balde. `rules.estimate_cap`
  es el factor 2. `rules.estimate_in_force` es la única lectura de la estimación vigente.
- **Partir antes de construir.** Si no cabe, el plan trae `parts` (≥ 2) y ninguna subtarea.
  El nodo `split` crea un issue por parte, con `agentic`, `Blocked by:` de sus
  dependencias, los invariantes del plan, el enlace al documento original y un marcador
  idempotente. La puerta de bloqueos del poller (ADR-0039) hace que cada parte aterrice
  antes de la siguiente. La tarea original termina en el final `split`, sin PR ni
  reanudación. Profundidad máxima 1: una parte que tampoco cabe escala con la clase
  `estimate`.
- **Se descarta y se repite.** Los ADRs y el glosario que escribió la entrevista de la
  tarea partida quedan en su rama, que no se mergea. Cada parte corre su propia entrevista.
  Se acepta una entrevista de solución por parte a cambio de no tener un mecanismo que
  transfiera y renumere ADRs.
- **Tope por estimación.** Convive con el tope plano del ADR-0004, que queda intacto: este
  enmienda su frase "no depende del plan" solo para este segundo tope. Es `2 ×
  estimate_in_force`, se mira antes de cada intento, solo frena lo que no arrancó, y una
  tarea sin estimación no lo tiene. Alcanzado, escala con la clase nueva `estimate` y la
  estimación nueva que calcula el código. Esa escalada exige decisión del humano; nadie la
  reanuda sola. La estimación nueva solo se vuelve vigente (`estimate_revisions`) cuando el
  humano reanuda, con `--estimate` o aceptando la propuesta.
- **Aprender de la diferencia.** Al cierre, `actual_files` (de `changedFiles` del PR, por
  una sola función de dominio que llaman `agentic close` y el auto-merge, también para un
  PR cerrado sin fusionar) queda junto a `cost_usd` y la estimación. El scorecard suma una
  columna informativa, la mediana del cociente real/estimado con `n`, fuera del orden
  lexicográfico.

## Riesgos conocidos

- La estimación sigue siendo del mismo modelo que escribe el plan; el guardia y el p90
  acotan el sesgo, no lo eliminan. Se mide con el cociente.
- Si el PR de una parte se cierra sin fusionar, la cadena queda detenida hasta que el
  humano decida.
- Cada parte repite la entrevista de solución.
