"""Convierte una lista de ids de tarea en los inputs de los runs hijos de `implement-task`."""

META = {
    "description": "Construye la lista de inputs (task_id, repo_url, base) para launch_plans",
    "side_effect": "none",
    "events": ["ok", "failed"],
    "failure_events": ["failed"],
}


def run(inputs, ctx):
    task_ids = inputs.get("task_ids")
    if isinstance(task_ids, str):
        task_ids = [part.strip() for part in task_ids.split(",") if part.strip()]
    if not task_ids:
        return {"event": "failed", "outputs": {"error": "no se indicó ninguna tarea"}}
    items = [
        {"task_id": task_id, "repo_url": inputs["repo_url"], "base": inputs.get("base") or "main"}
        for task_id in task_ids
    ]
    return {"event": "ok", "outputs": {"items": items, "count": len(items)}}
