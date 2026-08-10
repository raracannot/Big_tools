# Shared edge-chain topology utilities
# Used by slide_edge, curvature_slide, and free_curvature_slide operators

import bmesh
from mathutils import Vector


def div_set(selected_edges):
    """Partition selected edges into connected components via flood-fill."""
    eall = set(selected_edges)
    components = []
    while eall:
        e1 = eall.pop()
        component = {e1}
        stack = [e1]
        while stack:
            cur = stack.pop()
            for v in cur.verts:
                for e2 in v.link_edges:
                    if e2 in selected_edges and e2 not in component:
                        component.add(e2)
                        stack.append(e2)
        eall -= component
        components.append(list(component))
    return components


def get_endpoints(edge_set):
    """Find vertices with exactly 1 selected edge in the given set (open ends)."""
    vs = set()
    for e in edge_set:
        vs.update(e.verts)
    ends = []
    for v in vs:
        count = sum(1 for e in v.link_edges if e in edge_set)
        if count == 1:
            ends.append(v)
    return ends


def build_edge_chain(edge_set, start_vert):
    """Walk from a start_vert along selected edges to build an ordered chain.
    Returns list of (BMVert, BMEdge) tuples in traversal order, or empty list on failure."""
    if not edge_set:
        return []
    chain = []
    visited = set()
    cur_v = start_vert
    while True:
        unvisited = [e for e in cur_v.link_edges if e in edge_set and e not in visited]
        if not unvisited:
            break
        if len(unvisited) > 1:
            # T-junction detection - prefer the edge most aligned with incoming direction
            if chain:
                in_vec = (cur_v.co - chain[-1][0].co).normalized()
                best = max(unvisited, key=lambda e: abs(in_vec.dot(
                    (e.other_vert(cur_v).co - cur_v.co).normalized())))
                e1 = best
            else:
                e1 = unvisited[0]
        else:
            e1 = unvisited[0]
        visited.add(e1)
        next_v = e1.other_vert(cur_v)
        chain.append((cur_v, e1))
        cur_v = next_v
    chain.append((cur_v, None))
    return chain


def build_edge_chain_circle(edge_set):
    """Walk selected edges in a closed loop starting from an arbitrary edge.
    Returns list of (BMVert, BMEdge) tuples or empty list."""
    if not edge_set:
        return []
    edges_list = list(edge_set)
    e_start = edges_list[0]
    p = e_start.link_loops[0] if e_start.link_loops else None
    if p is None:
        return []
    start_v = p.vert
    chain = []
    visited = set()
    cur_v = start_v
    while True:
        unvisited = [e for e in cur_v.link_edges if e in edge_set and e not in visited]
        if not unvisited:
            break
        if len(chain) > 0 and unvisited[0].other_vert(cur_v) == chain[0][0]:
            e1 = unvisited[0]
        else:
            e1 = unvisited[0]
        visited.add(e1)
        next_v = e1.other_vert(cur_v)
        chain.append((cur_v, e1))
        cur_v = next_v
        if cur_v == start_v:
            break
    return chain


def find_rail_verts(bm, edge_component):
    """For each vertex in a selected edge component, find the 2 best unselected
    'rail' edges that define the sliding direction. Returns dict:
    {BMVert: {'rail_edges': [e1, e2], 'rail_verts': [v1, v2]}}"""
    result = {}
    for e in edge_component:
        for v in e.verts:
            if v in result:
                continue
            unselected = [ed for ed in v.link_edges if ed not in edge_component]
            if not unselected:
                continue
            ref_vec = None
            for le in v.link_edges:
                if le in edge_component:
                    ref_vec = (le.other_vert(v).co - v.co).normalized()
                    break
            if ref_vec is None:
                continue
            scored = []
            for ed in unselected:
                other_v = ed.other_vert(v)
                vec = (other_v.co - v.co).normalized()
                dot = abs(ref_vec.dot(vec))
                scored.append((dot, ed, other_v))
            scored.sort(key=lambda x: -x[0])
            if len(scored) >= 1:
                result[v] = {
                    'rail_edges': [s[1] for s in scored[:2]],
                    'rail_verts': [s[2] for s in scored[:2]],
                }
    return result


def walk_rail_chain(bm, start_vert, rail_edge, selected_edge_set, max_steps=100):
    """Walk along unselected edges from start_vert to find the full rail chain.
    Returns list of BMVert in order, or empty list."""
    chain = []
    visited_edges = set()
    visited_verts = set()
    cur_v = start_vert
    cur_e = rail_edge

    while cur_e and len(chain) < max_steps:
        if cur_e in visited_edges:
            break
        visited_edges.add(cur_e)
        next_v = cur_e.other_vert(cur_v)
        chain.append(next_v)
        visited_verts.add(cur_v)
        cur_v = next_v
        prev_e = cur_e
        prev_dir = (cur_v.co - prev_e.other_vert(cur_v).co).normalized()
        cur_e = None
        best_score = -999.0
        for next_e in cur_v.link_edges:
            if next_e in visited_edges:
                continue
            if next_e in selected_edge_set:
                continue
            shared = set(prev_e.link_faces).intersection(next_e.link_faces)
            is_opposite = len(shared) == 0
            n_vec = (next_e.other_vert(cur_v).co - cur_v.co).normalized()
            dot = n_vec.dot(prev_dir)
            if not is_opposite and dot < 0.5:
                continue
            score = (10.0 if is_opposite else 0.0) + dot
            if score > best_score:
                best_score, cur_e = score, next_e
    return chain
