---
status: accepted
date: 2026-10-26
---

# Un chequeo rojo se reintenta una vez dentro de la puerta; si pasa al reintento es un flake

GH-360. Un chequeo rojo rechazaba la entrega y el Developer solía volver a entregar el mismo
código, con lo que la puerta corría todo otra vez. En apostrophe, 164 rechazos fueron
seguidos de una re-entrega sin commit de por medio (63,2 h de puerta); en 112 la re-entrega
idéntica pasó (43,8 h). Medido el 2026-10-26 sobre las tareas iniciadas desde el 2026-10-12.

## Decisión

Un chequeo rojo se reintenta una vez dentro de la puerta, antes de que ésta decida.
`domain/presubmit.flake_verdict(primera, reintento)` es la única función que decide, y
devuelve `pass` (verde a la primera, sin reintento), `flake` (rojo y luego verde) o `fail`
(rojo dos veces). Ninguna puerta decide esto por su cuenta.

- **Cuándo se reintenta.** Todo chequeo rojo, salvo que haya agotado su tiempo: una fila
  `timed_out` no deja señal de qué pruebas fallaron y reintentarla duplicaría una espera de
  25 minutos. A lo sumo un reintento por chequeo y por llamada de la puerta; una fila de
  reintento nunca se reintenta. Sólo reintenta la última fila del chequeo (la forma completa
  tras una afectada fallida, o la fila sin plan), nunca una fila de forma afectada.
- **Qué comando.** La puerta de entrega reintenta con la forma que el producto declara
  (`rerun_checks.<chequeo>.command`) sólo si la primera fila es un fallo sin tiempo agotado y
  `failed_ids` leyó al menos un id; si no, con el comando completo del chequeo. La puerta de
  merge (`merge.run_checks` y el corte de aterrizaje) y `full_checks` reintentan siempre el
  comando completo, nunca la forma: sólo una corrida completa cubre la suite.
- **La clave del stack.** `rerun_checks: {<chequeo>: {command, test_id}}`, opcional, leída
  en la base de la tarea (el Developer de la tarea no puede cambiar el patrón que lo juzga).
  El parser (`knowledge/domain/stack.py`) y el preflight rechazan: un nombre que no es un
  chequeo declarado; `command` sin `test_id` o al revés; un `test_id` que no compila, que no
  tiene exactamente un grupo, de más de 200 caracteres, con un grupo cuantificado que contiene
  un cuantificador, o con una alternancia dentro de un grupo cuantificado (estructural, sobre
  el árbol de `re._parser`); y cualquier entrada en un producto sin `delivery_checks`, porque
  el chequeo completo de la tarea (ADR-0064) garantiza una corrida completa sobre la cabeza
  integrada antes del pull request, la mergee quien la mergee y haya CI o no. La forma no debe
  parar antes de tiempo (`-x`, `--maxfail`, `maxFailures`); el producto elige qué hace una caché
  vacía (`--lfnf=none`).
- **Las filas.** Una fila de reintento lleva `rerun: true`, va justo tras la primera en
  `results` y tiene su propia duración. `final_results` sigue decidiendo por la última fila
  por nombre, así que un flake pasa. `_store_passes` salta toda fila con `rerun`: el primer
  intento falló, así que de ese chequeo no se guarda nada (ADR-0065 queda intacto). Una fila
  con tiempo agotado registra `timed_out: true`, para que la regla lea un hecho y no prosa.
- **El registro.** `TaskState.flakes: Annotated[list[FlakeRecord], add_flakes]`, con
  `FlakeRecord = {check, head, gate: delivery|merge|full_checks, tests}`. `add_flakes` es
  idempotente y conmutativo: clave `(check, head, gate)`, `tests` como conjunto ordenado y
  acotado a 50, salida ordenada por clave. `flakes` se escribe sólo por ese reductor (un
  parche para los nodos del grafo, `add_flakes(existente, nuevo)` para `record_closure`).
  Registrar, archivar o comentar un flake nunca cambia, demora ni rechaza una entrega o un
  merge; los fallos del tracker se registran y se saltan.
- **`failed_ids(salida, patrón)`** aplica el patrón sólo a los últimos 64 KB de la salida y
  devuelve a lo sumo 50 ids. El resumen de pytest y la lista de Playwright están al final
  (unos 100-300 bytes por fallo); un resumen cortado da menos ids o ninguno, y ninguno es un
  reintento completo: el lado seguro.
- **Observabilidad.** La señal `agentic.presubmit.checks` gana la etiqueta `outcome`
  (`pass|flake|fail`). Ningún id de prueba, regex ni salida va a una etiqueta. Las filas de
  reintento quedan fuera de la muestra de duraciones, como las `reused`.

## Seguridad

Una regex declarada por el producto corre ahora dentro del proceso del sistema, sobre la
salida del chequeo del propio producto: no alcanza ningún archivo, comando ni red. Se lee en
la base de la tarea. Su costo está acotado al parsear (las formas que retroceden de modo
catastrófico se rechazan) y por el corte a 64 KB en la única función que la ejecuta.

## Ahorro esperado

Por chequeo: `flakes × (viaje del Developer + tiempo del chequeo fallido − tiempo del
reintento) − fallos_reales × tiempo del reintento`. Las 43,8 h (112 flakes) y 25,0 h (78
fallos reales) del issue se midieron antes del almacén de ADR-0065, que ya quita el reintento
de los chequeos que pasaron: 43,8 h es un techo, no el ahorro. Con una forma (`tests`) el
neto es positivo, pues un fallo real reintenta sólo sus pruebas; sin forma (`web-e2e`) cada
fallo real paga un reintento completo y el neto puede acercarse a cero. Los tiempos agotados
cuestan como hoy.

## Desviaciones respecto del issue

1. La puerta de merge y `full_checks` reintentan el comando completo, nunca la forma.
2. Un chequeo con tiempo agotado nunca se reintenta.
3. Una forma sólo se acepta en un producto que también declara `delivery_checks`.

## Consecuencias

- Una prueba que falla sólo si otra contamina estado compartido pasa bajo `--lf` y sale como
  flake; el sistema no distingue ambos casos.
- Los registros de `merge_checks` anteriores a este cambio no llevan marca de reintento y
  siguen el camino de hoy.
