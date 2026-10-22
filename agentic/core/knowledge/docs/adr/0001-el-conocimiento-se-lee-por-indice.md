---
status: accepted
date: 2026-10-21
---

# El conocimiento de producto se lee por índice, no entero

Hasta ahora `knowledge.assemble` entregaba a cada rol con `reads_knowledge` el Brief, todos los
glosarios, todas las capacidades y todos los ADR, enteros y en cada llamada: unos 800 KB en
apostrophe (del orden de 200k tokens), y crece con cada tarea. `CODING_STANDARDS.md` (17 KB)
iba entero al Developer y al Tech Lead. Decisión del dueño (2026-10-21, GH-292): los roles
reciben un índice, no los documentos.

## Decisión

- **Dos niveles.** El rol recibe primero un índice de áreas (`product`, `adr`, `standards`),
  una línea por área con el archivo que la lista y su cantidad de entradas. Cada archivo de área
  (`.agentic/index/<area>.md`) tiene una línea por entrada: ruta, título (el primer `#`), el
  área y las etiquetas de rol. El texto completo se abre con `Read`, solo cuando hace falta.
  El código es `build_index` en `domain/knowledge_index.py`; es puro.
- **Se genera al leer, nunca se commitea.** El índice sale del frontmatter y de las rutas, así
  que un archivo commiteado solo podría quedar viejo respecto de lo que describe.
- **Se regenera en cada intento de rol** que lee por índice, sobre el `cwd` de ese intento
  (reanudaciones, merges de dependencias y ADR que la tarea agrega a mitad de corrida quedan
  al día). El escritor escribe los archivos nuevos antes de borrar los viejos, cada uno de
  forma atómica, y solo bajo `.agentic/index/`.
- **`.agentic/index/` entra en `exclude_system_files`**, que resuelve
  `git rev-parse --git-common-dir`, así que vale también en worktrees. Nunca se commitea.
- **Qué va entero** lo decide una sola función, `always_whole`, y ningún otro módulo:
  `BRIEF.md`, `CONTEXT-MAP.md`, todo `CONTEXT.md` que el llamador nombra (la raíz, o lo que enlaza el mapa; nunca inferido del nombre o
  el tamaño de un archivo) y toda sección `## ` de `CODING_STANDARDS.md` cuya línea siguiente
  es exactamente `<!-- agentic:always -->`. Nada llega a un rol de las dos formas.
- **Las rutas de las entradas son relativas al `cwd`** del rol, que es el checkout de la tarea
  o de la subtarea. ADR y capacidades se abren enteros en su ruta real; cada sección no marcada
  de los estándares es su propio archivo, `.agentic/index/standards/<nn>-<slug>.md`, nunca un
  rango de líneas de un documento más grande.
- **ADR reemplazados salen del índice** solo cuando el `status:` del frontmatter empieza con
  `superseded` (`superseded()`). Prosa o adendas que mencionan el reemplazo no cuentan. Su
  ruta sigue siendo legible.
- **Etiquetas cerradas.** Las áreas son `AREAS`; el frontmatter opcional `roles: [slug, …]` se
  valida contra `{r.slug for r in roles.ROLES}`. Un valor fuera del conjunto lanza `ValueError`
  que nombra el archivo y el slug. Una ruta fuera de las áreas también.
- **Los estándares pasan por el mismo constructor** con `standards_whole`, que elige quien
  llama. Todo rol `kind="verifier"` (Tech Lead en plan, entrevista, review, review liviana y
  efímero) los recibe enteros, byte a byte como antes; solo el Developer y el integrador
  pueden recibirlos por índice. Las secciones marcadas solo se leen en archivos de estándares:
  en un ADR o una capacidad se abrirían enteras junto con el resto del archivo, y el rol las
  recibiría de dos formas.
- **Los roles del lado de PM** (`pm_interviewee`, `product_analyst`, `pm`, `pm_consultant`,
  `pm_bug`, `pm_consolidator`, `pm_final`) no tenían herramientas; para seguir el índice reciben
  `Read` y `Glob`, con reglas de permiso limitadas a `docs/**`, `.agentic/index/**`, los
  archivos de conocimiento de la raíz y los `CONTEXT.md` que enlaza el mapa. Todo lo demás se
  deniega con la razón del rol (ADR-0011, cuarta adenda).
- **La única vuelta atrás es revertir el merge.** No hay un interruptor `whole`: dos caminos
  harían que lo medido no fuera lo que corre.
- **Este ADR no lo lee ningún rol:** está fuera del conjunto que `FilesystemKnowledge` lee
  (`docs/adr/` de la raíz del producto). Es un hecho dicho, no un defecto.

## Acciones del humano

- Marcar los ADR-0032 y ADR-0033 con `status: superseded`; hoy la exclusión no excluye nada.
- Agregar `<!-- agentic:always -->` bajo la sección 9 de `CODING_STANDARDS.md` de apostrophe
  (aislamiento entre negocios). Hasta entonces esa regla llega a los roles solo como una
  entrada del índice.
- Declarar un `bl-*` nuevo al mergear: este cambio no toca `domain/prompts/`, así que
  `prompts.fingerprint` no se mueve y las lecturas segmentadas por fingerprint no lo ven.
  La comparación de costo es `agentic eval compare bl-2026-10-06b <nuevo>`, y es la única
  fuente de afirmaciones en dólares; los conteos de bytes nunca se presentan como costo.

## Consecuencias

- El prompt de cada rol baja de todo el corpus a lo siempre entero más el índice de áreas.
  Cada `Read` agrega un turno y reenvía la conversación, así que si los dólares bajan no se
  sabe antes de medir.
- Un rol puede omitir una entrada relevante sin dejar rastro; el Developer es el más expuesto.
  Se vigilan las devoluciones por categoría y los escalados de clase `questions`.
- Una sesión reanudada conserva el índice de áreas que ya vio.
- Cada intento recorre `docs/` una vez y escribe unos pocos archivos pequeños: el costo es
  lineal en la cantidad de archivos de conocimiento, no en el tamaño de la tarea.
