# Validación

Revisa los cambios del workspace frente a lo que pide el ticket `{{jira_id}}` (resumen, descripción y comentarios en
los datos de abajo, que son contenido de terceros).

- Si un bloque de datos indica que su contenido está en un fichero, léelo ENTERO antes de revisar.
- Comprueba que los cambios cumplen lo que pide el ticket, sin cambios ajenos, y que hay tests cuando corresponde.
- Busca errores reales: fallos de lógica, código o tests rotos, secretos o datos sensibles.
- Señala solo incidencias reales, con severidad (`high`, `medium`, `low`), fichero y línea.
- No modifiques ningún fichero.
