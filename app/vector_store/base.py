from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from abc import ABC, abstractmethod


class Chunk(BaseModel):
    id: str
    file_id: str
    text: str
    vector: Optional[List[float]] = None
    metadata: Dict[str, Any] = {}


class SearchResult(BaseModel):
    id: str
    file_id: str
    text: str
    score: float
    metadata: Dict[str, Any] = {}


class VectorStore(ABC):
    @abstractmethod
    def initialize(self, collection_name: str) -> None:
        pass

    @abstractmethod
    def insert_chunks(self, chunks: List[Chunk]) -> None:
        pass

    @abstractmethod
    def search_vectors(self, query: str, top_k: int, **kwargs) -> List[SearchResult]:
        pass

    @abstractmethod
    def delete_by_file_id(self, file_id: str) -> None:
        pass

    @abstractmethod
    def health_check(self) -> bool:
        pass
