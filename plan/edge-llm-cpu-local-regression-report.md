# Edge LLM CPU Local Regression Report

- Generated At: 2026-06-23T15:57:47
- Machine: LAPTOP-3HH3781H
- Repository: D:\Workspace\AI-Law-Assistant
- Overall Status: passed
- Pytest Exit Code: 0

## Test Scope

```text
tests/test_llm_router.py
tests/test_llm_local_mode.py
tests/test_json_guard.py
tests/test_local_llm_fallback.py
tests/test_tax_contract_parser.py
tests/test_tax_matcher.py
tests/test_tax_risk.py
tests/test_memory_pipeline_fallback.py
tests/test_contract_audit_memory_mode.py
tests/test_contract_audit.py
```

## Pytest Command

```powershell
python -m pytest tests/test_llm_router.py tests/test_llm_local_mode.py tests/test_json_guard.py tests/test_local_llm_fallback.py tests/test_tax_contract_parser.py tests/test_tax_matcher.py tests/test_tax_risk.py tests/test_memory_pipeline_fallback.py tests/test_contract_audit_memory_mode.py tests/test_contract_audit.py
```

## Pytest Output

```text
============================= test session starts =============================
platform win32 -- Python 3.13.5, pytest-8.4.1, pluggy-1.5.0
rootdir: D:\Workspace\AI-Law-Assistant
plugins: anyio-4.7.0, langsmith-0.4.27, cov-7.0.0
collected 53 items

tests\test_llm_router.py ....                                            [  7%]
tests\test_llm_local_mode.py ..                                          [ 11%]
tests\test_json_guard.py ...                                             [ 16%]
tests\test_local_llm_fallback.py ...                                     [ 22%]
tests\test_tax_contract_parser.py ....                                   [ 30%]
tests\test_tax_matcher.py ....                                           [ 37%]
tests\test_tax_risk.py .                                                 [ 39%]
tests\test_memory_pipeline_fallback.py ..                                [ 43%]
tests\test_contract_audit_memory_mode.py .................               [ 75%]
tests\test_contract_audit.py .............                               [100%]

============================== warnings summary ===============================
C:\Users\YOGA\anaconda3\Lib\site-packages\jieba\_compat.py:18
  C:\Users\YOGA\anaconda3\Lib\site-packages\jieba\_compat.py:18: UserWarning: pkg_resources is deprecated as an API. See https://setuptools.pypa.io/en/latest/pkg_resources.html. The pkg_resources package is slated for removal as early as 2025-11-30. Refrain from using this package or pin to Setuptools<81.
    import pkg_resources

C:\Users\YOGA\anaconda3\Lib\site-packages\pkg_resources\__init__.py:3146
  C:\Users\YOGA\anaconda3\Lib\site-packages\pkg_resources\__init__.py:3146: DeprecationWarning: Deprecated call to `pkg_resources.declare_namespace('sphinxcontrib')`.
  Implementing implicit namespace packages (as specified in PEP 420) is preferred to `pkg_resources.declare_namespace`. See https://setuptools.pypa.io/en/latest/references/keywords.html#keyword-namespace-packages
    declare_namespace(pkg)

C:\Users\YOGA\anaconda3\Lib\site-packages\pkg_resources\__init__.py:3146
  C:\Users\YOGA\anaconda3\Lib\site-packages\pkg_resources\__init__.py:3146: DeprecationWarning: Deprecated call to `pkg_resources.declare_namespace('zope')`.
  Implementing implicit namespace packages (as specified in PEP 420) is preferred to `pkg_resources.declare_namespace`. See https://setuptools.pypa.io/en/latest/references/keywords.html#keyword-namespace-packages
    declare_namespace(pkg)

C:\Users\YOGA\anaconda3\Lib\site-packages\pypdf\_crypt_providers\_cryptography.py:32
  C:\Users\YOGA\anaconda3\Lib\site-packages\pypdf\_crypt_providers\_cryptography.py:32: CryptographyDeprecationWarning: ARC4 has been moved to cryptography.hazmat.decrepit.ciphers.algorithms.ARC4 and will be removed from cryptography.hazmat.primitives.ciphers.algorithms in 48.0.0.
    from cryptography.hazmat.primitives.ciphers.algorithms import AES, ARC4

tests/test_tax_risk.py: 1 warning
tests/test_contract_audit_memory_mode.py: 14 warnings
  D:\Workspace\AI-Law-Assistant\app\memory_system\experience_repo.py:15: DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version. Use timezone-aware objects to represent datetimes in UTC: datetime.datetime.now(datetime.UTC).
    return datetime.utcnow().isoformat()

tests/test_memory_pipeline_fallback.py: 2 warnings
tests/test_contract_audit_memory_mode.py: 43 warnings
  D:\Workspace\AI-Law-Assistant\app\services\contract_audit_modules\memory_pipeline\cleanup.py:39: DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version. Use timezone-aware objects to represent datetimes in UTC: datetime.datetime.now(datetime.UTC).
    now = datetime.utcnow()

tests/test_memory_pipeline_fallback.py: 4 warnings
tests/test_contract_audit_memory_mode.py: 104 warnings
  D:\Workspace\AI-Law-Assistant\app\services\contract_audit_modules\trace_writer.py:128: DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version. Use timezone-aware objects to represent datetimes in UTC: datetime.datetime.now(datetime.UTC).
    day = datetime.utcnow().strftime("%Y-%m-%d")

tests/test_memory_pipeline_fallback.py: 4 warnings
tests/test_contract_audit_memory_mode.py: 104 warnings
  D:\Workspace\AI-Law-Assistant\app\services\contract_audit_modules\trace_writer.py:131: DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version. Use timezone-aware objects to represent datetimes in UTC: datetime.datetime.now(datetime.UTC).
    row = {"ts": datetime.utcnow().isoformat(), "event": str(

tests/test_contract_audit_memory_mode.py: 46 warnings
  D:\Workspace\AI-Law-Assistant\app\memory_system\rerank.py:109: DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version. Use timezone-aware objects to represent datetimes in UTC: datetime.datetime.now(datetime.UTC).
    now = datetime.utcnow()

tests/test_contract_audit_memory_mode.py: 14 warnings
  D:\Workspace\AI-Law-Assistant\app\memory_system\manager.py:108: DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version. Use timezone-aware objects to represent datetimes in UTC: datetime.datetime.now(datetime.UTC).
    now = datetime.utcnow()

tests/test_contract_audit_memory_mode.py: 14 warnings
  D:\Workspace\AI-Law-Assistant\app\memory_system\manager.py:304: DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version. Use timezone-aware objects to represent datetimes in UTC: datetime.datetime.now(datetime.UTC).
    now = datetime.utcnow()

tests/test_contract_audit_memory_mode.py: 32 warnings
  D:\Workspace\AI-Law-Assistant\app\services\contract_audit_modules\trace_writer.py:103: DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version. Use timezone-aware objects to represent datetimes in UTC: datetime.datetime.now(datetime.UTC).
    day = datetime.utcnow().strftime("%Y-%m-%d")

tests/test_contract_audit_memory_mode.py: 32 warnings
  D:\Workspace\AI-Law-Assistant\app\services\contract_audit_modules\trace_writer.py:106: DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version. Use timezone-aware objects to represent datetimes in UTC: datetime.datetime.now(datetime.UTC).
    row = _clip_payload({"ts": datetime.utcnow().isoformat(), "round": rn, "action": str(

tests/test_contract_audit_memory_mode.py: 16 warnings
  D:\Workspace\AI-Law-Assistant\app\memory_system\manager.py:274: DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version. Use timezone-aware objects to represent datetimes in UTC: datetime.datetime.now(datetime.UTC).
    day = datetime.utcnow().strftime("%Y-%m-%d")

tests/test_contract_audit_memory_mode.py: 16 warnings
  D:\Workspace\AI-Law-Assistant\app\memory_system\manager.py:289: DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version. Use timezone-aware objects to represent datetimes in UTC: datetime.datetime.now(datetime.UTC).
    stamp = datetime.utcnow().isoformat()

tests/test_contract_audit_memory_mode.py: 16 warnings
  D:\Workspace\AI-Law-Assistant\app\memory_system\indexer.py:245: DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version. Use timezone-aware objects to represent datetimes in UTC: datetime.datetime.now(datetime.UTC).
    now = datetime.utcnow().isoformat()

tests/test_contract_audit_memory_mode.py: 14 warnings
  D:\Workspace\AI-Law-Assistant\app\memory_system\manager.py:583: DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version. Use timezone-aware objects to represent datetimes in UTC: datetime.datetime.now(datetime.UTC).
    "generated_at": datetime.utcnow().isoformat(),

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
====================== 53 passed, 480 warnings in 38.13s ======================
```

## Smoke Check

- Include Local Smoke: False
- Local Main API: http://127.0.0.1:11434/v1
- Local Small API: http://127.0.0.1:11434/v1
- Cloud API: (not provided)
- Local Main Result: skipped
- Local Small Result: skipped
- Cloud Result: skipped

## Smoke Notes

- None
