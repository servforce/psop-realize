from __future__ import annotations

import re
from typing import Any


class QueryGraphError(ValueError):
    pass


def normalize_query_graph(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise QueryGraphError("section.query_graph is required; please regenerate the structured transcript with the current query_graph prompt")

    raw_nodes = value.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise QueryGraphError("section.query_graph.nodes must be a non-empty list")

    nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict):
            raise QueryGraphError("section.query_graph.nodes items must be objects")
        node_id = normalize_graph_id(raw_node.get("id"))
        label = normalize_graph_label(raw_node.get("label"))
        role = normalize_graph_id(raw_node.get("role"))
        if not node_id or not label or not role:
            raise QueryGraphError("section.query_graph node must include id, label, and role")
        if node_id in node_ids:
            raise QueryGraphError(f"section.query_graph node id is duplicated: {node_id}")
        if not isinstance(raw_node.get("required"), bool):
            raise QueryGraphError("section.query_graph node.required must be boolean")
        weight = parse_weight(raw_node.get("weight"), field_name="section.query_graph node.weight")
        nodes.append(
            {
                "id": node_id,
                "label": label,
                "role": role,
                "required": bool(raw_node["required"]),
                "weight": weight,
            }
        )
        node_ids.add(node_id)

    if not nodes:
        raise QueryGraphError("section.query_graph must include at least one detectable object node")

    raw_edges = value.get("edges")
    if not isinstance(raw_edges, list):
        raise QueryGraphError("section.query_graph.edges must be a list")

    edges: list[dict[str, Any]] = []
    for raw_edge in raw_edges:
        if not isinstance(raw_edge, dict):
            raise QueryGraphError("section.query_graph.edges items must be objects")
        source = normalize_graph_id(raw_edge.get("from") or raw_edge.get("source"))
        relation = normalize_graph_relation(raw_edge.get("relation") or raw_edge.get("type"))
        target = normalize_graph_id(raw_edge.get("to") or raw_edge.get("target"))
        if not source or not relation or not target:
            raise QueryGraphError("section.query_graph edge must include from, relation, and to")
        if source not in node_ids or target not in node_ids:
            raise QueryGraphError("section.query_graph edge endpoint must reference an existing node id")
        if not isinstance(raw_edge.get("required"), bool):
            raise QueryGraphError("section.query_graph edge.required must be boolean")
        weight = parse_weight(raw_edge.get("weight"), field_name="section.query_graph edge.weight")
        edges.append(
            {
                "from": source,
                "relation": relation,
                "to": target,
                "required": bool(raw_edge["required"]),
                "weight": weight,
            }
        )

    return {"nodes": nodes, "edges": edges}


def query_graph_prompt_terms(query_graph: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for node in query_graph.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        label = normalize_graph_label(node.get("label"))
        if label:
            terms.append(label)
    return list(dict.fromkeys(terms))


def normalize_graph_label(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text).strip()
    return " ".join(text.split())


def normalize_graph_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"[^0-9a-z_]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def normalize_graph_relation(value: Any) -> str:
    return normalize_graph_id(value)


def parse_weight(value: Any, *, field_name: str) -> float:
    if not isinstance(value, (int, float)):
        raise QueryGraphError(f"{field_name} must be numeric, got {value!r} ({type(value).__name__})")
    weight = float(value)
    if weight < 0.0:
        raise QueryGraphError(f"{field_name} must be non-negative, got {weight}")
    return weight
