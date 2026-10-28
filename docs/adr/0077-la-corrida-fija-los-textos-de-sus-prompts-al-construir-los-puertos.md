---
status: accepted
date: 2026-10-28
---

# La corrida fija los textos de sus prompts y de los estándares por defecto al construir los puertos

GH-409. Una corrida lee del disco de stage0 el prompt de cada rol cada vez que ese rol
arranca, pero su código Python se cargó al arrancar la corrida. Si stage0 se actualiza a un
master más nuevo mientras la corrida vive, el código viejo renderiza un prompt nuevo. GH-399
murió así en la revisión de Tech Lead (`no value for placeholder {{docs_review}}`) y quedó en
`developing` sin proceso, sin que el poller la viera fallida.

Extiende ADR-0005 (el overlay de prompts del producto) y ADR-0012 (los estándares por
defecto).

## Decisión

1. **Se fijan al construir los puertos.** `FilesystemPrompts.__init__` lee una sola vez el
   prompt de cada slug del conjunto de roles (`ROLES`), con el overlay del producto por
   encima del default del sistema, y `FilesystemKnowledge.__init__` lee una sola vez
   `DEFAULT_STANDARDS`. `raw()` y la lectura de estándares sirven esa copia. Solo se leen los
   slugs del conjunto: otros `.md` del overlay se ignoran, como hoy.
2. **Por qué ahí, y dónde vive la corrida.** `composition.default_ports` construye los
   puertos otra vez en cada nodo del grafo, porque `product_dir` puede llegar con el
   `configurable` de la corrida. Fijar los textos solo al construir el adaptador duraría,
   entonces, un rol. Por eso el adaptador lee al construirse y `_PortsPerRun` (el mismo
   objeto que ya guarda el `RunLimits` de la corrida) se queda con los `prompts` y el
   `knowledge` que armó por primera vez para cada par (`thread_id`, `product_dir`) y se los
   entrega a todo nodo posterior de esa corrida. Una llamada sin `thread_id` no tiene corrida
   a la que fijarse y no guarda nada. No es perezoso por rol (eso es lo que falla hoy) ni vive
   en preflight (la corrida no lo comparte con `langgraph dev`).
3. **Un error de lectura no rompe la construcción.** Se guarda por slug y `raw()` lo vuelve a
   lanzar tal cual (`OSError`/`UnicodeDecodeError`) para ese rol; igual para los estándares.
   El hash `prompt_hash(raw)` de cada texto no cambia.
4. **Qué no se fija.** El playbook de cada rol se sigue leyendo por rol: `playbooks_changed`
   ya prevé que cambie a mitad de corrida. El manifiesto del plugin y la plantilla del brief
   no dan forma a un prompt.
5. **Si un prompt igual no renderiza** (un placeholder sin valor), el intento escala como
   fallo de rol: `PromptFailure`, motivo `render`, clase de escalación de fallo de rol, sin
   reintento (el mismo texto fallaría igual), costo cero, contado como fallo propio, y el
   parche no lleva `prompts`. El subtask `render-escalation` lo construye; `load_prompt`
   sigue lanzando el `ValueError` de `render` a preflight y `cli.py`.

## Consecuencias

- **Costo.** Una lectura de unos 19 archivos chicos por nodo del grafo, que antes se hacía rol
  por rol. No crece con los datos.
- **Riesgos que quedan.** Los imports perezosos del código y las skills del plugin, cargadas
  de `PLUGIN_DIR`, siguen leyendo el stage0 vivo. `scripts/stage0.sh` puede mover stage0 bajo
  una corrida viva. Otra excepción cruda cualquiera todavía puede dejar una tarea en
  `developing` si el diagnóstico del camino de crash falla.
- **Opciones rechazadas.** Diferir la actualización de stage0 mientras haya corridas vivas:
  frena el merge de cualquier arreglo detrás de la corrida más larga, y una corrida colgada
  lo congelaría para siempre. Un worktree por sha: multiplica el disco y el tiempo de
  preparación por corrida para cubrir lo que fijar los textos de los prompts ya cubre.
