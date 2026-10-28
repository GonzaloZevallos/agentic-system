---
status: accepted
date: 2026-10-28
---

# El directorio temporal sale de todo repositorio y tiene una partición por producto

GH-397. Enmienda el ADR-0069. Desde GH-391 el directorio temporal de una corrida era
`<producto>/.agentic/tmp/<tarea>/…` y se exportaba como `TMPDIR`. Cuando el producto es este
mismo sistema, el `tmp_path` de pytest queda dentro del repositorio desde el que corre el
paquete y `RunsOnItsOwnCheckout` (ADR-0017) rechaza cada prueba que arma un workspace del
sistema ahí: en GH-392, seis pruebas rojas y un chequeo `tests` que venció tres veces a los
1500 s. Cualquier tarea de agentic-system que corra la suite en un rol o en los chequeos
locales chocaba con la misma pared.

## Decisión

**La raíz del scratch está fuera de todo repositorio.** Es `Configuration.scratch_dir`
(`AGENTIC_SCRATCH_DIR`), por defecto `$XDG_CACHE_HOME/agentic/scratch`, o
`~/.cache/agentic/scratch` si esa variable no existe: en el disco, no en el tmpfs `/tmp`. Todo
consumidor (el directorio de cada intento, el registro de dueño, el listado, el barrido y la
lectura de espacio libre) deriva el camino de `Configuration.scratch_dir`, de
`reaping.product_key` y de las funciones puras de `domain/scratch.py`; ninguno arma un camino
desde `product_dir` ni escribe `.agentic/tmp`.

**Una partición por producto.** El diseño es `<raíz>/<clave del producto>/<tarea raíz>/<tipo>-<8>/`.
El barrido y el listado de un producto tocan solo su partición.

**La negativa.** `refuse_scratch_in_a_repository` rechaza una raíz que cae dentro del
repositorio del producto o del de stage0, con `ScratchInARepository`. Compara repositorios, no
caminos (un worktree es otro directorio y el mismo repositorio), y mide desde el ancestro
existente más cercano de la raíz, porque la raíz se crea de forma perezosa y `repository_at`
responde `None` para un camino que no existe. Una raíz en cualquier otro repositorio se acepta.
Corre en las dos puertas de `refuse_its_own_checkout`: `preflight` (la del CLI) y el final de
`default_ports` (la que comparten `langgraph dev`, Studio y la API).

**`kept/` se queda en el producto.** La evidencia que una tarea cita sigue en
`<producto>/.agentic/kept/<tarea>/` (`TASK_EVIDENCE_DIR`): la escribe el rol, la lee el
revisor y vive 14 días, y no es `TMPDIR`. Es lo único de `kept_path_for` que sigue relativo al
producto.

**El piso de espacio libre lee tres volúmenes**: la raíz del scratch, el del producto (donde
siguen `kept/`, los worktrees y los checkpoints) y `/tmp`. La línea de log del poller gana la
etiqueta `product`. `disk_full` sigue leyendo solo el directorio del intento, su checkout y
`/tmp`.

**Las particiones huérfanas.** Un producto que se borró de la máquina deja su partición.
`orphan_partitions` devuelve exactamente lo que el barrido de otro producto puede borrar de una
partición ajena: nunca una partición cuya carpeta, carpetas de tarea o entradas (los nombres
temporales ocultos incluidos) sean más jóvenes que `SCRATCH_ORPHAN_GRACE`, ni una que tenga un
registro de dueño vivo o ilegible. Borrar es en dos fases: se renombra a
`.<clave>.swept-<token>` y después se elimina, porque entre listar y borrar otro barrido o un
intento nuevo puede aparecer; un resto renombrado que quedó a medias vuelve a salir de la
misma regla pasada la gracia.

**La cota de 40 caracteres** de la parte bajo la raíz (12 de clave de producto, `/`, la clave
de tarea, `/` y `<tipo>-<8>`) sigue valiendo, pero ahora cuelga de la raíz: el largo de la raíz
es el presupuesto del operador para el límite de 108 bytes de un socket unix. La raíz por
defecto de esta máquina es más corta que el prefijo viejo.

**El árbol viejo.** `<producto>/.agentic/tmp/` queda excluido de git (`LEGACY_SCRATCH_DIR`) y
nada lo barre solo: lo borra una persona cuando ya no corre ningún stage0 anterior a este
cambio.

## Consecuencias y riesgos conocidos

- El `TMPDIR` ya no es hijo del producto: quien mire el disco del producto no ve el scratch de
  sus corridas, y limpiar un producto no limpia su partición hasta el siguiente barrido.
- Quien fija `AGENTIC_SCRATCH_DIR` dentro de un repositorio recibe la negativa antes de gastar.
- `/tmp` sigue contando para `disk_full`, igual que en el ADR-0069.
- La causa del vencimiento de 1500 s de GH-392 pudo no ser solo la falta del techo de git de
  `tests/conftest.py`: un vencimiento que persista con el techo presente es un hallazgo
  externo, no de este cambio.
- Un `stat` más por ronda del poller y por intento fallido, y un listado de las particiones de
  la raíz por ronda, lineal en productos y tareas de la máquina.
