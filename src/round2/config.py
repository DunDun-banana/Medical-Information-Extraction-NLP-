from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import yaml
@dataclass(frozen=True)
class HybridRound2Config:
    raw:dict[str,Any]
    @property
    def server(self): return self.raw['server']
    @property
    def generation(self): return self.raw['generation']
    @property
    def pipeline(self): return self.raw.get('pipeline',{})
    @property
    def chunking(self): return self.raw.get('chunking',{})
    @property
    def llm(self): return self.raw.get('llm',{})
    @property
    def vihealthbert(self): return self.raw.get('vihealthbert',{})
    @property
    def phobert(self): return self.raw.get('phobert',{})
    @property
    def gliner(self): return self.raw.get('gliner',{})
    @property
    def verifier(self): return self.raw.get('verifier',{})
    @property
    def semantic_gate(self): return self.raw.get('semantic_gate',{})
    @property
    def assertion_scope(self): return self.raw.get('assertion_scope',{})
    @property
    def ensemble(self): return self.raw.get('ensemble',{})
    @property
    def normalization(self): return self.raw.get('normalization',{})
    @property
    def linking(self): return self.raw.get('linking',{})
    @property
    def reranker(self): return self.raw.get('reranker',{})
def load_hybrid_round2_config(path:Path)->HybridRound2Config:
    payload=yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    for required in ('server','generation'):
        if not isinstance(payload.get(required),dict): raise ValueError(f'Missing mapping: {required}')
    return HybridRound2Config(payload)
