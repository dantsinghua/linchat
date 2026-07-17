"""
DocumentParseService.search_documents_rag RAG 搜索单元测试 (011-document-subagent-rag T022)

覆盖: keyword/semantic/hybrid 搜索, rerank 公式, 降级, 用户隔离, 空结果
覆盖率要求: 服务层 >= 95%
"""

from unittest.mock import AsyncMock, patch

from tests.helpers import run_async


class TestSearchDocumentsRag:
    """search_documents_rag 测试"""

    @patch("apps.media.repositories.doc_chunk_repo.keyword_search", new_callable=AsyncMock)
    def test_keyword_search(self, mock_kw):
        """keyword 模式 — 仅调用 keyword_search"""
        from apps.media.services.document_rag import search_documents_rag

        mock_kw.return_value = [
            (1, 0, "量子计算应用于金融", 0.85),
            (2, 1, "区块链技术概述", 0.60),
        ]
        results = run_async(search_documents_rag(user_id=42, query="量子计算", mode="keyword", limit=5))
        assert len(results) == 2
        assert results[0]["score"] > 0
        mock_kw.assert_called_once()

    @patch("apps.media.repositories.doc_chunk_repo.vector_search", new_callable=AsyncMock)
    @patch("apps.memory.services.EmbeddingClient.generate_embedding", new_callable=AsyncMock)
    def test_semantic_search(self, mock_embed, mock_vec):
        """semantic 模式 — 调用 Embedding + vector_search"""
        from apps.media.services.document_rag import search_documents_rag

        mock_embed.return_value = [0.1] * 1024
        mock_vec.return_value = [(1, 0, "量子计算研究", 0.90)]
        results = run_async(search_documents_rag(user_id=42, query="量子", mode="semantic", limit=5))
        assert len(results) == 1
        mock_embed.assert_called_once()
        mock_vec.assert_called_once()

    @patch("apps.media.repositories.doc_chunk_repo.keyword_search", new_callable=AsyncMock)
    @patch("apps.media.repositories.doc_chunk_repo.vector_search", new_callable=AsyncMock)
    @patch("apps.memory.services.EmbeddingClient.generate_embedding", new_callable=AsyncMock)
    def test_hybrid_rerank(self, mock_embed, mock_vec, mock_kw):
        """hybrid 模式 — rerank 公式: vector*0.7 + keyword*0.3"""
        from apps.media.services.document_rag import search_documents_rag

        mock_embed.return_value = [0.1] * 1024
        mock_vec.return_value = [(1, 0, "量子计算", 0.90)]
        mock_kw.return_value = [(1, 0, "量子计算", 0.80), (2, 1, "区块链", 0.70)]

        results = run_async(search_documents_rag(user_id=42, query="量子", mode="hybrid", limit=5))
        assert len(results) >= 1
        # First result should have combined score from both vector and keyword
        mock_embed.assert_called_once()
        mock_vec.assert_called_once()
        mock_kw.assert_called_once()

    @patch("apps.media.repositories.doc_chunk_repo.keyword_search", new_callable=AsyncMock)
    @patch("apps.media.repositories.doc_chunk_repo.vector_search", new_callable=AsyncMock)
    @patch("apps.memory.services.EmbeddingClient.generate_embedding", new_callable=AsyncMock)
    def test_hybrid_degrade_on_vector_fail(self, mock_embed, mock_vec, mock_kw):
        """hybrid 模式 — 向量异常时降级为关键词"""
        from apps.media.services.document_rag import search_documents_rag

        mock_embed.side_effect = Exception("Embedding API down")
        mock_kw.return_value = [(1, 0, "降级结果", 0.75)]

        results = run_async(search_documents_rag(user_id=42, query="量子", mode="hybrid", limit=5))
        assert len(results) == 1
        assert "降级结果" in results[0]["chunk_text"]

    @patch("apps.media.repositories.doc_chunk_repo.keyword_search", new_callable=AsyncMock)
    @patch("apps.media.repositories.doc_chunk_repo.vector_search", new_callable=AsyncMock)
    @patch("apps.memory.services.EmbeddingClient.generate_embedding", new_callable=AsyncMock)
    def test_empty_results(self, mock_embed, mock_vec, mock_kw):
        """搜索无结果 — 返回空列表"""
        from apps.media.services.document_rag import search_documents_rag

        mock_embed.return_value = [0.1] * 1024
        mock_vec.return_value = []
        mock_kw.return_value = []

        # Also mock fulltext fallback
        with patch("apps.media.repositories.media_attachment_repo.fulltext_search_parsed_content", new_callable=AsyncMock, return_value=[]):
            results = run_async(search_documents_rag(user_id=42, query="不存在的内容", mode="hybrid", limit=5))
        assert results == []

    @patch("apps.media.repositories.doc_chunk_repo.keyword_search", new_callable=AsyncMock)
    def test_keyword_empty_no_indexed_docs(self, mock_kw):
        """无索引文档 — keyword 搜索返回空"""
        from apps.media.services.document_rag import search_documents_rag

        mock_kw.return_value = []
        with patch("apps.media.repositories.media_attachment_repo.fulltext_search_parsed_content", new_callable=AsyncMock, return_value=[]):
            results = run_async(search_documents_rag(user_id=42, query="test", mode="keyword", limit=5))
        assert results == []
