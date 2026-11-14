---
status: accepted
date: 2026-11-13
---

# La porción de Spec entra a un rol solo por `context_for`

GH-594 parte 3a (GH-713). La entrega de una feature con Spec (ADR-0095) necesita que el rol lea
la porción aprobada: el id, los criterios, las pantallas con su texto y los riesgos. Este
documento fija por dónde entra ese dato y por dónde no. La parte 3a deja el cableado inerte:
nada pone `state["spec"]`, la tabla de bloques va vacía y `run_role` no pasa ningún bloque.

## Contexto

Un prompt de rol tiene dos entradas: el prompt del sistema (que `render` arma desde el texto
crudo, más el playbook y los tiers) y el mensaje, que arma `context_for` desde el documento de
la tarea. El hash del prompt del sistema identifica la versión del rol en `eval compare`: un
dato que cambia por tarea no puede entrar ahí, porque cada tarea sería otro prompt.

## Decisión

- **El dato entra solo por `context_for`.** `spec_brief.fenced_section` arma la sección
  `## Approved Spec slice`, con el dato entre dos líneas `SPEC_FENCE`, y `context_for` la agrega
  después de las secciones declaradas y antes de las decisiones del humano, solo si hay
  `state["spec"]`. Nunca pasa por `render`, por el prompt del sistema ni por su hash. Sin spec,
  la salida de `context_for` es byte a byte la de hoy. `spec_brief` es el único módulo de
  Process que escribe `SPEC_FENCE`, y no lee el archivo de la Spec: solo el `SpecContract`
  que recibe.
- **Solo un bloque constante por grupo entra al hash.** El prompt del sistema puede llevar, con
  spec, un bloque fijo por grupo de roles (las reglas de cómo leer el dato). `SPEC_BLOCKS` los
  nombra por slug y va vacía; `with_playbook` ganó `spec_block`, vacío por defecto, que se une
  después del playbook y de los tiers con `\n\n` y queda dentro del hash. Con los tres vacíos
  devuelve el mismo objeto `Prompt`, de modo que ningún hash de hoy cambia.
- **`extra_input` se descartó.** Es texto que el llamador lee en cada corrida y se agrega al
  mensaje de cada intento; llevaría la porción a cada delta de una sesión reanudada, repitiendo
  lo que el rol ya tiene.
- **Forma del costo (estándar 8).** Una porción de a lo sumo 64 KiB (`SPEC_MAX_BYTES`) por
  prompt inicial o frío; cero por delta de una sesión reanudada; cero en esta parte, porque
  nada la pone todavía. No se agrega ninguna señal.
- **La guarda duplica a propósito el validador de Knowledge.** `spec_brief.data_refusal`
  repite las reglas de datos de `core.knowledge.domain.spec` (salto de línea, la valla sola en
  una línea, `{{`, `<!--`, tamaño). Process no puede importar Knowledge, y el dato vuelve desde
  el documento de la tarea al reanudar: se revisa de nuevo antes de armar el prompt. La razón
  es una palabra de un conjunto cerrado y nunca copia el texto del campo.
- **Dónde se aplica.** `run_role` llama a `data_refusal` dentro del `try` que ya convierte un
  `ValueError` en `PromptFailure`: una porción rechazada escala sin reintento y antes de
  cualquier llamada al modelo.

## Reglas que decide esta parte y ejecutan otras

- **GH-718** (puerto `Prompts.spec_block`, su adaptador y el preflight) hereda tres reglas: el
  bloque se lee dentro del manejador de `PromptFailure` de `run_role`; el nombre del bloque es
  solo un identificador, y uno inválido levanta `ValueError`, nunca `KeyError`; el bloque se
  pasa solo en el camino donde `prompt is None`.
- **GH-594 parte 4b** debe llevar a cada llamador que pasa su propio `prompt` (develop,
  interview, design, final_validation, copy, qa_performance) por `context_for` y por la misma
  guarda de datos, de a un llamador por vez. Hasta entonces esos llamadores no ven la porción.

## Verificación

Se comparó una vez `load_prompt` de base contra esta cabeza sobre los archivos de prompt
reales, para los 37 slugs de `ROLES`, con y sin tiers: texto y hash idénticos en las 74
combinaciones. No se versionan hashes dorados de los prompts reales; las pruebas fijan la
fórmula sobre un directorio sintético.

## Adenda (GH-718): cómo se carga y se revisa el bloque constante

- **Una instantánea de una sola tabla.** `FilesystemPrompts` lee, al construirse, cada nombre
  distinto de `SPEC_BLOCKS` (la única tabla; ningún código de producción pasa `blocks=`) y
  guarda el texto o el error de lectura, como hace con `raw` (GH-409). `Prompts.spec_block`
  sirve esa instantánea y nunca el disco del momento: editar o borrar el archivo bajo una
  corrida no cambia nada. Un error de lectura no rompe la construcción; lo levanta la llamada
  de ese nombre. Un nombre fuera de la instantánea es `ValueError`, nunca `KeyError`.
- **Qué directorios puede alcanzar un nombre.** `block_name_ok` (`^[a-z0-9][a-z0-9_-]*$`) es la
  única guarda entre una entrada de la tabla y el sistema de archivos: un nombre que no la pasa
  se guarda como `ValueError` y no se le arma ruta. Las lecturas llegan solo a
  `.agentic/prompts/spec/<nombre>.md` del producto (que gana) y a
  `agentic/core/process/domain/prompts/spec/<nombre>.md` del sistema. No se escribe nada.
- **Sin tabla no se lee ningún archivo.** Con `SPEC_BLOCKS` vacía, ningún camino abre un archivo
  bajo `prompts/spec/`, y todo texto y hash de prompt es idéntico al anterior.
- **El preflight rechaza por cuatro razones**, cada una con un solo productor y un mensaje
  `spec block <nombre>: <razón>` que nunca incluye el texto del bloque: `name` (decide
  `block_name_ok`, sin leer el bloque), `missing` (decide el preflight, por el error de lectura),
  `template` (`{{`) y `fence` (una línea que, recortada, es `SPEC_FENCE`), estas dos de
  `block_refusal`. El preflight no recibe tabla propia: recorre `spec_block_names()` del puerto.
- **Por qué `<!--` se permite.** El bloque es texto del dueño del producto, no un dato que una
  tarea trajo: la guarda de datos (`data_refusal`) rechaza `<!--` porque un campo de la porción
  puede venir forjado, y un bloque no.
- **Dónde se lee.** `run_role` pide el bloque dentro del `try` de `PromptFailure`, solo con
  `state["spec"]` presente, con la tabla nombrando al rol y sin `prompt` del llamador; un fallo
  escala sin reintento y antes de toda llamada al modelo.
