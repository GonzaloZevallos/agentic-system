# Decisiones del dueño

Las reglas generales que el dueño dio al contestar un escalado o por mensaje, sobre cómo
trabaja el sistema con cualquier producto. Todo rol las lee enteras. Una regla vigente decide
la pregunta que cubre sin escalar, y quien la aplica cita su id. Contra un ADR que toca, gana
la regla fechada después del ADR y la tarea deja un hallazgo `outside` para enmendarlo; la
fechada antes pierde.

Cada entrada lleva un id `D-` y 8 hexadecimales del sha256 de su texto normalizado, la fecha,
la regla, de dónde salió y, si corresponde, qué reemplaza. Solo se agrega al final; el texto
de una entrada no se edita: corregir es reemplazar, y la regla reemplazada se tacha en el
mismo commit que agrega la nueva. Las reglas de un producto están en el archivo de su propio
`docs/product/`.

### D-d3cd64da · 2026-10-28
Una tarea de más de 150 USD o de unos 40 archivos se parte.
Origen: issue #431, semilla con las reglas dadas entre 2026-10-02 y 2026-10-28
Rozan: ADR-0054

### D-99072874 · 2026-10-28
Una parte se construye entera, salvo que pase de 300 USD.
Origen: issue #431, semilla con las reglas dadas entre 2026-10-02 y 2026-10-28
Rozan: ADR-0054

### D-5c088d42 · 2026-10-28
Una corrida que llega a su tope de estimación recibe la nueva estimación aprobada, bajo la misma línea de 300 USD.
Origen: issue #431, semilla con las reglas dadas entre 2026-10-02 y 2026-10-28
Rozan: ADR-0054

### D-03258899 · 2026-10-28
Reiniciar una tarea es el último recurso. Un rechazo del libro de cuentas o de la contabilidad se resuelve o se escala, nunca se reinicia.
Origen: issue #431, semilla con las reglas dadas entre 2026-10-02 y 2026-10-28
Rozan: ADR-0048

### D-be76583a · 2026-10-28
Una colisión de número de ADR se renumera.
Origen: issue #431, semilla con las reglas dadas entre 2026-10-02 y 2026-10-28
Rozan: ADR-0049

### D-073ea3ad · 2026-10-28
El orquestador mergea cuando el Tech Lead aprobó y el CI declarado está verde; no espera al dueño.
Origen: issue #431, semilla con las reglas dadas entre 2026-10-02 y 2026-10-28
Rozan: ADR-0045

### D-da435190 · 2026-10-28
Los pull requests de lecciones del playbook los mergea el dueño, no el orquestador; antes los cura, con cada lección de 240 caracteres o menos.
Origen: issue #431, semilla con las reglas dadas entre 2026-10-02 y 2026-10-28

### D-2ed419a0 · 2026-10-28
El CI de GitHub decide la suite completa.
Origen: issue #431, semilla con las reglas dadas entre 2026-10-02 y 2026-10-28
Rozan: ADR-0064

### D-c0aa720e · 2026-10-28
Las corridas locales de e2e siguen apagadas hasta que el dueño diga lo contrario.
Origen: issue #431, semilla con las reglas dadas entre 2026-10-02 y 2026-10-28

### D-19118a23 · 2026-10-28
Un check rojo en el CI del pull request es un hallazgo de aterrizaje a arreglar, no una pregunta para el humano.
Origen: issue #431, semilla con las reglas dadas entre 2026-10-02 y 2026-10-28
Rozan: ADR-0045

### D-7e11ef60 · 2026-10-28
La evidencia que solo puede producir una corrida de GitHub (línea base de cobertura, tiempo de pared, e2e en shards) sale de la primera corrida verde del pull request.
Origen: issue #431, semilla con las reglas dadas entre 2026-10-02 y 2026-10-28
Rozan: ADR-0064

### D-d506fe31 · 2026-10-28
La entrega abre el pull request. Su descripción nunca es un criterio de subtarea.
Origen: issue #431, semilla con las reglas dadas entre 2026-10-02 y 2026-10-28

### D-1408e7d0 · 2026-10-28
Una tarea de documentación documenta lo que el código hace y no cambia comportamiento.
Origen: issue #431, semilla con las reglas dadas entre 2026-10-02 y 2026-10-28
Rozan: ADR-0074

### D-b111a2ac · 2026-10-28
La documentación va en español, tanto la funcional como la técnica.
Origen: issue #431, semilla con las reglas dadas entre 2026-10-02 y 2026-10-28

### D-f3aa438e · 2026-10-28
El código, los identificadores, los nombres de clase y los nombres de UML van en inglés.
Origen: issue #431, semilla con las reglas dadas entre 2026-10-02 y 2026-10-28

### D-03fa4682 · 2026-10-28
Los datos guardados quedan como el usuario los cargó y nunca se traducen.
Origen: issue #431, semilla con las reglas dadas entre 2026-10-02 y 2026-10-28

### D-181cf43e · 2026-10-28
Los diagramas mantienen un presupuesto de tamaño y los grandes se parten por significado (agentic-system#430).
Origen: issue #431, semilla con las reglas dadas entre 2026-10-02 y 2026-10-28

### D-88fd1e7e · 2026-10-28
El sistema nunca toca datos de producción ni los secretos del entorno de producción.
Origen: issue #431, semilla con las reglas dadas entre 2026-10-02 y 2026-10-28
