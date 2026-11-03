# Tareas

Cada tarea es un fichero `TASK-NNN.md` que describe qué hay que hacer y cómo se comprueba. El plan `implement-task` recibe el id (`task_id`) y el agente lee `tasks/<task_id>.md` dentro del workspace.

## Formato
```markdown
# TASK-NNN · Título breve

## Contexto
Por qué hace falta.

## Qué hacer
Cambios concretos y acotados.

## Criterios de aceptación
- [ ] Comportamiento observable 1
- [ ] Hay tests y `pytest` pasa

## Fuera de alcance
Lo que NO se debe tocar.
```

## Cómo lanzarlas
```bash
gestplan run implement-task --input task_id=TASK-001 --input repo_url=<url del repo>
gestplan run implement-tasks --input task_ids=TASK-001,TASK-002 --input repo_url=<url del repo>
```
Cada run se detiene en una aprobación manual antes de publicar (`gestplan approvals list/show/approve`) y se continúa con `gestplan resume <run>`. Los planes no fusionan: un humano fusiona el PR.
