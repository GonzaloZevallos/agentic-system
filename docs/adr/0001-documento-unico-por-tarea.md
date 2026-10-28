---
status: accepted
date: 2026-09-12
---

# Documento único por tarea en vez de mensajes entre agentes

Los roles necesitan pasarse trabajo. Elegimos un solo archivo por tarea en el repo del
producto, con frontmatter YAML que mantiene PjM y una sección Markdown por rol, en vez de
mensajes directos entre agentes o comentarios en el issue de GitHub. El archivo le da a PjM
trazabilidad total sin esfuerzo, permite reintentos, cualquier rol relee sin pedirle nada
a nadie, se grepea por nombre de sección y entra en el checkpoint.

## Opciones consideradas

- **Mensajes directos entre agentes**: sin registro central, PjM no ve nada sin trabajo extra.
- **El issue de GitHub como documento**: parsear secciones de comentarios es frágil y cuesta
  una llamada de API por lectura. El issue queda como bandeja y estado visible, derivado del
  archivo.

## Consecuencias

La sincronización archivo → issue se puede desfasar. El archivo manda siempre.

**Adenda (2026-10-28):** la cláusula "el documento se commitea" la supersede parcialmente [ADR-0078](0078-el-documento-de-tarea-nunca-se-versiona-en-una-rama-de-entrega.md): el documento vive en el disco del producto y no se versiona en una rama de entrega.
