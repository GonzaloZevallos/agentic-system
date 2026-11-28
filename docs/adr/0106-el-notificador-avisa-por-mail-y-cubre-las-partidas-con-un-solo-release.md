---
status: accepted
date: 2026-11-23
---

# El notificador avisa por mail y cubre las partidas con un solo release

GH-1056, parte 2 (GH-1061). El notificador le debe a una persona un mail cuando una tarea espera
a un humano o aterriza. Este ADR registra lo que ya está construido en
`agentic/core/process/domain/notify.py`, que es puro e inerte (sin reloj, sin entrada ni salida),
y lo que decide la parte siguiente, que lo llama desde el poller. El envío y los registros no
están en este ADR: los trae esa parte.

## Eventos y claves

Un aviso es la deuda con una persona, nunca el mensaje. Hay tres clases:

- **waiting**: una pausa que espera a un humano. `waiting_event` lo construye, con clave
  `EventKey('waiting', producto, issue, instante)` donde el instante es el `at` de la última
  escalación. Es `superseded` si el issue está cerrado o la pausa ya se reanudó.
- **release**: un aterrizaje. `release_event` lo construye, con clave
  `EventKey('release', producto, issue, instante)`.
- **digest**: el resumen diario de un producto, con clave `DigestKey(producto, fecha local)`.
  Por ahora solo existen la clave y la hora (`DEFAULT_DIGEST_HOUR`); el cálculo es de un issue de
  seguimiento.

La clave rechaza al construirse un instante sin zona UTC, un issue que no es entero positivo, una
clase desconocida y un producto que no es slug, y nunca normaliza. Cada clave resuelta queda en
el registro con su `Outcome` (`sent`, `superseded`, `covered`, `unbuildable`).

## Cobertura de partidas

Un split reparte una tarea en partes, y una parte puede partirse otra vez (hasta dos niveles). Lo
que se anuncia es el aterrizaje de la raíz, no el de cada parte. `release_event` lee un
`SplitFacts` (padres, `split_closed_at` y partes como `PartFact`) que el llamador carga; el
dominio no lee nada.

- **Una tarea con un padre conocido queda `covered`, siempre**, aun una parte intermedia que ya
  está cerrada. El instante de su clave es su propio cierre: `split_closed_at` si tiene partes
  propias, `auto_merged_at` si no. Sin instante no emite nada.
- **Solo una raíz con `split_closed_at` emite `send`**, listando sus hojas fusionadas aplanadas
  (las partes que ninguna otra nombra en `parent_of`), ordenadas por issue y cada una con su padre
  intermedio. La clave usa `split_closed_at`. La raíz no filtra por los flags de sus hojas.
- **Una tarea sin padre ni partes emite `send`** cuando se fusionó sola (`auto_merged_at`) y sus
  flags dicen `touches_ui` o `touches_copy`, leídos solo por `rules.touches_person`. Ese lector no
  filtra por tipo, a diferencia de `design_applies` y `copy_applies`, que deciden el camino
  (ADR-0040, ADR-0058).
- **`split=None` significa que los hechos no se pudieron leer.** Nunca se lee como «sin padre»:
  no emite ni marca nada y la ronda siguiente vuelve a intentar.
- `SplitFacts` rechaza al construirse un árbol incoherente: una parte que se nombra a sí misma, un
  ciclo, más de un `parent_of` externo distinto, más de dos padres, más de dos niveles, o padres y
  partes vacíos con `split_closed_at` puesto. `PartFact` rechaza enteros no positivos, un `merged`
  que no es booleano y un `pr` que no es `None` ni entero positivo.

## Política de envío

- **Horizonte de 7 días** (`NOTIFY_HORIZON`): un evento vence solo si su instante es posterior a la
  activación y al corte; el historial viejo nunca manda mails. Las claves más viejas se podan.
- **Descanso de reintento de 15 minutos** (`NOTIFY_RETRY_AFTER`) tras una ronda fallida, y **5
  envíos por ronda** (`SENDS_PER_ROUND`).
- **Apagado por defecto.** Sin activación (`started_at`) no vence nada.
- `pending` es la única composición de «vence» y «su clave no está registrada»; ni
  `waiting_event` ni `release_event` llaman a `due`.

## Sin secretos, mensajes saneados

Ningún secreto va a los logs, a los registros ni al mail: el registro no tiene ningún campo de
texto libre y las claves y los hechos son enteros e instantes validados. Todo texto que llegue a
un mensaje se sanea antes de armarlo: una sola línea, sin caracteres de control y con largo
acotado. Ese saneo lo construye la parte que arma el mensaje, no este módulo. Un evento cuyo
mensaje no se puede armar queda `unbuildable`, nunca se manda a medias.

## Límites conocidos

- Un merge hecho a mano no escribe `auto_merged_at` y no manda release.
- Un split abandonado nunca anuncia sus partes: la raíz no tiene `split_closed_at` y cada parte
  queda `covered`.
- Un SMTP caído más de 7 días pierde los eventos: salen del horizonte.
- Un archivo de registro corrupto pierde hasta 7 días de eventos pendientes.
- Una retención de formato agotada no manda mail: es una época externa, no una pausa humana.
- Un padre que se cierra por el `Closes` de su única hija terminal no tiene `split_closed_at` y no
  anuncia.
- Una raíz cerrada sin ninguna hoja fusionada no anuncia.

## Adenda (GH-1057): el adaptador SMTP y su cableado

- **TLS.** El puerto 465 es TLS implícito (`SMTP_SSL`). En cualquier otro puerto el servidor tiene
  que ofrecer STARTTLS: se hace `ehlo`, se verifica `has_extn("starttls")` y se pasa a TLS con
  `ssl.create_default_context()`, que verifica el certificado y el nombre. Sin STARTTLS el envío
  falla con `NotifyFailed` antes de cualquier `login`: nunca se autentica sin TLS. Toda conexión
  lleva un timeout explícito de 10 s. Sin usuario no hay `login`.
- **Una sola lectura de la configuración.** `adapters/smtp/config.py` es el único módulo que nombra
  las variables `AGENTIC_NOTIFY_*`. `settings(environ)` recibe el mapa, nunca lee `os.environ`, y
  devuelve los valores crudos: un puerto o una hora mal escritos no lanzan al componer, fallan al
  enviar como `NotifyFailed`. La composición obtiene todo de ahí y nadie más importa `smtplib`.
- **Bordes.** Un `EMAIL_TO` vacío o con solo espacios significa apagado: `notifier`, `notified` y
  `notify_settings` quedan en `None`. Un `EVENTS` vacío o sin ningún tipo tras partir por comas
  significa todos los tipos: un error de tipeo nunca apaga el aviso en silencio. Un destinatario
  con coma se rechaza, es uno solo. El From es el usuario si tiene `@` y, si no, el destinatario.
- **Fallos.** Todo error sale como `NotifyFailed` con el nombre del tipo de la excepción original y
  nada más: ni su texto, ni la respuesta del servidor, ni la contraseña, el usuario o cualquier
  valor configurado, tampoco en `__cause__` ni en `__context__`.
- **Canal hacia el caso de uso.** `Ports.notify_settings` lleva la hora del resumen y los tipos de
  evento, crudos, a la parte que los interpreta, sin que importe el adaptador ni lea el entorno.
  Es `None` junto con el notificador, en el dry-run y en la evaluación.
