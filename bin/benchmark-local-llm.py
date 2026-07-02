"""Quick benchmark of local Ollama models for enterprise deployment assessment."""
import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:11434/api/chat"


def ollama_chat(model, prompt, max_tokens=256):
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"num_predict": max_tokens, "temperature": 0.0},
            "stream": False,
        }
    ).encode()
    req = urllib.request.Request(
        BASE, data=body, headers={"Content-Type": "application/json"}
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read())
    elapsed = time.time() - t0
    content = data.get("message", {}).get("content", "")
    eval_count = data.get("eval_count", 0)
    eval_dur_ns = data.get("eval_duration", 1)
    eval_dur_s = eval_dur_ns / 1e9 if eval_dur_ns else 0.001
    tps = eval_count / eval_dur_s if eval_dur_s > 0 else 0
    prompt_eval_count = data.get("prompt_eval_count", 0)
    prompt_eval_dur = data.get("prompt_eval_duration", 1) / 1e9
    ppt = prompt_eval_count / prompt_eval_dur if prompt_eval_dur > 0 else 0
    return {
        "elapsed": elapsed,
        "content": content,
        "eval_count": eval_count,
        "tok_per_sec": tps,
        "prompt_tps": ppt,
    }


def main():
    print("=" * 60)
    print("LOCAL OLLAMA MODEL BENCHMARK")
    print("=" * 60)
    print(f"Target: {BASE}")
    print(f"PID:    {os.getpid()}")
    print()

    # ── Test 1: Small model entity extraction ──
    print("── T1: Small Model (llama3.2:3b) - Entity extraction ──")
    prompt_t1 = (
        "Extract entities from this contract text as JSON: "
        "Party A is Beijing Tech Co., Party B is Shanghai Trade Co., amount 1M RMB. "
        'Return JSON: {"parties": [], "amount": "", "type": ""}'
    )
    try:
        r = ollama_chat("llama3.2:3b", prompt_t1, 100)
        print(f"  Latency:      {r['elapsed']:.1f}s")
        print(f"  Tokens:       {r['eval_count']} generated")
        print(f"  Tok/sec:      {r['tok_per_sec']:.1f}")
        print(f"  Prompt t/s:   {r['prompt_tps']:.1f}")
        print(f"  Output:       {r['content'][:120]}")
    except Exception as e:
        print(f"  FAIL: {e}")

    # ── Test 2: Small model tax matching ──
    print()
    print("── T2: Small Model (llama3.2:3b) - Tax clause match ──")
    prompt_t2 = (
        "Determine if this clause relates to value-added tax (VAT): "
        "Taxpayers selling goods or providing taxable services shall have "
        "tax payable as output tax minus input tax for the period. "
        'Output JSON: {"is_match": true/false, "confidence": 0.0-1.0, "reason": ""}'
    )
    try:
        r = ollama_chat("llama3.2:3b", prompt_t2, 80)
        print(f"  Latency:      {r['elapsed']:.1f}s")
        print(f"  Tokens:       {r['eval_count']} generated")
        print(f"  Tok/sec:      {r['tok_per_sec']:.1f}")
        print(f"  Output:       {r['content'][:120]}")
    except Exception as e:
        print(f"  FAIL: {e}")

    # ── Test 3: Main model simple ──
    print()
    print("── T3: Main Model (qwen3.6:27b) - Simple ──")
    try:
        r = ollama_chat("qwen3.6:27b", "Hello, respond with just the word OK.", 20)
        print(f"  Latency:      {r['elapsed']:.1f}s")
        print(f"  Tokens:       {r['eval_count']} generated")
        print(f"  Tok/sec:      {r['tok_per_sec']:.1f}")
        print(f"  Prompt t/s:   {r['prompt_tps']:.1f}")
        print(f"  Output:       {r['content'][:80]}")
    except Exception as e:
        print(f"  FAIL: {e}")

    # ── Test 4: Main model contract audit ──
    print()
    print("── T4: Main Model (qwen3.6:27b) - Contract clause audit ──")
    prompt_t4 = (
        "You are a contract audit expert. Audit this clause and return JSON only. "
        "Clause: Party B shall pay the first installment within 30 days of signing. "
        'Output JSON: {"risk_level": "low/medium/high", "issues": [], "suggestions": []}'
    )
    try:
        r = ollama_chat("qwen3.6:27b", prompt_t4, 200)
        print(f"  Latency:      {r['elapsed']:.1f}s")
        print(f"  Tokens:       {r['eval_count']} generated")
        print(f"  Tok/sec:      {r['tok_per_sec']:.1f}")
        print(f"  Prompt t/s:   {r['prompt_tps']:.1f}")
        content = r["content"].strip()
        is_json = content.startswith("{") and content.endswith("}")
        print(f"  JSON wrapper: {is_json}")
        print(f"  Output:       {content[:200]}")
    except Exception as e:
        print(f"  FAIL: {e}")

    # ── Test 5: Main model long context ──
    print()
    print("── T5: Main Model (qwen3.6:27b) - Long prompt (2K chars) ──")
    filler = "Clause {}: The parties agree to standard commercial terms. "
    long_clauses = "".join(filler.format(i) for i in range(1, 40))
    prompt_t5 = (
        f"Audit the following contract clauses and return a JSON summary. "
        f"Contract text: {long_clauses}"
        'Output JSON: {"total_clauses": 0, "risk_summary": "...", "top_risks": []}'
    )
    try:
        r = ollama_chat("qwen3.6:27b", prompt_t5, 200)
        print(f"  Latency:      {r['elapsed']:.1f}s")
        print(f"  Tokens:       {r['eval_count']} generated")
        print(f"  Tok/sec:      {r['tok_per_sec']:.1f}")
        print(f"  Prompt t/s:   {r['prompt_tps']:.1f}")
        print(f"  Prompt len:   ~{len(prompt_t5)} chars")
        print(f"  Output:       {r['content'][:150]}")
    except Exception as e:
        print(f"  FAIL: {e}")

    # ── Summary ──
    print()
    print("=" * 60)
    print("BENCHMARK COMPLETE")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
