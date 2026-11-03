from app.api.routers.contracts import _build_audit_error_payload


def test_build_audit_error_payload_classifies_pdf_digitization_failure():
    payload = _build_audit_error_payload(
        RuntimeError("digitize_content_list_missing"),
        "zh",
    )
    assert payload["code"] == "AUDIT_PDF_DIGITIZATION_FAILED"
    assert payload["category"] == "pdf_digitization"
    assert payload["title"] == "PDF 数字化失败"
    assert payload["operator_scenarios"]


def test_build_audit_error_payload_classifies_timeout_failure():
    payload = _build_audit_error_payload(
        RuntimeError("httpx.ReadTimeout: timed out"),
        "en",
    )
    assert payload["code"] == "AUDIT_LLM_TIMEOUT"
    assert payload["category"] == "llm_timeout"
    assert payload["title"] == "Model request timed out"
    assert payload["retryable"] is True


def test_build_audit_error_payload_classifies_llm_connection_failure():
    payload = _build_audit_error_payload(
        RuntimeError(
            "llm request failed: httpx.ConnectError: [WinError 10061] 由于目标计算机积极拒绝，无法连接。"),
        "zh",
    )
    assert payload["code"] == "AUDIT_LLM_CONNECTION_FAILED"
    assert payload["category"] == "llm_connection"
    assert payload["title"] == "模型服务连接失败"


def test_build_audit_error_payload_classifies_storage_permission_failure():
    payload = _build_audit_error_payload(
        PermissionError(
            "[WinError 5] 拒绝访问。: 'D:\\Workspace\\data\\memory\\debug\\rag_retrieval\\2026-10-15'"),
        "zh",
    )
    assert payload["code"] == "AUDIT_STORAGE_PERMISSION_DENIED"
    assert payload["category"] == "storage_permission"
