# Medical Information Extraction from Vietnamese Clinical Text

A hybrid NLP pipeline for extracting medical entities and clinical attributes from Vietnamese clinical text.

## Overview

This project develops a hybrid medical information extraction system for Vietnamese clinical notes. The pipeline combines Transformer-based NLP models, Large Language Models, rule-based methods, medical dictionaries, retrieval and reranking techniques.

The system is designed to extract medical entities together with attributes such as entity type, assertion status and medical code candidates.

## Key Features

- Vietnamese medical Named Entity Recognition (NER)
- Section-aware clinical text parsing
- Assertion detection
- Medical entity linking
- Candidate retrieval and reranking
- Rule-based and dictionary-based extraction
- LLM-assisted verification
- Local/offline LLM inference

## System Architecture

```text
Vietnamese Clinical Text
          │
          ▼
   Document Parser
          │
          ├── Section Detection
          │
          ├── Rule-based Extraction
          │
          ├── ViHealthBERT NER
          │
          └── GLiNER
          │
          ▼
      Entity Candidates
          │
          ├── Medical Dictionary
          ├── BM25 Retrieval
          ├── BGE-M3 Embedding Retrieval
          └── BGE Reranker
          │
          ▼
    Candidate Linking
          │
          ├── Assertion Detection
          └── Attribute Extraction
          │
          ▼
       Qwen3-4B
   Verification / Reasoning
          │
          ▼
      Final Prediction