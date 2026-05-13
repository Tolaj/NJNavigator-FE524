"""Semantic search helpers used by the MCP server's search_knowledge tool."""

from langchain_chroma import Chroma


def search_knowledge(vs: Chroma, query: str, k: int = 5) -> list[str]:
    """Search across Wikipedia articles and MTA alerts."""
    try:
        return [d.page_content for d in vs.similarity_search(query, k=k)]
    except Exception as e:
        return [f"Search error: {e}"]


def search_alerts_only(vs: Chroma, query: str, k: int = 5) -> list[str]:
    """Search only MTA alert documents."""
    try:
        return [d.page_content for d in vs.similarity_search(
            query, k=k, filter={"source": "mta_alert"}
        )]
    except Exception as e:
        return [f"Search error: {e}"]
