---
status: accepted
date: 2026-10-26
---

# Verificación en dos niveles: puerta rápida en cada entrega, suite completa una vez por tarea

GH-358. Hoy cada entrega de subtarea corre la suite completa del producto, y la puerta
de integración, la de merge y el CI la vuelven a correr. En apostrophe la puerta es el
61 % del tiempo de sesión de los roles (212,8 h de 348 h en 973 llamadas, mediana de 16,1
min cuando pasa) y el modelo es apenas el 6 % del tiempo de una sesión de Developer.

## Decisión

- **La forma: `delivery_checks:`.** Una clave opcional de `stack.yaml`, una lista de
  nombres de `checks`: el nivel rápido, el que corre en cada entrega y reentrega. Se lee en
  la base de la tarea como el resto de las claves. Ausente significa sin niveles: el camino
  de siempre, byte a byte. Una lista igual a todo `checks` es válida: los niveles están
  activos y el nivel completo es la corrida sin formas afectadas.
- **Por qué no un `tier:` por chequeo.** Parte la declaración en dos lugares, deja una
  tercera combinación (un chequeo sin `tier`) que hay que decidir, y el orden de ejecución y
  las formas afectadas (ADR-0046) ya viven por nombre en otras claves. Una lista aparte se
  valida contra `checks` en un solo sitio y se lee con un solo método del port,
  `Stack.delivery_checks(revision)`.
- **Qué corre dónde.** Entrega y reentrega de subtarea, entrega del integrador, `fix`,
  `sync_master` y pasada final de importantes del aterrizaje (todas por `develop`): el
  nivel rápido, con formas afectadas. El nodo `full_checks`, la puerta de merge
  (`merge.run_checks`) y el corte de aterrizaje: todo `checks`, sin forma afectada. La
  revisión y `diagnose` reproducen objeciones con todo `checks`, sin cambios.
- **El nodo `full_checks`.** Un nodo del sistema, sin rol ni costo de modelo (el patrón de
  QA Performance, ADR-0060, sin el rol). `after_integrate` manda aquí cuando no hay nada
  despachado y toda unidad está aprobada, antes de `copy_review`, `final_validation` y
  `qa_performance`; siempre vuelve a `integrate`. Corre en el checkout de la tarea, exige
  árbol limpio al empezar y lo deja limpio (`restore_clean`, sin `-x`), verde o rojo.
  Escribe el registro `full_checks` del frontmatter y la sección `Full checks`. Una corrida
  roja abre una fila `blocking`, de alcance `plan`, por chequeo fallido y reabre la
  integración con `returned_by: "full_checks"`, contando en `pair_for(target)`.
- **`deliver` falla cerrado.** Bajo niveles empuja sólo con un registro pasado cuya cabeza
  difiere de `HEAD` únicamente en el documento de la propia tarea. Con registro ausente o
  viejo no empuja: marca `full_checks_healed: <cabeza>` y vuelve a `integrate`. Un segundo
  rechazo sobre la misma cabeza escala con la clase `system_delivery`, para que la tarea no
  pueda dar vueltas en silencio.
- **El fan-in marca la cabeza.** `integrate` escribe `task_head` en cada pasada, las
  reanudadas incluidas, sólo si el producto declara niveles; así una cabeza que movió una
  devolución o un aterrizaje es una cabeza sobre la que nadie corrió la suite.
- **Los aterrizajes reanudados pasan otra vez por el nodo.** Una reanudación pasa por
  `integrate`, que marca la cabeza nueva, y `after_integrate` vuelve a mandar a
  `full_checks`. Así la verificación completa antes del merge se mantiene en el camino
  donde el humano aterriza (requisito 2: nunca menos verificación antes del merge).
- **El prompt.** Las plantillas `developer.md` y `developer_integrator.md` no se editan.
  Bajo niveles, `develop` agrega una sección escrita por el sistema (`tier_section`) después
  de renderizar, como se agrega el playbook (ADR-0043); su hash cubre el crudo más la
  sección. Sin niveles, el texto y el hash son los de antes. La sección de entrega del
  Developer nombra el nivel que corrió.
- **Rechazado por ahora.** Diferir la corrida completa al CI de la rama (`ci:` puede
  declararse, pero el nodo local es el único camino cubierto por `deliver`), y que la puerta
  de merge reutilice un registro `full_checks` verde cuando master no se movió. Ambos ahorran
  más y piden decidir la frescura del registro; quedan como seguimiento.

- **Señales (estándar 7).** Una sola señal nueva en la familia de documentos: el registro
  `full_checks` del frontmatter, sin texto libre (la salida de un chequeo fallido va sólo a
  la sección `Full checks`, no al registro). Sin span nuevo. Pero la clase de escalada
  `system_delivery` es un valor nuevo del atributo `agentic.task.paused` del span de la
  tarea y una familia nueva `escalations.system_delivery` en la fila de escaladas del
  tablero; ambos cambian de forma y quedan fijados en `test_dashboards.py`. El costo por
  entrega no cambia (el nodo no gasta modelo) y el retrabajo de las corridas rojas lo cuenta
  el `change_history` existente.

## Costo y efecto estimado

Por tarea: `D × rápido + (1 + R + L) × completo + R × (Developer + revisión)`, con D
entregas, R reaperturas después de `integrate` y L pasadas de aterrizaje que mueven la
cabeza. Hoy apostrophe corre unas 13,5 puertas y unas 2,95 h de puerta por tarea.

- **Escenario A, niveles sin formas afectadas** (rápido = todo salvo e2e). Si e2e es la mitad
  de la mediana, cada entrega ahorra unos 8 min (−1,8 h por tarea) contra
  +(1 + R + L) × 20 min: del −35 al −45 % de las horas de puerta con R de 0,3 a 0,5 y L de 0
  a 1. Si e2e es un cuarto: cerca del −10 a −20 %.
- **Escenario B, además pytest y vitest afectados:** cerca del −55 a −65 %.

El ahorro es condicional: sin formas afectadas viene casi todo de sacar e2e de la puerta de
entrega, y depende de su parte en la mediana. Las cifras son fórmulas sobre la evidencia, no
mediciones. R no tiene historia: una corrida completa roja vuelve al integrador, que no
escribió el código roto, y su cota es un supuesto.

## Consecuencias

Esta tarea no corre bajo sus propios niveles: la clave se lee en la base. La declaración de
apostrophe (todo salvo e2e, y formas afectadas) es un seguimiento fuera de esta tarea.
