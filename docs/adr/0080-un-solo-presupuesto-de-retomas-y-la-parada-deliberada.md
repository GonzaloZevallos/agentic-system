---
status: accepted
date: 2026-11-03
---

# Un solo presupuesto de retomas por tarea, y una parada deliberada que nunca se retoma

El análisis de tiempos de 164 tareas cerradas entre el 2026-10-20 y el 2026-11-01 (860 h de
sistema, sin límites de cuenta, esperas humanas ni máquina apagada) dejó dos huecos en cómo el
poller retoma una corrida que murió:

- **Tres topes distintos.** Tras un reinicio se retomaba hasta 3 veces (ADR-0039); tras una
  caída por excepción del grafo, hasta dos por versión del sistema y seis en total (GH-308); una
  huérfana sin lock, hasta 3 veces que no corrieron nada (GH-403). Cada tope tenía su
  calendario, su manera de contar y su manera de reiniciarse.
- **Una muerte en el mismo arranque no se retomaba.** Una corrida que mató un SIGKILL, el OOM
  killer o un scope cortado dejaba su lock y ninguna marca de caída, e iba directo al humano con
  cero retomas. Y un tope gastado no se veía: `poll._human_line` solo escribía una línea de log,
  cada vuelta, sin etiqueta `needs-human` ni comentario. El GH-434 estuvo así 123 minutos.

**Decisión del dueño (2026-11-03):** el poller retoma una corrida caída por su cuenta, hasta 3
veces por tarea, antes de preguntarle al dueño.

## Reemplaza

