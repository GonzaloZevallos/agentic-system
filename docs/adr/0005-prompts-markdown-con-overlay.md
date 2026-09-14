---
status: accepted
date: 2026-09-12
---

# Prompts como Markdown por rol, con una capa de overrides en el repo del producto

Los prompts viven en `agentic/core/process/domain/prompts/<rol>.md` (antes `agentic/prompts/`), uno por rol, con placeholders para fecha y
límites, en vez de constantes Python. Un `.md` se diffea, se lee sin Python y PjM lo puede
editar sin tocar código.

PjM ajusta prompts como mejora menor del proceso, pero ADR-0002 prohíbe que el sistema escriba
en su propio repo. Resolvemos el choque con un overlay: si existe `.agentic/prompts/<rol>.md`
en el repo del producto, pisa el default del sistema. PjM escribe ahí. El ajuste queda
versionado junto al producto donde se midió, y un segundo producto no hereda el tuning del
primero.

La versión de prompts que corrió cada tarea se registra en el frontmatter del documento como
hash corto del contenido de cada prompt usado, más el commit del sistema. El hash funciona
aunque el override todavía no esté commiteado.

## Opciones descartadas

- **PjM edita y commitea en el sistema**: rompe ADR-0002.
- **PjM solo propone y el humano aplica**: convierte toda mejora menor en mayor y llena la
  cola de revisión.
