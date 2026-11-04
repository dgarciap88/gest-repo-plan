"""confluence.get_page: storage format → bloques, para modificar una página y reescribirla."""


from gestplan.atlassian import blocks_to_markdown, render_storage, storage_to_blocks
from gestplan.engine.http import FakeHttp, json_response
from tests.helpers import build, drive
from tests.test_atlassian_more import settings_engine

BLOCKS = [
    {"type": "heading", "level": 2, "text": "Resumen"},
    {"type": "paragraph", "text": "línea 1\nlínea 2 & <b>"},
    {"type": "list", "items": ["uno", "dos"], "ordered": True},
    {"type": "list", "items": ["a"], "ordered": False},
    {"type": "table", "columns": ["Clave", "Valor"], "rows": [["x", "1"], ["y", "2"]]},
    {"type": "code", "language": "python", "text": "if a < b and ']]>':\n    pass"},
]

RICH = (
    "<h1>Título</h1><p>Texto con <strong>negrita</strong> y <a href=\"http://x\">enlace</a>.</p>"
    "<ul><li>uno<ul><li>anidado</li></ul></li><li>dos</li></ul>"
    '<ac:structured-macro ac:name="info"><ac:rich-text-body><p>aviso</p></ac:rich-text-body></ac:structured-macro>'
    '<p><ac:image><ri:attachment ri:filename="a.png"/></ac:image></p>'
    "<p><code>gestplan run:abc node:pagina</code></p><hr/>"
)


def test_ida_y_vuelta_de_la_plantilla_no_pierde_nada():
    blocks, unsupported = storage_to_blocks(render_storage(BLOCKS))
    assert unsupported == []
    expected = [{**b, "ordered": b.get("ordered", False)} if b["type"] == "list" else b for b in BLOCKS]
    assert [{k: v for k, v in b.items()} for b in blocks] == expected


def test_macros_y_elementos_raros_se_conservan_y_lo_demas_se_avisa():
    blocks, unsupported = storage_to_blocks(RICH)
    assert [b["type"] for b in blocks] == ["heading", "paragraph", "list", "macro", "raw", "raw"]  # imagen y línea horizontal
    assert blocks[1]["text"] == "Texto con negrita y enlace."
    assert blocks[2]["items"] == ["uno", "anidado", "dos"]
    assert blocks[3] == {"type": "macro", "name": "info", "params": {}, "body": [{"type": "paragraph", "text": "aviso"}]}
    assert "ac:image" in blocks[4]["storage"] and "a.png" in blocks[4]["storage"]  # la imagen no se pierde
    joined = " | ".join(unsupported)
    assert "strong" in joined and "listas anidadas" in joined and "info" not in joined
    assert "gestplan run" not in str(blocks)  # el marcador no es contenido
    assert "ri:filename=\"a.png\"" in render_storage(blocks[3:5])  # al reescribir sigue la imagen


def test_macro_de_jira_ida_y_vuelta():
    macro = {"type": "macro", "name": "jira", "params": {"jqlQuery": "parent is not EMPTY <x>", "columns": "key,summary",
                                                         "maximumIssues": 20, "count": False}}
    storage = render_storage([macro, {"type": "macro", "name": "noformat", "text": "a ]]> b"}])
    assert "&lt;x&gt;" in storage and 'ac:name="maximumIssues">20<' in storage and ">false<" in storage
    blocks, unsupported = storage_to_blocks(storage)
    assert unsupported == [] and blocks[0]["params"]["jqlQuery"] == "parent is not EMPTY <x>"
    assert blocks[0]["params"]["maximumIssues"] == "20" and blocks[1]["text"] == "a ]]> b"
    assert render_storage(blocks) == render_storage(blocks_roundtrip(blocks))


def blocks_roundtrip(blocks):
    return storage_to_blocks(render_storage(blocks))[0]


def test_bloques_macro_y_raw_se_validan():
    from gestplan.nodes.confluence import ConfluenceCreatePage

    cfg = ConfluenceCreatePage.ConfigModel.model_validate({"space": "A", "title": "t", "blocks": [
        {"type": "macro", "name": "info", "body": [{"type": "paragraph", "text": "x"}]},
        {"type": "raw", "storage": "<p>hola</p>"}]})
    assert cfg.blocks[0].body[0].text == "x"


def test_markdown_de_lectura():
    text = blocks_to_markdown(BLOCKS)
    assert text.startswith("## Resumen") and "1. uno" in text and "| Clave | Valor |" in text and "```python" in text


class Wiki:
    def __init__(self, storage):
        self.page = {"id": "77", "title": "Informe", "version": {"number": 3}, "space": {"key": "MFS"},
                     "ancestors": [{"id": "1"}, {"id": "5"}], "body": {"storage": {"value": storage}},
                     "_links": {"webui": "/spaces/MFS/pages/77", "base": "https://w.example/wiki"}}

    def __call__(self, method, url, body):
        if method == "GET" and "/rest/api/content/77?" in url:
            return json_response(self.page)
        if method == "GET" and "title=Informe" in url and "spaceKey=MFS" in url:
            return json_response({"results": [self.page]})
        if method == "GET" and "title=Nada" in url:
            return json_response({"results": []})
        return None


def run_get(tmp_path, storage, **config):
    engine = settings_engine(tmp_path, FakeHttp(Wiki(storage)))
    run_id = build(engine, {"name": "lee", "nodes": {"p": {"type": "confluence.get_page", "config": config}}})
    drive(engine, run_id)
    return engine, run_id


def test_leer_por_id_entrega_bloques_y_metadatos(tmp_path):
    engine, run_id = run_get(tmp_path, render_storage(BLOCKS), page_id="77")
    out = engine.store.latest_outputs(run_id, "p")
    assert out["title"] == "Informe" and out["space"] == "MFS" and out["parent_id"] == "5" and out["version"] == 3
    assert out["lossless"] is True and out["unsupported"] == [] and len(out["blocks"]) == len(BLOCKS)
    assert out["url"] == "https://w.example/wiki/spaces/MFS/pages/77" and out["text"].startswith("## Resumen")
    assert engine.store.attempts(run_id, "p")[-1]["event"] == "ok"


def test_leer_por_titulo_y_no_encontrada(tmp_path):
    engine, run_id = run_get(tmp_path, "<p>hola</p>", space="MFS", title="Informe")
    assert engine.store.latest_outputs(run_id, "p")["blocks"] == [{"type": "paragraph", "text": "hola"}]
    engine, run_id = run_get(tmp_path, "<p>hola</p>", space="MFS", title="Nada")
    assert engine.store.attempts(run_id, "p")[-1]["event"] == "not_found"


def test_strict_emite_lossy_si_se_perderia_contenido(tmp_path):
    engine, run_id = run_get(tmp_path, RICH, page_id="77", strict=True)
    last = engine.store.attempts(run_id, "p")[-1]
    assert last["event"] == "lossy" and "listas anidadas" in last["error"]
    engine, run_id = run_get(tmp_path, RICH, page_id="77")  # sin strict: ok, pero lo declara
    assert engine.store.attempts(run_id, "p")[-1]["event"] == "ok"
    assert engine.store.latest_outputs(run_id, "p")["lossless"] is False


def test_exige_id_o_espacio_y_titulo(root):
    from tests.test_validator import codes, run

    plan = {"name": "x", "nodes": {"p": {"type": "confluence.get_page", "config": {"space": "A"}}}, "edges": []}
    assert "invalid_config" in codes(run(plan, root))
    plan["nodes"]["p"]["config"] = {"page_id": "5"}
    assert "invalid_config" not in codes(run(plan, root))
