# Validación

Revisa los cambios del workspace. Se han generado aplicando la skill `{{skill}}` con el agente `{{agent}}` para el
ticket `{{jira_id}}`.

- Comprueba que los cambios son coherentes con lo que esa skill pretende y que no hay cambios ajenos.
- Busca errores reales: fallos de lógica, tests o código roto, secretos o datos sensibles.
- Señala solo incidencias reales, con severidad (`high`, `medium`, `low`), fichero y línea.
- No modifiques ningún fichero.
