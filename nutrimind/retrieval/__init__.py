"""Retrieval layer: PDF ingestion, chunking, embeddings, vector search.

Public surface used by the service layer:

* :func:`nutrimind.retrieval.embeddings.resolve_embedding_provider`
* :class:`nutrimind.retrieval.vector_store.VectorStore`
* :func:`nutrimind.retrieval.ingestion.ingest_pdf`
* :class:`nutrimind.retrieval.retriever.Retriever`
"""
