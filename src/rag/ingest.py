"""
Builds the ChromaDB knowledge base from two sources:
  1. Wikipedia articles about NJ/NYC transit  (loaded once, persisted to disk)
  2. Live MTA service alerts                  (refreshed every app startup)
"""

import os
from pathlib import Path
from typing import Any

from langchain_chroma import Chroma
from langchain_community.document_loaders import WebBaseLoader
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

WIKI_SOURCES = [
    "https://en.wikipedia.org/wiki/New_York_City_Subway",
    "https://en.wikipedia.org/wiki/PATH_(rail_system)",
    "https://en.wikipedia.org/wiki/NJ_Transit_Rail_Operations",
    "https://en.wikipedia.org/wiki/General_Transit_Feed_Specification",
    "https://en.wikipedia.org/wiki/Metropolitan_Transportation_Authority",
]

BASE_DIR   = Path(__file__).resolve().parents[2]
CHROMA_DIR = str(BASE_DIR / "data" / "chroma_db")
COLLECTION = "njnavigator"

_splitter = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=80)


def _embeddings() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(model="text-embedding-3-small")


def _vectorstore() -> Chroma:
    return Chroma(
        collection_name=COLLECTION,
        embedding_function=_embeddings(),
        persist_directory=CHROMA_DIR,
    )


def _wiki_already_loaded(vs: Chroma) -> bool:
    try:
        r = vs.get(where={"source": "wikipedia"}, limit=1)
        return len(r["ids"]) > 0
    except Exception:
        return False


def ingest_wikipedia(vs: Chroma) -> int:
    if _wiki_already_loaded(vs):
        print("  [RAG] Wikipedia already in ChromaDB — skipping")
        return 0

    print("  [RAG] Loading Wikipedia articles ...")
    docs: list[Document] = []
    for url in WIKI_SOURCES:
        try:
            raw = WebBaseLoader(url).load()
            for d in raw:
                d.metadata["source"] = "wikipedia"
                d.metadata["url"] = url
            docs.extend(raw)
            print(f"    {url.split('/wiki/')[-1]}")
        except Exception as e:
            print(f"    SKIP {url}: {e}")

    if not docs:
        return 0
    chunks = _splitter.split_documents(docs)
    vs.add_documents(chunks)
    print(f"  [RAG] Wikipedia: {len(chunks)} chunks stored")
    return len(chunks)


def ingest_alerts(vs: Chroma, alerts: list[dict[str, Any]]) -> int:
    # Remove stale alerts before inserting fresh ones
    try:
        old = vs.get(where={"source": "mta_alert"})
        if old["ids"]:
            vs.delete(ids=old["ids"])
    except Exception:
        pass

    if not alerts:
        print("  [RAG] No alerts to ingest")
        return 0

    docs = []
    for a in alerts:
        text = a["header"]
        if a.get("description"):
            text += f"\n{a['description']}"
        docs.append(Document(
            page_content=text,
            metadata={
                "source":          "mta_alert",
                "affected_routes": ", ".join(a.get("affected_routes", [])),
            },
        ))

    vs.add_documents(docs)
    print(f"  [RAG] Alerts: {len(docs)} stored")
    return len(docs)


def build_vectorstore(alerts: list[dict[str, Any]] | None = None) -> Chroma:
    """
    Initialise ChromaDB, ingest Wikipedia (once), refresh alerts.
    Returns the ready Chroma instance.
    """
    print("\n[RAG] Building knowledge base ...")
    vs = _vectorstore()
    ingest_wikipedia(vs)
    ingest_alerts(vs, alerts or [])
    print("[RAG] Knowledge base ready.\n")
    return vs
