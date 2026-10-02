from algokit.graphs import dijkstra, shortest_path


def test_cheaper_path_found_later():
    graph = {"a": [("b", 10), ("c", 1)], "c": [("b", 2)], "b": [("d", 1)], "d": []}
    assert shortest_path(graph, "a", "d") == (4, ["a", "c", "b", "d"])


def test_distances_are_minimal():
    graph = {
        1: [(2, 7), (3, 9), (6, 14)],
        2: [(3, 10), (4, 15)],
        3: [(4, 11), (6, 2)],
        4: [(5, 6)],
        6: [(5, 9)],
    }
    distances, predecessors = dijkstra(graph, 1)
    assert distances == {1: 0, 2: 7, 3: 9, 4: 20, 5: 20, 6: 11}
    assert predecessors[6] == 3
    assert predecessors[5] == 6


def test_direct_edge_still_used_when_cheapest():
    graph = {"s": [("t", 1), ("m", 1)], "m": [("t", 5)]}
    assert shortest_path(graph, "s", "t") == (1, ["s", "t"])
