from gestplan.plans.graph import PlanGraph
from gestplan.plans.loader import parse_plan


def build(nodes, edges):
    data = {
        "name": "t",
        "nodes": {n: {"type": "route"} for n in nodes},
        "edges": [{"from": f"{a}.x", "to": b} for a, b in edges],
    }
    plan, issues = parse_plan(data)
    assert plan is not None, issues
    return PlanGraph(plan)


def test_raices_y_alcanzabilidad():
    g = build(["a", "b", "c", "d"], [("a", "b"), ("b", "c")])
    assert g.roots == ["a", "d"]
    assert g.reachable() == {"a", "b", "c", "d"}


def test_ciclo_sin_raiz_no_es_alcanzable():
    g = build(["a", "b"], [("a", "b"), ("b", "a")])
    assert g.roots == []
    assert g.reachable() == set()


def test_componentes_ciclicos_y_has_cycle():
    g = build(["a", "b", "c", "d"], [("a", "b"), ("b", "c"), ("c", "b"), ("c", "d")])
    assert g.cyclic_components() == [["b", "c"]]
    assert g.has_cycle({"b", "c"})
    assert not g.has_cycle({"b"})


def test_dominadores_con_ramas_y_union():
    #   a -> b -> d ; a -> c -> d   => ni b ni c dominan a d; a sí
    g = build(["a", "b", "c", "d"], [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")])
    doms = g.dominators()
    assert doms["d"] == {"a", "d"}
    assert doms["b"] == {"a", "b"}


def test_dominadores_en_un_ciclo():
    g = build(["a", "b", "c"], [("a", "b"), ("b", "c"), ("c", "b")])
    assert g.dominators()["c"] == {"a", "b", "c"}


def test_antecesores():
    g = build(["a", "b", "c"], [("a", "b"), ("b", "c")])
    assert g.ancestors("c") == {"a", "b"}
    assert g.ancestors("a") == set()


def test_aristas_de_retorno_y_niveles():
    g = build(["a", "b", "c"], [("a", "b"), ("b", "c"), ("c", "b")])
    assert g.back_edges() == {("c", "b")}
    assert g.levels() == [["a"], ["b"], ["c"]]
