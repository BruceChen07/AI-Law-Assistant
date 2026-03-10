from typing import Optional
from pydantic import BaseModel


class SearchQuery(BaseModel):
    query: str
    language: str = "zh"
    top_k: int = 10
    date: Optional[str] = None
    region: Optional[str] = None
    industry: Optional[str] = None
    use_semantic: bool = False
    semantic_weight: float = 0.6
    bm25_weight: float = 0.4
    candidate_size: int = 200


class EmbeddingRequest(BaseModel):
    text: str
    is_query: bool = False
    language: str = "zh"
