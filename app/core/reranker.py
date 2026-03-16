import logging
from typing import List, Dict, Any, Optional

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

logger = logging.getLogger("law_assistant")


class RerankerService:
    def __init__(self, model_path: Optional[str] = None, profiles: Optional[Dict[str, str]] = None, device: Optional[str] = None, batch_size: int = 8, max_len: int = 512):
        self.model_path = model_path
        self.profiles = profiles or {}
        self.models = {}
        self.tokenizers = {}
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.batch_size = batch_size
        self.max_len = max_len
        if model_path:
            self._load_model(model_path, "default")

    def _load_model(self, model_path: str, key: str):
        try:
            logger.info("Loading reranker model from %s on %s", model_path, self.device)
            tokenizer = AutoTokenizer.from_pretrained(model_path)
            model = AutoModelForSequenceClassification.from_pretrained(model_path)
            model.to(self.device)
            model.eval()
            self.models[key] = model
            self.tokenizers[key] = tokenizer
            logger.info("Reranker model loaded")
        except Exception as e:
            logger.error("Failed to load reranker model: %s", str(e))
            self.models.pop(key, None)
            self.tokenizers.pop(key, None)

    def _get_model(self, lang: Optional[str]):
        key = lang or "default"
        if key in self.models and key in self.tokenizers:
            return self.models[key], self.tokenizers[key]
        path = None
        if lang and self.profiles.get(lang):
            path = self.profiles.get(lang)
        elif self.model_path:
            path = self.model_path
        if not path:
            return None, None
        self._load_model(path, key)
        return self.models.get(key), self.tokenizers.get(key)

    def compute_score(self, query: str, text: str, lang: Optional[str] = None) -> float:
        model, tokenizer = self._get_model(lang)
        if not model or not tokenizer:
            return 0.0
        with torch.no_grad():
            inputs = tokenizer([[query, text]], padding=True, truncation=True, return_tensors="pt", max_length=self.max_len)
            inputs = {k: v.to(self.device) for k, v in inputs.items()}
            scores = model(**inputs, return_dict=True).logits.view(-1,).float()
            return float(scores[0])

    def rerank(self, query: str, candidates: List[Dict[str, Any]], top_k: Optional[int] = None, lang: Optional[str] = None) -> List[Dict[str, Any]]:
        model, tokenizer = self._get_model(lang)
        if not model or not tokenizer or not candidates:
            return candidates[:top_k] if top_k else candidates

        pairs = [[query, c.get("content", "")] for c in candidates]
        all_scores = []

        for i in range(0, len(pairs), self.batch_size):
            batch_pairs = pairs[i:i + self.batch_size]
            with torch.no_grad():
                inputs = tokenizer(batch_pairs, padding=True, truncation=True, return_tensors="pt", max_length=self.max_len)
                inputs = {k: v.to(self.device) for k, v in inputs.items()}
                scores = model(**inputs, return_dict=True).logits.view(-1,).float()
                all_scores.extend(scores.cpu().numpy().tolist())

        for i, c in enumerate(candidates):
            c["rerank_score"] = float(all_scores[i])
            c["final_score"] = float(all_scores[i])

        candidates.sort(key=lambda x: x.get("rerank_score", 0.0), reverse=True)
        return candidates[:top_k] if top_k else candidates