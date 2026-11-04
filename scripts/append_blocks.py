"""Añade bloques al final de una lista de bloques de Confluence (para modificar una página leída con get_page)."""

META = {
    "description": "Devuelve blocks + extra (concatenados)",
    "side_effect": "none",
    "events": ["ok", "failed"],
    "failure_events": ["failed"],
}


def run(inputs, ctx):
    blocks, extra = inputs.get("blocks"), inputs.get("extra")
    if not isinstance(blocks, list) or not isinstance(extra, list):
        return {"event": "failed", "outputs": {"error": "blocks y extra deben ser listas de bloques"}}
    return {"event": "ok", "outputs": {"blocks": blocks + extra, "count": len(blocks) + len(extra)}}
