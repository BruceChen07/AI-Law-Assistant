"""L1 压测语料生成器。

从 data/laws 下的真实税法法规 docx 抽取正文，拼装成 4 种输入规模的审计式提示，
用于 benchmark-ollama-vs-llamacpp.py 的 L1 基础性能压测。

- 短提示  ~200   tokens：实体抽取 / 税务匹配单条
- 中提示  ~2,000 tokens：multipass 单块条款审计
- 长提示  ~8,000 tokens：classic 单轮全合同审计
- 超长    ~14,000 tokens：触及 16K 预算上限的大合同

token 估算沿用 benchmark 脚本的启发式（cjk*1.1 + 其余/3.8），
实际 token 数以 Ollama prompt_eval_count 为准，压测报告中回填。
"""
import glob
import os
import sys

from docx import Document

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
LAWS_DIR = os.path.join(REPO, "data", "laws")

INSTRUCTION = (
    "你是税务合同审计助手。请阅读以下税法法规与合同条款材料，"
    "识别其中的涉税风险点，指出涉及的法条，并按 JSON 数组输出，"
    "每项包含 risk_title、clause、law_title、article_no、severity 字段。\n\n材料：\n"
)

# 目标场景 -> 目标字符数（中文场景 token≈字符*1.1）
SCENARIOS = {
    "short_200": 180,
    "medium_2000": 1800,
    "long_8000": 7300,
    "xlong_14000": 12800,
}


def _estimate_tokens(text: str) -> int:
    s = (text or "").strip()
    if not s:
        return 0
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    non_cjk = len(s) - cjk
    return max(1, int(cjk * 1.1 + non_cjk / 3.8))


def _collect_corpus() -> str:
    paras: list[str] = []
    for path in sorted(glob.glob(os.path.join(LAWS_DIR, "*.docx"))):
        try:
            doc = Document(path)
        except Exception:
            continue
        for p in doc.paragraphs:
            t = (p.text or "").strip()
            if len(t) >= 8:
                paras.append(t)
    return "\n".join(paras)


def main() -> int:
    corpus = _collect_corpus()
    if not corpus:
        print("[ERROR] no corpus extracted from law docx files")
        return 1
    print(f"[INFO] corpus chars={len(corpus)} est_tokens={_estimate_tokens(corpus)}")

    for name, target_chars in SCENARIOS.items():
        body = corpus
        # 语料不足则重复拼接以达到目标长度
        while len(body) < target_chars:
            body = body + "\n" + corpus
        body = body[:target_chars]
        prompt = INSTRUCTION + body
        out_path = os.path.join(HERE, f"prompt_{name}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(prompt)
        print(f"[OK] {name}: chars={len(prompt)} est_tokens={_estimate_tokens(prompt)} -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