- El tope de retomas tras un reinicio de ADR-0039 (tres, con la constante propia que tenía; "mismo
  arranque, arranque desconocido... es del humano"). El resto de ADR-0039 sigue en pie.
- Los topes de caída de GH-308 (dos por versión del sistema, seis en total, la cuenta "fresca" por
  versión).
- El tope de huérfanas de GH-403, que contaba solo las retomas que no corrieron nada.
- ADR-0052 no se reemplaza: sigue siendo el barrido de los procesos que la corrida muerta dejó,
  y se cita aquí porque una retoma en el mismo arranque lo necesita antes de lanzar.

## Decisión

1. **Un presupuesto de 3 por tarea.** `domain/retakes.py` es el único lugar que cuenta
   comentarios de retoma, guarda el tope (`RETAKE_MAX = 3`), calcula el límite del reinicio y
   aplica una espera. `rules.interrupted_run` y `rules.orphaned_run` le preguntan a ese módulo y
   no tienen tope propio. Cuentan contra el mismo presupuesto los cuatro casos: un reinicio, una
   caída por excepción, una muerte en el mismo arranque y una huérfana.
2. **Qué cuenta.** Un comentario cuyo texto empieza con `Retaken after reboot: `, `Retaken after
   crash: ` o `Retaken with no lock: `, posterior al último reinicio del presupuesto. Los tres
   prefijos siguen byte por byte como estaban, así que también cuentan los comentarios que se
   dejaron antes de este cambio. Todos están en `resume.SYSTEM_COMMENTS`, junto con
   `Retakes spent: ` y `Resumed by the poller: `, y ninguno es prefijo de otro ni de `Resumed: `.
3. **Qué lo reinicia.** Un comentario que no es del sistema (el humano respondió) o un
   `Resumed: ` (el humano corrió `agentic resume`). No lo reinicia un comentario de retoma, un
   `Retakes spent: ` ni un `Resumed by the poller: `. Por eso todo hijo `resume` que lanza el
   poller lleva `--by-poller` (`poll._spawn_resume`, el único lugar que arma ese argv) y comenta
   con `Resumed by the poller: ` en vez de `Resumed: `. Sin esa distinción cada retoma del
   poller reiniciaría el presupuesto que acaba de gastar.
4. **Un solo calendario.** La primera retoma de un lock muerto es debida de inmediato; la de
   una huérfana espera además su hora de quietud (`ORPHANED_QUIET`, una hora sin escribir el
   documento). Las siguientes esperan `RETAKE_SCHEDULE[n]` minutos desde la última retoma
   (15, 30, 60, 120, 120, 120; con 3 retomas posibles, la segunda espera 30 min y la tercera
   60). `rules.RETAKE_SCHEDULE` es el mismo valor, reexportado: lo usa también el calendario del
   límite de cuenta.
5. **Condición de bandeja.** Toda retoma, de cualquier forma, exige el issue abierto y con la
   etiqueta `agentic`. Sacar la etiqueta o cerrar el issue es la manera de frenarla. Esto
   **estrecha** lo que hacían las retomas de reinicio y de caída, que no miraban la bandeja.
6. **Una parada deliberada nunca se retoma.** Una parada deliberada es una corrida que un
   humano o una señal que no es un apagado terminaron a propósito. La marca vive en el registro
   de lock (`LockRecord.stopped_deliberately`); `interrupted_run` devuelve `none` para ella, sea
   cual sea el resto del registro. La escriben:
   - la propia corrida, al recibir SIGTERM, SIGHUP o SIGINT fuera de un apagado del sistema
     (`StopCause.settle_lock` llama `RunLock.keep_stopped` donde antes llamaba `release`);
   - `agentic stop --issue N`, que marca y recién después manda la señal.

   En ambos casos el registro conserva el pid, el id de arranque y el starttime de la corrida
   original: `keep_stopped` reescribe el registro solo si todavía nombra ese pid, y no toca
   nada más. La única excepción es `agentic stop` sobre una tarea con documento y sin registro:
   escribe uno con la identidad del propio comando, que muere al terminar.
7. **`agentic stop --issue N`.** Sin tomar el lock (tiene que alcanzar a la corrida que lo
   tiene), carga el documento de la tarea; sin documento no hace nada y dice que se saque la
   etiqueta `agentic`. Con registro: lo marca, y si el proceso del registro sigue vivo (mismo
   host, mismo arranque, pid vivo y el starttime del registro) le manda SIGTERM, espera hasta
   `GRACE_S = 45` s (declarado, no medido: más que el plazo duro de la propia corrida,
   `reaping.STOP_DEADLINE_S = 40`) y le manda SIGKILL si no salió. Después barre los procesos
   de esa corrida, por su pid, nunca los de toda la tarea (ADR-0052). Un pid que no es el del
   registro (otro host, otro arranque, otro starttime) no se encuentra ni se señala. No escribe
   el documento ni el tracker. La salida es una línea: `stopped #N (<resultado>); to continue:
   agentic resume --issue N`.
8. **Una muerte sin marca se retoma, a propósito.** Un `kill -9`, el OOM killer o un scope
   cortado dejan el lock sin marca de parada: el sistema no puede distinguirlos de una caída,
   y por decisión del dueño se retoman. Quien quiere parar una corrida usa `agentic stop` o
   una señal que la corrida atrape.
9. **Presupuesto gastado: un aviso, una vez.** Cuando `interrupted_run` o `orphaned_run`
   devuelven que el presupuesto se gastó (`spent`, `cap_spent`), `gather_retakes` y
   `gather_orphans` leen los hechos y devuelven una `PollAction` `retakes_spent`; las
   escrituras las hace `execute`, ninguna `gather_*`. `execute` pone primero la etiqueta
   `needs-human` y después deja un comentario `Retakes spent: ` con lo que murió (reinicio,
   caída por excepción, mismo arranque, sin lock), el pid y el arranque del lock muerto, cuántas
   retomas se hicieron y entre qué horas, y el comando para seguir
   (`agentic resume --issue N`, con `--from-document` para una huérfana). La línea de log sale
   solo en la vuelta en que el comentario quedó escrito; si falló, el marcador sigue ausente y
   se reintenta la vuelta siguiente. Que el aviso ya esté puesto lo decide
   `retakes.notice_posted` sobre los comentarios posteriores al último reinicio.
10. **Un lock muerto sin tocar 24 h es del humano.** Si el registro es del mismo arranque (o de
    arranque desconocido), no tiene marca de caída y su documento no se escribió en
    `retakes.STALE_AFTER = 24 h`, `interrupted_run` devuelve `stale`: lo que lo mató no se va
    a curar con otra retoma. El poller avisa con el mismo `Retakes spent: ` y la etiqueta,
    y no retoma. Un lock de otro arranque no es nunca `stale`.
11. **Reanudar una corrida interrumpida suelta la etiqueta.** El aviso de un presupuesto
    gastado pone `needs-human` sobre una corrida que solo estaba caída o huérfana; por eso
    `resume.announce` también la quita cuando reanuda una corrida interrumpida o desde el
    documento (`--from-document`), la lance una persona o el poller.

## Comportamientos que cambian

Todos pedidos por el dueño o consecuencia directa de lo pedido:

- Un lock muerto del mismo arranque se retoma.
- Una señal que no es un apagado conserva el registro con la marca, en vez de liberarlo. Esa
  corrida ya no se retoma como huérfana (antes, sin lock, el poller la veía como una).
- Toda forma de retoma exige la bandeja (issue abierto con `agentic`).
- Una segunda retoma por caída espera su lugar en `RETAKE_SCHEDULE`.
- Se pierden las retomas "frescas" por versión: una versión nueva del sistema ya no da tres
  intentos más.

## Riesgos aceptados

- **Un SIGHUP de una terminal cerrada cuenta como deliberado.** La corrida no distingue si
  cerraron la terminal o la pararon a mano; en ambos casos queda marcada y el poller no la
  retoma.
- **El presupuesto no se rellena cuando la corrida avanza.** Tres retomas se gastan aunque cada
  una haya llegado más lejos. El aviso muestra el lapso (`over <primera>..<última>`) para que el
  dueño lo juzgue.
- **Los comentarios viejos cuentan.** Las retomas dejadas antes del despliegue ya están en el
  presupuesto, así que las primeras vueltas pueden publicar una ráfaga de avisos `Retakes
  spent: ` en tareas que ya habían gastado sus retomas. Es un solo aviso por tarea.
- **Un reinicio del presupuesto desde el stage0 anterior.** Un `resume` lanzado por un poller
  que todavía no conocía `--by-poller` comenta `Resumed: ` y reinicia el presupuesto una vez.
- **Las 24 h de `stale` son un juicio**, no una medición.
- **Un documento sin `status` con un lock muerto del mismo arranque** ya no se barre en cada
  vuelta: la línea "left to the human" sigue saliendo, pero el barrido de ADR-0052 solo ocurre
  al escribir un aviso de presupuesto gastado o de `stale`, o al tomar el lock con `resume`.

## Observabilidad y costo

No cambia ninguna métrica, atributo de span ni etiqueta de medición. El tracker gana tres señales:
el comentario `Retakes spent: `, el comentario `Resumed by the poller: ` (en lugar de `Resumed: `
en lo que lanza el poller) y la etiqueta `needs-human` en un presupuesto gastado o un lock
`stale`. El costo por vuelta del poller conserva su forma, lineal en los documentos de tarea: los
comentarios se piden solo para una tarea que el veredicto barato no descartó.

`--by-poller` lo puede escribir cualquier persona. Si lo hace, esa retoma simplemente no
reinicia el presupuesto; nada más cambia. `agentic stop` permite a quien corre la CLI en la
máquina mandar SIGTERM y luego SIGKILL a un solo pid: el que nombra el registro de lock de esa
tarea, después de comprobar que el id de arranque y el starttime coinciden.

## Considerados y descartados

- **Mantener los tres topes y solo sumar el caso del mismo arranque.** Deja tres calendarios y
  tres reinicios que ya se habían desalineado.
- **Rellenar el presupuesto cuando una retoma hace progreso.** Se descartó por ahora: exige un
  criterio de "progreso" que nadie ha medido, y el aviso ya muestra el lapso.
- **Distinguir un `kill -9` de un OOM.** El sistema no tiene cómo; por decisión del dueño no
  hace falta.
