"""
DocumentParseService.chunk_document 分块算法单元测试 (011-document-subagent-rag T021)

覆盖: Markdown 标题分割, 段落拆分, 小块合并, 长块切分, 边界情况
"""

from apps.media.services.document_rag import chunk_document


class TestChunkDocument:
    """chunk_document 分块算法测试"""

    def test_empty_content(self):
        assert chunk_document("") == []

    def test_whitespace_only(self):
        assert chunk_document("   \n\n  ") == []

    def test_single_short_paragraph(self):
        result = chunk_document("Hello world.")
        assert result == ["Hello world."]

    def test_markdown_heading_split(self):
        """按 Markdown 标题拆分"""
        content = "# Section 1\n" + "A" * 100 + "\n\n# Section 2\n" + "B" * 100
        result = chunk_document(content, chunk_size=50)
        assert len(result) >= 2
        full = " ".join(result)
        assert "Section 1" in full
        assert "Section 2" in full

    def test_multi_level_headings(self):
        """多级标题均能分割"""
        content = "# H1\n" + "A" * 80 + "\n\n## H2\n" + "B" * 80 + "\n\n### H3\n" + "C" * 80
        result = chunk_document(content, chunk_size=50)
        assert len(result) >= 2

    def test_paragraph_split(self):
        """段落间双换行符拆分"""
        content = "Para 1 content.\n\nPara 2 content.\n\nPara 3 content."
        result = chunk_document(content, chunk_size=200)
        assert len(result) >= 1
        # All paragraphs should be present
        full = " ".join(result)
        assert "Para 1" in full
        assert "Para 3" in full

    def test_small_chunks_merged(self):
        """小段落应合并到 chunk_size 以下"""
        content = "A\n\nB\n\nC\n\nD"
        result = chunk_document(content, chunk_size=100)
        # 4 tiny paragraphs should merge into fewer chunks
        assert len(result) < 4

    def test_long_chunk_cut(self):
        """超过 chunk_size 的段落应被切分"""
        content = "A" * 2000
        result = chunk_document(content, chunk_size=800, overlap=100)
        assert len(result) >= 3
        # Each chunk should not exceed chunk_size
        for chunk in result:
            assert len(chunk) <= 800

    def test_overlap_between_long_chunks(self):
        """长段切分时有重叠"""
        content = "A" * 1600
        result = chunk_document(content, chunk_size=800, overlap=100)
        assert len(result) >= 2
        # Check overlap: end of chunk[0] should overlap with start of chunk[1]
        if len(result) >= 2:
            overlap_region = result[0][-100:]
            assert overlap_region in result[1]

    def test_no_headings_paragraph_split(self):
        """无标题内容降级为段落拆分"""
        content = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        result = chunk_document(content, chunk_size=200)
        assert len(result) >= 1
        full = " ".join(result)
        assert "First paragraph" in full

    def test_real_markdown_document(self):
        """模拟真实 Markdown 文档"""
        content = """# 引言

本文探讨量子计算在金融领域的应用前景。量子计算作为新兴技术，近年来受到广泛关注。

## 背景

量子计算是一种利用量子力学原理进行信息处理的计算范式。与经典计算不同，量子计算利用叠加态和纠缠实现并行计算。

## 应用场景

### 投资组合优化

量子退火算法可用于解决组合优化问题，在投资组合优化领域展现出巨大潜力。

### 风险分析

蒙特卡洛模拟可借助量子加速，大幅提升风险评估的计算效率。

## 结论

量子计算在金融领域具有广阔的应用前景，但仍面临技术成熟度和人才储备等方面的挑战。"""
        result = chunk_document(content, chunk_size=100, overlap=20)
        assert len(result) >= 2
        full = " ".join(result)
        assert "引言" in full
        assert "结论" in full
