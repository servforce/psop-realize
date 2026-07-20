from __future__ import annotations

from app.db.session import SessionLocal, init_db
from app.services.audit import finish_call, logged_call
from app.services.standards import standard_service
from app.services.wireframes import wireframe_service


def create_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as exc:  # pragma: no cover - import depends on optional package
        raise RuntimeError("The mcp package is required to run the MCP server. Install requirements.txt first.") from exc

    init_db()
    mcp = FastMCP("servforce_standard_markdown_server")

    @mcp.tool()
    def get_standard_overview_md(standard_id: str, caller: str = "local-mcp") -> dict:
        """Read overview.md for a materialized standard."""
        return _read_markdown_tool("get_standard_overview_md", standard_id, "overview", caller)

    @mcp.tool()
    def get_standard_structure_md(standard_id: str, caller: str = "local-mcp") -> dict:
        """Read structure.md for a materialized standard."""
        return _read_markdown_tool("get_standard_structure_md", standard_id, "structure", caller)

    @mcp.tool()
    def get_standard_logic_md(standard_id: str, caller: str = "local-mcp") -> dict:
        """Read logic.md for a materialized standard."""
        return _read_markdown_tool("get_standard_logic_md", standard_id, "logic", caller)

    @mcp.tool()
    def get_standard_body_md(standard_id: str, caller: str = "local-mcp") -> dict:
        """Read body.md for a materialized standard."""
        return _read_markdown_tool("get_standard_body_md", standard_id, "body", caller)

    @mcp.tool()
    def search_standards(query: str, limit: int = 5, caller: str = "local-mcp") -> dict:
        """Search materialized standards by text."""
        with SessionLocal() as session:
            with logged_call(
                session,
                interface_type="mcp",
                tool_or_endpoint="search_standards",
                caller=caller,
                request={"query": query, "limit": limit},
            ) as call_id:
                result = standard_service.search(session, query=query, limit=limit)
                finish_call(session, call_id, result)
                result["call_id"] = call_id
                return result

    @mcp.tool()
    def generate_frame_wireframe(frame_id: int, caller: str = "local-mcp") -> dict:
        """Generate a Qwen wireframe PNG for one extracted video frame."""
        with SessionLocal() as session:
            with logged_call(
                session,
                interface_type="mcp",
                tool_or_endpoint="generate_frame_wireframe",
                caller=caller,
                request={"frame_id": frame_id},
            ) as call_id:
                result = wireframe_service.generate_wireframe(session, frame_id)
                finish_call(session, call_id, result)
                result["call_id"] = call_id
                return result

    return mcp


def _read_markdown_tool(tool_name: str, standard_id: str, kind: str, caller: str) -> dict:
    with SessionLocal() as session:
        with logged_call(
            session,
            interface_type="mcp",
            tool_or_endpoint=tool_name,
            caller=caller,
            request={"standard_id": standard_id, "kind": kind},
            standard_id=standard_id,
        ) as call_id:
            result = standard_service.get_markdown(session, standard_id, kind)
            finish_call(session, call_id, {"chars": len(result["markdown"]), "artifact": result["artifact"]})
            result["call_id"] = call_id
            return result


def main() -> None:
    mcp = create_server()
    mcp.run("stdio")


if __name__ == "__main__":
    main()
