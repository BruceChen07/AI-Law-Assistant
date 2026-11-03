from app.core.config import _normalize_paths
from app.services.audit_retrieval import _write_rag_trace


def test_normalize_paths_includes_rag_and_llm_trace_dirs():
    cfg = _normalize_paths(
        {
            "data_dir": "../data",
            "llm_trace_dir": "../data/memory/llm_interactions",
            "rag_trace_dir": "../data/memory/debug/rag_retrieval",
            "contract_audit_debug_archive_dir": "../data/archive/debug",
        },
        r"D:\Workspace\AI-Law-Assistant\app",
    )

    assert cfg["data_dir"] == r"D:\Workspace\AI-Law-Assistant\data"
    assert cfg["llm_trace_dir"] == r"D:\Workspace\AI-Law-Assistant\data\memory\llm_interactions"
    assert cfg["rag_trace_dir"] == r"D:\Workspace\AI-Law-Assistant\data\memory\debug\rag_retrieval"
    assert cfg["contract_audit_debug_archive_dir"] == r"D:\Workspace\AI-Law-Assistant\data\archive\debug"


def test_write_rag_trace_does_not_raise_when_directory_is_not_writable(monkeypatch):
    monkeypatch.setattr("app.services.audit_retrieval._cleanup_rag_debug_tree", lambda *_args, **_kwargs: None)

    def _raise_permission(*_args, **_kwargs):
        raise PermissionError("[WinError 5] Access is denied")

    monkeypatch.setattr("os.makedirs", _raise_permission)

    _write_rag_trace(
        {"rag_trace_enabled": True, "rag_trace_dir": r"D:\blocked\rag_trace"},
        "trace-1",
        "rag_retrieval_start",
        {"foo": "bar"},
    )
