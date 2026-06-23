"""Semantic embeddings for hybrid search.

Embeds each current program's identity text (asset name + synonyms + target + mechanism +
modality + indication + therapeutic area) into `program_embedding` (pgvector, 1024-dim) so the
search layer can do fuzzy/synonym matching ("KRAS" ~ "K-Ras" ~ "KRAS G12C inhibitor") with a fast,
DETERMINISTIC vector lookup — no LLM in the query path.

Local by default (fastembed / ONNX, BAAI/bge-large-en-v1.5) so the service is self-contained,
free per query, and reproducible. `text_hash` makes (re-)embedding idempotent: only programs whose
identity text changed are re-embedded.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import text as sql
from sqlalchemy.orm import Session

from pipeline_intel.config import settings

EMBED_MODEL = "BAAI/bge-large-en-v1.5"  # 1024-dim — matches program_embedding.embedding Vector(1024)
EMBED_DIM = 1024
_BATCH = 256

# One row per CURRENT program with the text fields that identify it. Aggregates target verbatim +
# HGNC and asset synonyms so a query hits any disclosed or normalized name.
_CORPUS_SQL = """
select p.program_id,
       a.preferred_name,
       coalesce(a.mechanism_verbatim,'')                                  as mechanism,
       coalesce(a.modality_verbatim,'')                                   as modality,
       i.preferred_label                                                  as indication,
       coalesce(pv.indication_verbatim,'')                                as indication_verbatim,
       coalesce(im.therapeutic_area,'')                                   as therapeutic_area,
       coalesce((select string_agg(distinct coalesce(t.hgnc_symbol, at.verbatim), ', ')
                 from asset_target at left join target t on t.target_id=at.target_id
                 where at.asset_id=a.asset_id), '')                       as targets,
       coalesce((select string_agg(distinct syn.synonym, ', ')
                 from asset_synonym syn where syn.asset_id=a.asset_id), '') as synonyms
from program p
join program_version pv on pv.program_id=p.program_id and pv.valid_to is null
join asset a on a.asset_id=p.asset_id
join indication i on i.indication_id=p.indication_id
left join indication_mapping im
       on im.indication_id=p.indication_id and im.status in ('auto','reviewed')
"""


@dataclass
class EmbedStats:
    total: int = 0
    embedded: int = 0
    unchanged: int = 0

    def as_dict(self) -> dict:
        return {"total": self.total, "embedded": self.embedded, "unchanged": self.unchanged}


def _row_text(r) -> str:
    """Identity text for a program. Stable field order so the hash is deterministic."""
    parts = [r.preferred_name, r.synonyms, r.targets, r.mechanism, r.modality,
             r.indication, r.indication_verbatim, r.therapeutic_area]
    return " | ".join(p.strip() for p in parts if p and p.strip())


def _text_hash(t: str) -> str:
    return hashlib.sha256(f"{EMBED_MODEL}\n{t}".encode()).hexdigest()


_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding  # noqa: PLC0415 — heavy import, defer

        _embedder = TextEmbedding(model_name=settings().embed_model or EMBED_MODEL)
    return _embedder


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed documents. fastembed returns numpy arrays; we hand back plain lists for pgvector."""
    return [v.tolist() for v in _get_embedder().embed(texts)]


def embed_query(q: str) -> list[float]:
    """Embed a single query string (bge wants a short instruction prefix for retrieval queries)."""
    prefixed = f"Represent this sentence for searching relevant passages: {q}"
    return next(iter(_get_embedder().query_embed(prefixed))).tolist()


def ensure_vector_index(s: Session) -> None:
    """HNSW index for fast cosine ANN over the embeddings. Idempotent."""
    s.execute(sql(
        "create index if not exists ix_program_embedding_hnsw "
        "on program_embedding using hnsw (embedding vector_cosine_ops)"))
    s.commit()


def embed_all(s: Session, force: bool = False) -> EmbedStats:
    """Embed every current program whose identity text changed (or all, if force). Upserts into
    program_embedding; commits in batches so it is resumable."""
    rows = list(s.execute(sql(_CORPUS_SQL)))
    stats = EmbedStats(total=len(rows))

    existing: dict[str, str] = {}
    if not force:
        existing = {pid: h for pid, h in s.execute(
            sql("select program_id, text_hash from program_embedding"))}

    pending: list[tuple[str, str, str]] = []  # (program_id, text, hash)
    for r in rows:
        t = _row_text(r)
        h = _text_hash(t)
        if not force and existing.get(r.program_id) == h:
            stats.unchanged += 1
            continue
        pending.append((r.program_id, t, h))

    for i in range(0, len(pending), _BATCH):
        chunk = pending[i:i + _BATCH]
        vecs = embed_texts([c[1] for c in chunk])
        for (pid, _t, h), vec in zip(chunk, vecs, strict=True):
            s.execute(
                sql("""insert into program_embedding (program_id, embedding, text_hash, model)
                       values (:pid, (:emb)::vector, :h, :m)
                       on conflict (program_id)
                       do update set embedding=(:emb)::vector, text_hash=:h, model=:m"""),
                {"pid": pid, "emb": str(vec), "h": h, "m": EMBED_MODEL},
            )
            stats.embedded += 1
        s.commit()
    return stats
