import os
from typing import Optional
from app.vector_store.base import VectorStore
from app.vector_store.sqlite_store import SQLiteVectorStore
from app.vector_store.chroma_store import ChromaDBVectorStore, CHROMA_AVAILABLE
from app.core.database import get_conn


class VectorStoreFactory:
    _instance: Optional[VectorStore] = None
    _current_engine: str = ""

    @classmethod
    def get_engine(cls, cfg: dict) -> str:
        with get_conn(cfg) as conn:
            cur = conn.cursor()
            cur.execute("SELECT engine FROM vector_store_config WHERE id = 1")
            row = cur.fetchone()
            if row:
                return row['engine']
        return 'sqlite'

    @classmethod
    def set_engine(cls, cfg: dict, engine: str) -> None:
        if engine == 'chromadb' and not CHROMA_AVAILABLE:
            raise ValueError("ChromaDB is not installed.")
        with get_conn(cfg) as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE vector_store_config 
                SET engine = ?, updated_at = CURRENT_TIMESTAMP 
                WHERE id = 1
            """, (engine,))
            conn.commit()
        # Force re-instantiation on next call
        cls._instance = None
        cls._current_engine = ""

    @classmethod
    def get_store(cls, cfg: dict, embedder=None) -> VectorStore:
        engine = cls.get_engine(cfg)
        
        if cls._instance is not None and cls._current_engine == engine:
            return cls._instance

        if engine == 'chromadb':
            if not CHROMA_AVAILABLE:
                # Fallback to sqlite if configured but not available
                engine = 'sqlite'
            else:
                chroma_path = os.path.join(cfg.get('data_dir', './data'), 'chroma_db')
                os.makedirs(chroma_path, exist_ok=True)
                cls._instance = ChromaDBVectorStore(persist_directory=chroma_path, embedder=embedder)
        
        if engine == 'sqlite':
            db_path = cfg.get('db_path', './data/app.db')
            cls._instance = SQLiteVectorStore(db_path=db_path, embedder=embedder)

        cls._current_engine = engine
        return cls._instance

    @classmethod
    def is_chroma_available(cls) -> bool:
        return CHROMA_AVAILABLE
