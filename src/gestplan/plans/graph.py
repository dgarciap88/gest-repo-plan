"""Análisis del grafo de un plan: alcanzabilidad, ciclos, dominadores y orden."""

from __future__ import annotations

from collections import deque

from gestplan.plans.models import Plan

START = "__start__"


class PlanGraph:
    def __init__(self, plan: Plan) -> None:
        self.nodes: list[str] = list(plan.nodes)
        known = set(self.nodes)
        self.edges: list[tuple[str, str, str]] = [
            (e.source_node, e.source_event, e.to)
            for e in plan.edges
            if e.source_node in known and e.to in known
        ]
        self.succ: dict[str, set[str]] = {n: set() for n in self.nodes}
        self.pred: dict[str, set[str]] = {n: set() for n in self.nodes}
        for src, _event, dst in self.edges:
            self.succ[src].add(dst)
            self.pred[dst].add(src)
        self.roots: list[str] = [n for n in self.nodes if not self.pred[n]]
        self._ancestors: dict[str, set[str]] = {}

    def reachable(self) -> set[str]:
        seen: set[str] = set()
        queue = deque(self.roots)
        while queue:
            node = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            queue.extend(self.succ[node] - seen)
        return seen

    def ancestors(self, node: str) -> set[str]:
        """Nodos desde los que se puede llegar a `node` (incluye al propio nodo si está en un ciclo)."""
        if node not in self._ancestors:
            seen: set[str] = set()
            queue = deque(self.pred[node])
            while queue:
                current = queue.popleft()
                if current in seen:
                    continue
                seen.add(current)
                queue.extend(self.pred[current] - seen)
            self._ancestors[node] = seen
        return self._ancestors[node]

    def cyclic_components(self) -> list[list[str]]:
        """Componentes fuertemente conexas con ciclo (Tarjan)."""
        index: dict[str, int] = {}
        low: dict[str, int] = {}
        on_stack: set[str] = set()
        stack: list[str] = []
        result: list[list[str]] = []
        counter = 0

        def visit(node: str) -> None:
            nonlocal counter
            index[node] = low[node] = counter
            counter += 1
            stack.append(node)
            on_stack.add(node)
            for nxt in sorted(self.succ[node]):
                if nxt not in index:
                    visit(nxt)
                    low[node] = min(low[node], low[nxt])
                elif nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if low[node] == index[node]:
                component: list[str] = []
                while True:
                    top = stack.pop()
                    on_stack.discard(top)
                    component.append(top)
                    if top == node:
                        break
                if len(component) > 1 or node in self.succ[node]:
                    result.append(sorted(component))

        for node in self.nodes:
            if node not in index:
                visit(node)
        return result

    def has_cycle(self, subset: set[str]) -> bool:
        """¿Hay algún ciclo dentro del subgrafo inducido por `subset`?"""
        state: dict[str, int] = {}

        def visit(node: str) -> bool:
            state[node] = 1
            for nxt in self.succ[node] & subset:
                if state.get(nxt) == 1:
                    return True
                if nxt not in state and visit(nxt):
                    return True
            state[node] = 2
            return False

        return any(node not in state and visit(node) for node in sorted(subset))

    def dominators(self) -> dict[str, set[str]]:
        """Dominadores de cada nodo alcanzable (un nodo se domina a sí mismo)."""
        reachable = self.reachable()
        doms: dict[str, set[str]] = {n: set(reachable) for n in reachable}
        changed = True
        while changed:
            changed = False
            for node in sorted(reachable):
                preds = self.pred[node] & reachable
                if node in self.roots:
                    new = {node}
                elif preds:
                    new = set.intersection(*(doms[p] for p in preds)) | {node}
                else:
                    new = {node}
                if new != doms[node]:
                    doms[node] = new
                    changed = True
        return doms

    def back_edges(self) -> set[tuple[str, str]]:
        """Aristas de retorno (las que cierran un ciclo) según un recorrido en profundidad."""
        state: dict[str, int] = {}
        back: set[tuple[str, str]] = set()

        def visit(node: str) -> None:
            state[node] = 1
            for nxt in sorted(self.succ[node]):
                if state.get(nxt) == 1:
                    back.add((node, nxt))
                elif nxt not in state:
                    visit(nxt)
            state[node] = 2

        for root in sorted(self.roots):
            if root not in state:
                visit(root)
        return back

    def levels(self) -> list[list[str]]:
        """Orden por niveles ignorando las aristas de retorno (nodos del mismo nivel son paralelos)."""
        back = self.back_edges()
        pending = {n: {p for p in self.pred[n] if (p, n) not in back} for n in self.reachable()}
        done: set[str] = set()
        levels: list[list[str]] = []
        while len(done) < len(pending):
            ready = sorted(n for n, deps in pending.items() if n not in done and deps <= done)
            if not ready:
                break
            levels.append(ready)
            done.update(ready)
        return levels
