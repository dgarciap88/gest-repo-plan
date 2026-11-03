# TASK-001 · Comando `gestplan plans` para listar los planes disponibles

## Contexto
Hoy hay que conocer el nombre o la ruta de un plan para lanzarlo. Un comando que liste los planes de `plans/` y `plans/examples/` facilita descubrirlos, tanto a personas como a la IA que diseña planes.

## Qué hacer
- Añadir el subcomando `gestplan plans` en `src/gestplan/cli.py` que liste, uno por línea, cada plan encontrado con: nombre, número de nodos, inputs obligatorios y descripción.
- Ordenar por nombre. Si un fichero no es un plan válido, mostrarlo con una marca de error y seguir.
- Respetar `--root` como el resto de comandos.

## Criterios de aceptación
- [ ] `gestplan plans` muestra `implement-task` e `implement-tasks` del propio repo.
- [ ] Un JSON inválido en `plans/` no rompe el comando (se marca como no válido).
- [ ] Hay tests en `tests/` y `pytest` pasa.

## Fuera de alcance
No cambiar el formato de los planes ni el motor.
