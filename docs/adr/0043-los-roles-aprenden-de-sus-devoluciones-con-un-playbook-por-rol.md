---
status: accepted
date: 2026-10-05
---

# Los roles aprenden de sus devoluciones: un Reflector propone, un curador de código aplica, una persona fusiona

Dentro de una tarea el ciclo funciona: `## Tech Lead: findings` lleva lo que las
revisiones dejaron abierto y un Developer devuelto retoma su propia conversación. Entre
tareas no pasa nada: lo que un rol hizo mal en una no llega a la siguiente, salvo que la
sesión orquestadora note el patrón y abra un issue (#174 y #175 fueron así). El README
ya diseñaba la mejora del proceso de PjM (ventana de 10 tareas cerradas, revertir si la
métrica empeoró, resumen cada 10), pero ningún prompt ni código la implementaba. Este
ADR decide el mecanismo entero; sale del pedido de GH-182.

El patrón es el de ACE (Agentic Context Engineering, Zhang et al., 2025): un reflector
lee una trayectoria terminada y extrae lecciones; un curador las funde en un playbook
por rol como **deltas incrementales** (agregar, fusionar, retirar, con contadores),
nunca como reescritura, porque reescribir colapsa el contexto y lo sesga hacia la
brevedad. Se recorta a lo que este sistema ya registra: las devoluciones traen una
categoría cerrada (ADR-0016), una ubicación y evidencia.

## Un rol nuevo, el Reflector, y no PjM ni dos roles

El humano autorizó agregar, cambiar o quitar roles para este issue. Se decidió por los
méritos, y los costos de cada opción son:

| Opción | Costo | Por qué pierde o gana |
|---|---|---|
| **Un rol nuevo, Reflector (slug `reflector`, actor, 18 roles)** | Un rol más en la lista y un prompt | **Gana.** Corre fuera de toda tarea, una vez por tarea cerrada, y solo carga las devoluciones y las revisiones que las originaron |
| Dentro de PjM | Cada tarea paga en el contexto de PjM las revisiones que PjM no necesita para enrutar | PjM es el router: su costo se paga en todas las tareas, y cargarle las revisiones lo cobraría siempre. Además PjM no puede ser juez de su propia mejora |
| Dos roles, reflector y curador | El doble de costo por tarea, y las reglas de fusión pasan a ser algo que un modelo puede olvidar | El segundo paso es contabilidad pura: deduplicar, sumar un contador, respetar un tope. El código lo hace mejor, con pruebas |
| Un rol en dos pasos | Igual que dos roles | El segundo paso sigue siendo contabilidad |

## El curador es código

`playbook.apply` (en `core/process/domain/playbook.py`) solo agrega, fusiona, suma una
cita a o retira **un** ítem por delta, contra el playbook que dejó el delta anterior. No
cuesta turnos, sus reglas tienen pruebas unitarias y ningún camino de código toma texto de
un modelo y escribe un playbook entero. Le corresponde decidir qué deltas se aplican; el
Reflector solo propone.

- Un `add` se rechaza salvo que cite una tarea cerrada cuyo `return_history` tenga esa
  categoría, del conjunto cerrado `FindingCategory`, con ese `slug` como responsable
  (`playbook.accountable`). Una tabla única, `(tipo de tarea, categoría, ¿integración?) →
  slug`, alimenta tanto `accountable` como `PLAYBOOK_ROLES`, y todo slug de ella es un rol
  de `roles.ROLES`.
- Una lección es una línea de a lo sumo 240 caracteres, sin salto de línea, backtick,
  `{{` ni `http(s)://`; `apply` rechaza otra cosa. Un playbook tiene a lo sumo 12 ítems:
  pasado el tope hay que fusionar o retirar antes de agregar.
- Un `add` cuyo texto normalizado coincide con uno rechazado, con el mismo slug y
  categoría, se rechaza.

## El playbook vive solo en el overlay del producto

`Prompts.playbook(role)` lee `.agentic/playbooks/<slug>.yaml` del overlay del producto
(ADR-0005). El default del sistema está vacío: una lección pertenece al producto donde
ocurrió el error, y lo aprendido sobre los prompts de este sistema y sobre el código de
un producto son cosas distintas. `load_prompt` agrega una sección `### Playbook` **después**
de rellenar los placeholders, así que no se agrega ningún placeholder y
`preflight.PLACEHOLDERS` queda igual.

## La regla del hash y qué frase de ADR-0019 reemplaza

Este ADR reemplaza, por su nombre, la frase de ADR-0019 «el hash se calcula sobre el texto
crudo». Ahora `Prompt.hash` es `prompt_hash(raw)` con el playbook vacío y
`prompt_hash(raw + "\n" + sección)` con uno no vacío: un cambio de playbook es un cambio de
prompt para comparar. Con el playbook vacío o ausente el texto y el hash son byte a byte los
de hoy. No edita ADR-0005 ni ADR-0019. Reemplaza, además, el diseño de «Mejora del
proceso» de PjM del README, para el mecanismo de lecciones; lo demás de esa sección sigue.

Cada tarea registra `playbooks[slug]`, el hash de la sección con la que corrió el rol por
primera vez, que nunca se pisa; un hash distinto más tarde va a `playbooks_changed[slug]`.
`prompts` sigue siendo «gana el último». El único atributo de span nuevo es
`agentic.role.playbook_hash`. El prompt `reflector.md` corre fuera de las tareas, así que
nunca entra en el `prompts_fingerprint` de una; desde la primera lección fusionada, el
fingerprint de ese rol en ese producto se mueve, a propósito.

## La ventana

La comparación corta segmentos de tareas cerradas que comparten un hash de sección por
rol; la ventana es de 10 tareas por lado y solo cuenta el brazo `default`. Excluye las
tareas con `playbooks_changed` para ese slug y es **no concluyente** con menos de 10 por
lado o si cruza más de una `system_baseline`. Una categoría estrictamente peor propone
retirar el ítem más nuevo que introdujo; 3 devoluciones de una misma categoría para un
mismo rol dentro de la ventana, pese a que hay un ítem, se vuelve una propuesta de chequeo
mecánico en el resumen. Esto es la ventana de 10 y 10 del README, no una corrida del set de
cinco por delta, que sería demasiado cara.

## El camino de escritura y el marcador

`agentic reflect` es un verbo propio; el poller lo llama tras los cierres, solo cuando el
producto tiene tareas elegibles. Toda escritura va a un worktree sobre `agentic/playbook`
y a **un** pull request candidato abierto por producto, nunca con la etiqueta `agentic`.
El cuerpo lleva un bloque bajo el marcador `<!-- agentic:playbook -->`, que es el registro
durable de lo ya reflejado y lo rechazado (el mismo patrón de marcador de ADR-0034).

- Un PR cerrado sin fusionar deja sus tareas como reflejadas y sus `add` como rechazados.
- Un PR fusionado rechaza un `add` solo si falta en el archivo del playbook **en el commit
  de fusión de ese PR**, nunca en `HEAD`.
- Los rechazos persisten en `.agentic/playbooks/rejected.yaml`, los registros en
  `reflected.yaml` y cada 10 tareas cerradas un resumen va a `summaries/<n>.md`, cuyo texto
  es también el cuerpo del PR. Va ahí porque las propuestas de retiro son deltas del mismo
  PR: la persona revisa números y cambios en un solo lugar, y el archivo sobrevive al PR.
- Nada escribe en un documento de tarea cerrado, un checkpoint de tarea, un issue ni la
  rama por defecto del producto. Un fallo del Reflector, del curador o del verbo nunca
  cambia, falla, escala ni reabre una tarea.

`reflect_task` (`application/reflect.py`) arma el input **en código** desde el
`return_history` y las secciones de revisión de las que salieron esas devoluciones: ningún
prompt le pregunta al modelo qué hallazgos se bajaron. Descarta lo no atribuible; sin
devoluciones atribuibles no llama al modelo. Corre en el modelo del actor con
`reflect_max_budget_usd` (1 USD por intento) y devuelve el resultado, sin escribir nada.

## Seguridad (§8)

Lo que el sistema puede hacer ahora: crear y actualizar una rama, `agentic/playbook`, en el
producto, y abrir o actualizar un PR sobre ella con las credenciales de `gh`. El texto de
una lección es texto de modelo derivado de revisiones, que pueden citar issues y diffs.
Llega al prompt de un rol solo por un PR fusionado por una persona; `apply` rechaza
saltos de línea, backticks, `{{`, `http(s)://` y todo lo que pase de 240 caracteres, y el
cuerpo del PR dice que cada lección debe leerse antes de fusionar.

## Costo

A lo sumo `reflect_max_tasks_per_run` (5) tareas × 2 intentos × 1 USD = 10 USD por
invocación. La cota diaria es esa por la cantidad de ciclos del poller que encontraron
tareas elegibles. La primera corrida no refleja nada anterior a `started_at`; el relleno es
explícito con `--since`. `reflect` no emite métricas (la precedente es ADR-0034): su costo
queda en `reflected.yaml` y en el bloque del PR.

## Riesgos conocidos

- **La ventana se seca.** Las tareas excluidas (hash cambiado, línea de base cruzada, otro
  brazo) la adelgazan, y muchas comparaciones quedarán sin concluir. El resumen dice cuántas.
- **«Un caso particular del producto» es juicio del modelo.** Solo lo controla la revisión
  del PR.
- **Editar a mano el cuerpo del PR puede borrar el marcador**; las tareas de ese PR se
  reflejan de nuevo.
- **El informe de aceptación es un acto humano tras fusionar**: con unas 10 tareas con el
  playbook vivo, comparadas con las 10 anteriores, como comentario del issue.
- **El mapeo de la n-ésima devolución a la n-ésima entrega de la revisión** es una
  aproximación: una devolución que agotó el límite del par escala sin registrarse en
  `return_history`, y desfasa en una las siguientes. El Reflector lee entonces la revisión
  vecina, no una inventada.

## Qué no cambia

Ninguna familia de métricas nueva, ninguna `FindingCategory`, ningún valor de
`EscalationClass`, ningún cambio a `ROLE_RETRIES` ni a los topes. Fuera de alcance:
fine-tuning, cambiar categorías o severidades, y aplicar solos los cambios mayores (orden
de etapas, roles), que siguen siendo propuestas para el humano.
