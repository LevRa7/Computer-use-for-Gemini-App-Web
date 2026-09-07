import pytest
import os
from core.schemas import NodeVitals

def test_code_index_builds_and_finds_symbols(tmp_path):
    from core.code_cache import CodeIndex

    cache_file = str(tmp_path / "test_code_index.json")
    indexer = CodeIndex(root_dir="core", cache_file=cache_file)
    stats = indexer.build_or_update()

    assert stats["total_files"] >= 6
    assert stats["parsed_files"] >= 6

    # Find specific known symbol in schemas.py
    symbols = indexer.find_symbol("NodeVitals")
    assert len(symbols) > 0
    node_sym = symbols[0]
    assert node_sym.name == "NodeVitals"
    assert node_sym.kind == "class"
    assert "core/schemas.py" in node_sym.file_path
    assert node_sym.line_start > 0

    # Find hook function in hooks.py
    hook_syms = indexer.find_symbol("mesh_pre_tool_call")
    assert len(hook_syms) > 0
    assert hook_syms[0].name == "mesh_pre_tool_call"
    assert hook_syms[0].kind in ("function", "async_function")

def test_code_index_incremental_caching(tmp_path):
    from core.code_cache import CodeIndex

    cache_file = str(tmp_path / "test_incremental_index.json")
    indexer = CodeIndex(root_dir="core", cache_file=cache_file)
    stats1 = indexer.build_or_update()
    assert stats1["parsed_files"] >= 6

    # Second run immediately without modifications should parse 0 files (100% cache hit)
    indexer2 = CodeIndex(root_dir="core", cache_file=cache_file)
    stats2 = indexer2.build_or_update()
    assert stats2["parsed_files"] == 0
    assert stats2["cache_hits"] >= 6

def test_code_index_file_outline_and_chunk(tmp_path):
    from core.code_cache import CodeIndex

    cache_file = str(tmp_path / "test_outline_index.json")
    indexer = CodeIndex(root_dir="core", cache_file=cache_file)
    indexer.build_or_update()

    outline = indexer.get_file_outline("core/schemas.py")
    names = [s.name for s in outline]
    assert "NodeVitals" in names
    assert "ToolExecutionResult" in names

    # Chunk reading
    chunk = indexer.get_cached_chunk("core/schemas.py", 1, 10)
    assert "class NodeVitals" in chunk
