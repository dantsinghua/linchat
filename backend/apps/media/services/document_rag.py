import logging
import re

from django.conf import settings

logger = logging.getLogger(__name__)


def chunk_document(
    content: str,
    chunk_size: int = 800,
    overlap: int = 100,
) -> list[str]:
    if not content or not content.strip():
        return []

    sections = re.split(r"\n(?=#{1,6} )", content)
    sections = [s.strip() for s in sections if s.strip()]

    paragraphs: list[str] = []
    for section in sections:
        parts = section.split("\n\n")
        for p in parts:
            p = p.strip()
            if p:
                paragraphs.append(p)

    if not paragraphs:
        return []

    merged: list[str] = []
    buf = ""
    for p in paragraphs:
        if buf and len(buf) + len(p) + 2 > chunk_size:
            merged.append(buf)
            buf = p
        else:
            buf = f"{buf}\n\n{p}" if buf else p
    if buf:
        merged.append(buf)

    chunks: list[str] = []
    for segment in merged:
        if len(segment) <= chunk_size:
            chunks.append(segment)
        else:
            start = 0
            while start < len(segment):
                end = start + chunk_size
                chunks.append(segment[start:end])
                start = end - overlap
                if start < 0:
                    break

    return chunks


async def search_documents_rag(
    user_id: int,
    query: str,
    mode: str = "hybrid",
    limit: int = 5,
) -> list[dict]:
    from apps.media.repositories import doc_chunk_repo, media_attachment_repo

    vector_results: list[tuple] = []
    keyword_results: list[tuple] = []

    if mode in ("keyword", "hybrid"):
        try:
            keyword_results = await doc_chunk_repo.keyword_search(user_id, query, limit=limit * 3)
        except Exception as e:
            logger.warning("Doc RAG keyword search failed: user=%d, err=%s", user_id, e)

    if mode in ("semantic", "hybrid"):
        try:
            from apps.memory.services import EmbeddingClient

            query_embedding = await EmbeddingClient.generate_embedding(query)
            vector_results = await doc_chunk_repo.vector_search(user_id, query_embedding, limit=limit * 3)
        except Exception as e:
            logger.warning("Doc RAG vector search failed, degrading to keyword: user=%d, err=%s", user_id, e)
            if mode == "semantic":
                try:
                    keyword_results = await doc_chunk_repo.keyword_search(user_id, query, limit=limit * 3)
                except Exception as e2:
                    logger.warning("Doc RAG keyword fallback also failed: user=%d, err=%s", user_id, e2)

    vector_weight = getattr(settings, "DOC_VECTOR_WEIGHT", 0.7)
    keyword_weight = getattr(settings, "DOC_KEYWORD_WEIGHT", 0.3)

    if mode == "hybrid" and (vector_results or keyword_results):
        score_map: dict[tuple, dict] = {}

        for att_id, chunk_idx, chunk_text, score in vector_results:
            key = (att_id, chunk_idx)
            if key not in score_map:
                score_map[key] = {"text": chunk_text, "vector": score, "keyword": 0.0, "attachment_id": att_id}
            else:
                score_map[key]["vector"] = max(score_map[key].get("vector", 0.0), score)

        for att_id, chunk_idx, chunk_text, score in keyword_results:
            key = (att_id, chunk_idx)
            if key not in score_map:
                score_map[key] = {"text": chunk_text, "vector": 0.0, "keyword": score, "attachment_id": att_id}
            else:
                score_map[key]["keyword"] = max(score_map[key].get("keyword", 0.0), score)

        ranked = sorted(
            score_map.values(),
            key=lambda x: x.get("vector", 0.0) * vector_weight + x.get("keyword", 0.0) * keyword_weight,
            reverse=True,
        )[:limit]
    elif vector_results:
        ranked = [{"text": t, "attachment_id": a, "vector": s, "keyword": 0.0} for a, _, t, s in vector_results[:limit]]
    elif keyword_results:
        ranked = [{"text": t, "attachment_id": a, "vector": 0.0, "keyword": s} for a, _, t, s in keyword_results[:limit]]
    else:
        ranked = []

    if not ranked:
        try:
            ft_results = await media_attachment_repo.fulltext_search_parsed_content(user_id, query, limit=limit)
            if ft_results:
                for att_id, att_uuid, file_name, score in ft_results:
                    ranked.append({"text": f"[全文匹配] {file_name}", "attachment_id": att_id, "vector": 0.0, "keyword": score, "match_type": "fulltext"})
        except Exception as e:
            logger.warning("Doc RAG fulltext fallback failed: user=%d, err=%s", user_id, e)

    if not ranked:
        return []

    att_ids = list({r["attachment_id"] for r in ranked})
    from apps.media.models import MediaAttachment

    att_map: dict[int, MediaAttachment] = {}
    try:
        from asgiref.sync import sync_to_async

        @sync_to_async
        def _load_atts():
            return {a.attachment_id: a for a in MediaAttachment.objects.filter(attachment_id__in=att_ids)}

        att_map = await _load_atts()
    except Exception:
        pass

    results = []
    for r in ranked:
        att = att_map.get(r["attachment_id"])
        combined = r.get("vector", 0.0) * vector_weight + r.get("keyword", 0.0) * keyword_weight
        results.append({
            "file_name": att.file_name if att else "未知文档",
            "attachment_uuid": att.attachment_uuid if att else "",
            "created_at": att.created_at.isoformat() if att and att.created_at else "",
            "chunk_text": r["text"],
            "score": round(combined, 4),
            "match_type": r.get("match_type", mode),
        })

    return results
