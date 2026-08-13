# Retrieval-Augmented Generation: An Overview

Retrieval-Augmented Generation, or RAG, is a technique for grounding large
language models in an external corpus of documents. Rather than relying only on
the parametric knowledge baked into the model's weights, a RAG system retrieves
relevant passages at query time and inserts them into the prompt, allowing the
model to cite specific, up-to-date sources.

The pattern was popularised by researchers at Meta AI in 2020 and has since
become the default architecture for enterprise chatbots, coding assistants, and
knowledge-management tools.

## How it works

A RAG pipeline has three stages: **indexing**, **retrieval**, and
**generation**. During indexing, source documents are split into chunks,
embedded with a vector model (such as OpenAI's `text-embedding-3-small` or the
open-weights `nomic-embed-text` from Nomic AI), and stored in a vector
database. Popular vector stores include Pinecone, Weaviate, Qdrant, and the
lightweight `sqlite-vec` extension.

At query time the user's question is embedded with the same model. A
nearest-neighbour search returns the top-k passages. These are prepended to
the prompt along with an instruction like "Answer the user's question using
ONLY the following context. If the context is insufficient, say so."

## Hybrid retrieval

Modern RAG systems rarely use dense vector search alone. A common improvement
is to fuse the vector ranking with a lexical BM25 score from a full-text index
using Reciprocal Rank Fusion. The intuition: dense vectors catch semantic
similarity, BM25 catches exact-term overlap, and neither is strictly better
than the other. SQLite's FTS5 module provides a straightforward BM25 backend
that pairs well with `sqlite-vec` in a single database file.

## Reciprocal Rank Fusion

Reciprocal Rank Fusion (RRF) is the specific algorithm most hybrid retrievers
use to merge two ranked lists. Each candidate document is scored by the sum of
`1 / (k + rank)` across every ranking it appears in, where `k` is a small
constant (commonly 60). Because RRF combines documents by their *rank position*
rather than their raw scores, it fuses the dense-vector ranking and the lexical
BM25 ranking cleanly even though those two scores live on incomparable scales.
Reciprocal Rank Fusion is a distinct technique from the hybrid-retrieval pattern
that employs it: hybrid retrieval is the architecture, RRF is the merge step.

## Limitations

RAG does not turn a language model into a search engine. It reduces
hallucination only when the retrieved passages actually contain the answer;
when they do not, the model still produces confident-sounding text. Careful
attention to chunking, retrieval quality, and citation formats is essential.
The best-known open critique of naive RAG is Douwe Kiela's talk "You cannot
build RAG on top of `text-embedding-ada-002` alone."

## Notable people and organisations

- **Meta AI Research** published the original RAG paper.
- **Nomic AI** maintains the popular `nomic-embed-text` open-weight embedding
  model.
- **Anthropic** publishes Claude, whose long context windows make it well-suited
  as the generator in a RAG pipeline.
