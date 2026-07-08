import os
from typing import List, Dict, Any, Optional
from app.vector_store.base import VectorStore, Chunk, SearchResult

try:
    import chromadb
    from chromadb.config import Settings
    CHROMA_AVAILABLE = True
except ImportError:
    CHROMA_AVAILABLE = False


class ChromaDBVectorStore(VectorStore):
    def __init__(self, persist_directory: str, embedder=None):
        if not CHROMA_AVAILABLE:
            raise ImportError("chromadb is not installed. Please install it using 'pip install chromadb'")
        self.persist_directory = persist_directory
        self.embedder = embedder
        self.client = chromadb.PersistentClient(path=self.persist_directory)
        self.collection = None

    def initialize(self, collection_name: str) -> None:
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}
        )

    def add_texts(self, texts: List[str], metadatas: Optional[List[Dict[str, Any]]] = None, ids: Optional[List[str]] = None, embeddings: Optional[List[List[float]]] = None) -> None:
        if not self.collection:
            raise ValueError("Collection not initialized")
        if not texts:
            return
        if ids is None:
            ids = [f"doc_{i}" for i in range(len(texts))]
        if metadatas is None:
            metadatas = [{} for _ in texts]

        unique_ids: List[str] = []
        unique_texts: List[str] = []
        unique_metas: List[Dict[str, Any]] = []
        unique_embeddings: List[List[float]] = []
        seen = set()
        has_embeddings = embeddings is not None and len(embeddings) == len(texts)

        for i, doc_id in enumerate(ids):
            if doc_id in seen:
                continue
            seen.add(doc_id)
            unique_ids.append(doc_id)
            unique_texts.append(texts[i])
            unique_metas.append(metadatas[i] if i < len(metadatas) else {})
            if has_embeddings:
                unique_embeddings.append(embeddings[i])

        payload: Dict[str, Any] = {
            "ids": unique_ids,
            "documents": unique_texts,
            "metadatas": unique_metas,
        }
        if has_embeddings:
            payload["embeddings"] = unique_embeddings
        self.collection.upsert(**payload)

    def insert_chunks(self, chunks: List[Chunk]) -> None:
        if not self.collection:
            raise ValueError("Collection not initialized")
        
        if not chunks:
            return
            
        ids = [chunk.id for chunk in chunks]
        documents = [chunk.text for chunk in chunks]
        metadatas = []
        for chunk in chunks:
            meta = chunk.metadata.copy()
            meta['file_id'] = chunk.file_id
            metadatas.append(meta)
            
        embeddings = [chunk.vector for chunk in chunks if chunk.vector]
        
        if len(embeddings) == len(chunks):
            self.collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=embeddings
            )
        else:
            # If no vectors provided, chroma can use its default embedding function, 
            # but we prefer to provide embeddings or fail if not configured
            self.collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas
            )

    def search_vectors(self, query: str, top_k: int, **kwargs) -> List[SearchResult]:
        if not self.collection:
            raise ValueError("Collection not initialized")
            
        query_vector = kwargs.get('query_vector')
        if not query_vector and self.embedder:
            query_vector = self.embedder.compute_embedding(query, is_query=True)
            
        if query_vector is not None:
            # Note: ChromaDB requires list of lists for query_embeddings
            # but we pass a single vector, so we wrap it
            if isinstance(query_vector, type(None)):
                 pass # Will not reach here
            
            # Make sure it's a python list of floats
            if hasattr(query_vector, 'tolist'):
                query_vector = query_vector.tolist()
                
            results = self.collection.query(
                query_embeddings=[query_vector],
                n_results=top_k
            )
        else:
            results = self.collection.query(
                query_texts=[query],
                n_results=top_k
            )
            
        search_results = []
        if not results['ids'] or not results['ids'][0]:
            return search_results
            
        for i in range(len(results['ids'][0])):
            doc_id = results['ids'][0][i]
            text = results['documents'][0][i] if results['documents'] else ""
            meta = results['metadatas'][0][i] if results['metadatas'] else {}
            # distances in cosine space, smaller is better (usually 1 - cosine_sim)
            # convert back to score where higher is better
            dist = results['distances'][0][i] if results['distances'] else 0.0
            score = 1.0 - dist
            
            search_results.append(SearchResult(
                id=doc_id,
                file_id=meta.get('file_id', ''),
                text=text,
                score=score,
                metadata=meta
            ))
            
        return search_results

    def delete_by_file_id(self, file_id: str) -> None:
        if not self.collection:
            raise ValueError("Collection not initialized")
        self.collection.delete(
            where={"file_id": file_id}
        )

    def health_check(self) -> bool:
        try:
            self.client.heartbeat()
            return True
        except Exception:
            return False
