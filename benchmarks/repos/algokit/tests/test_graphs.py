import pytest

from algokit.graphs import (
    CycleError,
    bfs_order,
    dijkstra,
    from_edges,
    shortest_hops,
    shortest_path,
    topological_sort,
)


def test_from_edges_undirected():
    assert from_edges([("a", "b"), ("b", "c")], directed=False) == {
        "a": ["b"],
        "b": ["a", "c"],
        "c": ["b"],
    }


def test_bfs_order_and_hops():
    graph = {1: [2, 3], 2: [4], 3: [4], 4: []}
    assert bfs_order(graph, 1) == [1, 2, 3, 4]
    assert shortest_hops(graph, 1, 4) == [1, 2, 4]
    assert shortest_hops(graph, 4, 1) is None


def test_dijkstra_simple_chain():
    graph = {"a": [("b", 2)], "b": [("c", 3)], "c": []}
    distances, _ = dijkstra(graph, "a")
    assert distances == {"a": 0, "b": 2, "c": 5}


def test_shortest_path_unreachable():
    with pytest.raises(KeyError):
        shortest_path({"a": [], "b": []}, "a", "b")


def test_dijkstra_rejects_negative_weights():
    with pytest.raises(ValueError):
        dijkstra({"a": [("b", -1)]}, "a")


def test_topological_sort():
    graph = {"shirt": ["tie"], "tie": ["jacket"], "pants": ["shoes", "jacket"], "shoes": []}
    order = topological_sort(graph)
    assert order.index("shirt") < order.index("tie") < order.index("jacket")
    assert order.index("pants") < order.index("shoes")


def test_topological_sort_cycle():
    with pytest.raises(CycleError):
        topological_sort({"a": ["b"], "b": ["a"]})
