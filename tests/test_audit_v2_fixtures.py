import hashlib
import json
from pathlib import Path


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "audit_v2"


def test_synthetic_fixture_manifest_is_complete_and_reproducible():
    manifest = json.loads(
        (FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["schema_version"] == 1
    assert {item["sample_id"] for item in manifest["samples"]} == {
        "synthetic-fragmented-797",
        "synthetic-oversized-clause",
        "synthetic-tail-tax-risk",
    }
    for sample in manifest["samples"]:
        payload = (FIXTURE_ROOT / sample["path"]).read_bytes()
        assert len(payload) == sample["bytes"]
        assert hashlib.sha256(payload).hexdigest() == sample["sha256"]
        assert sample["authorization_ref"] == "synthetic-generated"


def test_fragmented_fixture_has_exactly_797_nonempty_fragments():
    fragments = [
        line
        for line in (
            FIXTURE_ROOT / "fragmented_797.txt"
        ).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert len(fragments) == 797
    assert fragments[0].startswith("第1项")
    assert fragments[-1].startswith("第797项")


def test_tail_risk_fixture_keeps_risk_at_the_final_fragment():
    manifest = json.loads(
        (FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8")
    )
    sample = next(
        item
        for item in manifest["samples"]
        if item["sample_id"] == "synthetic-tail-tax-risk"
    )
    lines = [
        line
        for line in (FIXTURE_ROOT / sample["path"])
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    assert len(lines) == sample["expected_source_spans"]
    assert sample["expected_risks"][0]["marker"] in lines[-1]
