---
status: accepted
date: 2026-10-26
---

# Un resultado que pasó se reutiliza por contenido, no por historia

GH-359. El mismo código se chequea una y otra vez: el Developer corre la suite a mano
antes de entregar y la puerta la repite sobre el mismo commit; la puerta corre todo cada
vez que la cabeza se mueve, también tras un rechazo que no fue de un chequeo; la puerta de
merge y el corte de aterrizaje vuelven a correr sobre árboles ya verificados. Todos pasan
por `CheckoutPresubmit._run`: ahí se decide una sola vez.

## Decisión

- **La clave.** `domain/check_results.check_key(producto, nombre, comando exacto, árbol,
  huella)`: un digest hexadecimal sobre JSON, así que un campo con un separador no choca con
  el vecino. El árbol es `HEAD^{tree}` de un checkout limpio (`Workspace.tree`), no el
  commit: un merge cuyo árbol es igual a uno ya chequeado reutiliza su resultado. Una forma
  afectada lleva su propio comando, base incluida, así que nunca reemplaza a la completa.
- **La huella del entorno.** Los comandos de setup en el orden declarado, el sha completo de
  stage0 y `check_timeout_s`. Un stage0 `+dirty` no tiene huella: ni lee ni escribe, y el
  camino es el de hoy, byte a byte.
- **Deriva del entorno.** Tras pasar el setup, el sistema anota el árbol sobre el que corrió
  en el directorio git del worktree (`git rev-parse --git-path agentic-setup-tree`); nunca
  en los archivos del checkout. Una escritura exige esa anotación, que debe resolver como
  `tree` (`git cat-file -t`), y que `env_drifted` sea falso: ningún archivo de la lista
  cerrada (manifiestos, locks y archivos de versión de herramienta, a cualquier
  profundidad) ni ruta que un comando de setup nombre literalmente cambió entre el árbol del
  setup y el actual. La deriva no bloquea la lectura: un pase cuenta para la llamada en que
  corrió, sólo que no se guarda.
- **Sólo pases.** Nunca se guarda un fallo, un timeout, un árbol sucio, una corrida sin
  huella ni una corrida bajo deriva. Un fallo puede ser flaky y no se reutiliza como final.
- **Dónde vive.** Un almacén por máquina, `Configuration.check_results_dir`
  (`$XDG_STATE_HOME/agentic/check-results`), fuera de todo checkout; el producto es parte de
  la clave. Un `<clave>.json` por entrada, escrito de forma atómica (archivo temporal más
  `os.replace`). Elegido sobre el documento de tarea: el documento sólo vive dentro de una
  tarea y no sirve a la puerta de merge de otra rama ni al aterrizaje, y la clave por
  contenido hace innecesario atar el resultado a una historia.
- **Frescura.** `MAX_AGE_S = 86400`, comprobado en la lectura con un `now` que se pasa como
  argumento (ningún módulo de dominio lee el reloj). Hasta 5000 archivos: al escribir se
  podan primero los vencidos y luego los más viejos. El costo es lineal en el listado del
  directorio y sólo en escrituras.
- **Confianza.** Cada entrada lleva un HMAC-SHA256 sobre clave, instante y fila con un
  secreto de máquina de 32 bytes, `.key`, modo `0600`, creado por la primera escritura. Una
  entrada ausente, ilegible, sin verificar o vencida es un fallo de caché, nunca una
  excepción. El MAC nombra el problema del estado compartido entre escritores que no son la
  puerta: frena la edición accidental o a mano. **Residual:** todo corre bajo un mismo uid y
  sin sandbox de archivos; un proceso que lea el secreto por un desvío puede firmar una
  entrada. Esto detiene accidentes y el camino obvio, no a un adversario con el mismo uid.
- **Supuesto de determinismo.** Reutilizar por contenido supone que el chequeo es
  determinista sobre ese árbol y ese entorno. Un chequeo flaky que pasó una vez sigue verde
  hasta 24 h sobre el mismo árbol; es el costo aceptado.
- **`MCP_TOOL_TIMEOUT`.** _(Lo completa la subtarea `run-check-tool`: el supuesto sobre el
  tiempo máximo de la herramienta `run_check`, su única medición manual y la versión del
  CLI.)_
- **Rechazado.** Guardar el resultado sólo en el documento de tarea (no cruza ramas ni
  pasos de merge); un `cache_inputs` que cada producto declare (pide que cada producto
  enumere todo lo que un chequeo lee, y un olvido es un falso verde); reutilizar fallos
  (pueden ser flaky).

## Reemplaza

Esta decisión reemplaza el punto «Rechazado por ahora» de ADR-0064 sobre que la puerta de
merge reutilice un registro `full_checks` verde: la clave por contenido resuelve la
frescura. Un árbol de merge igual a uno chequeado reutiliza; cualquier otro corre todo.

## Consecuencias

Quedan fuera de esta subtarea la lectura y escritura en `CheckoutPresubmit`, el marcado
`reused` en los registros y la herramienta `run_check`: esta ADR fija el almacén y la clave
que ellas leen.
