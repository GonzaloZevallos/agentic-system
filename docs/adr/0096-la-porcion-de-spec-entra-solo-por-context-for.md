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
