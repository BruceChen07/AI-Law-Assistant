#!/usr/bin/env python3
"""
合同审计CLI入口 (Contract Audit CLI Entry)
==========================================
独立命令行入口，支持通过命令行参数执行完整合同审计链路。

执行链路：
  合同文件上传 → Skill0文本解析分块 → 路由匹配对应Skill组串行执行
  → 各Skill独立RAG检索+工具调用推理（端侧大模型+llama.cpp）
  → Skill5汇总分级交叉校验 → 输出完整审计报告JSON+Markdown

使用示例:
  python cli/contract_audit.py --file contract.pdf --output ./output
  python cli/contract_audit.py --file contract.pdf --temp 0.05 --max-tokens 1024
  python cli/contract_audit.py --file contract.pdf --no-cross-validation

依赖:
  - 现有项目LLMService (llama.cpp/ollama)
  - 现有项目embedding模型
  - skill_config.yaml
"""

import argparse
import json
import os
import sys
import time
import logging
from datetime import datetime
from typing import Optional

# 添加项目根目录到sys.path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("contract_audit_cli")


def _load_app_config(config_path: str = "") -> dict:
    """加载项目配置文件"""
    try:
        from app.core.config import load_config
        return load_config(os.path.dirname(config_path) if config_path else None)
    except Exception as e:
        logger.warning("加载项目配置失败: %s，使用默认配置", str(e))
        return {
            "llm_config": {
                "provider": "llama_cpp",
                "api_base": "http://127.0.0.1:8080/v1",
                "model": "local-model",
                "temperature": 0.1,
                "max_tokens": 2048,
            },
            "data_dir": os.path.join(_PROJECT_ROOT, "data"),
            "db_path": os.path.join(_PROJECT_ROOT, "data", "app.db"),
        }


def _init_llm_service(cfg: dict) -> Optional[object]:
    """初始化LLM服务"""
    try:
        from app.core.llm import LLMService
        llm = LLMService(cfg)
        logger.info("LLM服务初始化成功")
        return llm
    except Exception as e:
        logger.error("LLM服务初始化失败: %s", str(e))
        return None


def _init_embedder(cfg: dict):
    """初始化Embedding模型"""
    try:
        from app.core import embedding as core_embedding
        embed_status = core_embedding.get_registry_status()
        if embed_status.get("ready"):
            logger.info("Embedding模型加载成功")
            return core_embedding
        else:
            logger.warning("Embedding模型未就绪: %s", embed_status)
            return None
    except Exception as e:
        logger.warning("Embedding模型初始化失败: %s", str(e))
        return None


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="合同审计Skill体系 - 基于llama.cpp端侧大模型的离线合同审计引擎",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  %(prog)s --file contract.pdf
  %(prog)s --file contract.pdf --output ./reports --temp 0.05
  %(prog)s --file contract.txt --no-cross-validation --max-tokens 1024
  %(prog)s --file contract.docx --config ./custom_skill_config.yaml
        """,
    )

    parser.add_argument(
        "--file", "-f",
        required=True,
        help="合同文件路径（支持PDF/Word/TXT）",
    )
    parser.add_argument(
        "--config", "-c",
        default="",
        help="skill_config.yaml路径（默认使用模块内置配置）",
    )
    parser.add_argument(
        "--output", "-o",
        default="",
        help="审计报告输出目录（默认: ./output）",
    )
    parser.add_argument(
        "--temp", "-t",
        type=float,
        default=None,
        help="LLM温度参数（0.0-1.0，越低越保守，推荐0.05-0.2）",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="单次推理最大token数（默认2048）",
    )
    parser.add_argument(
        "--context-window",
        type=int,
        default=8192,
        help="上下文窗口大小（默认8192，对应llama.cpp的n_ctx）",
    )
    parser.add_argument(
        "--no-cross-validation",
        action="store_true",
        help="禁用交叉校验",
    )
    parser.add_argument(
        "--no-rag",
        action="store_true",
        help="禁用RAG检索（仅使用Prompt模板审计）",
    )
    parser.add_argument(
        "--rag-only",
        action="store_true",
        help="仅执行RAG检索测试，不进行LLM审计",
    )
    parser.add_argument(
        "--import-rag",
        action="store_true",
        help="导入模式：将知识文件导入到指定Skill RAG库",
    )
    parser.add_argument(
        "--import-skill",
        default="",
        help="导入目标Skill ID（与--import-rag配合使用）",
    )
    parser.add_argument(
        "--import-path",
        default="",
        help="导入知识文件路径（JSONL或文本文件，与--import-rag配合使用）",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="显示RAG库统计信息",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="详细输出模式",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="干运行模式：仅加载配置和模型，不执行审计",
    )

    return parser.parse_args()


def _print_banner():
    """打印Banner"""
    banner = r"""
    ╔══════════════════════════════════════════════╗
    ║     合同审计Skill体系 v1.0.0                  ║
    ║     Contract Audit Skill System              ║
    ║     底层引擎: llama.cpp (端侧大模型)           ║
    ╚══════════════════════════════════════════════╝
    """
    print(banner)


def _print_skill_results(report: dict):
    """打印审计结果摘要"""
    print("\n" + "=" * 60)
    print("  审计报告")
    print("=" * 60)

    print(f"\n  报告编号: {report.get('report_id', 'N/A')}")
    print(f"  合同类型: {report.get('contract_info', {}).get('contract_type', 'N/A')}")
    print(f"  综合风险等级: {report.get('overall_risk_level', 'N/A').upper()}")

    risk_summary = report.get("risk_summary", {})
    print(f"\n  风险统计:")
    print(f"    高风险: {risk_summary.get('final_high_risks', risk_summary.get('high', 0))}")
    print(f"    中风险: {risk_summary.get('final_medium_risks', risk_summary.get('medium', 0))}")
    print(f"    低风险: {risk_summary.get('final_low_risks', risk_summary.get('low', 0))}")

    pending = risk_summary.get('pending_review_risks', risk_summary.get('pending', 0))
    if pending:
        print(f"    待人工复核: {pending}")

    print(f"\n  审计摘要:")
    print(f"    {report.get('executive_summary', 'N/A')}")

    risk_items = report.get("risk_items", [])
    if risk_items:
        print(f"\n  风险详情（共{len(risk_items)}项）:")
        for item in risk_items:
            level_mark = {"high": "!!!", "medium": "!!", "low": "!", "pending": "?"}.get(
                item.get("risk_level", "low"), "!")
            print(f"    [{level_mark} {item.get('risk_level', '').upper()}] "
                  f"{item.get('risk_category', '')}")
            print(f"      问题: {item.get('issue', '')[:100]}...")
            print(f"      位置: {item.get('clause_location', 'N/A')}")
            if item.get("cross_validated"):
                print(f"      交叉验证: 已确认")

    print(f"\n  报告文件:")
    print(f"    JSON: {report.get('report_json', 'N/A')}")
    print(f"    Markdown: {report.get('report_md', 'N/A')}")

    print("\n" + "=" * 60)


def main():
    """CLI主入口"""
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    _print_banner()

    # ---- 加载项目配置 ----
    app_cfg = _load_app_config(args.config)

    # ---- 初始化LLM服务 ----
    llm = _init_llm_service(app_cfg)
    if llm is None and not args.stats and not args.import_rag and not args.dry_run:
        logger.error("无法初始化LLM服务，请检查 llm_config 配置")
        sys.exit(1)

    # ---- 初始化Embedding ----
    embedder = _init_embedder(app_cfg)

    # ---- RAG统计模式 ----
    if args.stats:
        from app.contract_audit_skills.core.skill_rag import SkillRAGManager
        config_dir = os.path.dirname(args.config) if args.config else os.path.dirname(__file__)
        rag_base = os.path.join(config_dir, "..", "rag_store")
        rag_mgr = SkillRAGManager(base_path=rag_base, embedder=embedder)
        stats = rag_mgr.get_collection_stats()
        print("\nRAG库统计:")
        for sid, s in stats.items():
            print(f"  {sid}: 文档数={s['document_count']}, 状态={s['status']}")
        return

    # ---- RAG导入模式 ----
    if args.import_rag:
        if not args.import_skill:
            logger.error("请指定 --import-skill 参数")
            sys.exit(1)
        if not args.import_path:
            logger.error("请指定 --import-path 参数")
            sys.exit(1)

        from app.contract_audit_skills.core.skill_rag import (
            SkillRAGManager, import_from_jsonl, import_from_text_files,
        )
        config_dir = os.path.dirname(args.config) if args.config else os.path.dirname(__file__)
        rag_base = os.path.join(config_dir, "..", "rag_store")
        rag_mgr = SkillRAGManager(base_path=rag_base, embedder=embedder)

        if args.import_path.endswith(".jsonl"):
            result = import_from_jsonl(args.import_skill, args.import_path, rag_mgr)
        else:
            result = import_from_text_files(args.import_skill, [args.import_path], rag_mgr)

        print(f"\nRAG导入完成:")
        print(f"  导入文档: {result['total_docs']}")
        print(f"  导入分块: {result['imported_chunks']}")
        if result['errors']:
            print(f"  错误: {result['errors']}")
        return

    # ---- 干运行模式 ----
    if args.dry_run:
        print("\n干运行模式：配置和模型加载正常")
        print(f"  LLM Provider: {app_cfg.get('llm_config', {}).get('provider', 'N/A')}")
        print(f"  LLM Model: {app_cfg.get('llm_config', {}).get('model', 'N/A')}")
        return

    # ---- 检查文件 ----
    if not os.path.exists(args.file):
        logger.error("文件不存在: %s", args.file)
        sys.exit(1)

    # ---- 执行审计 ----
    print(f"\n开始审计: {args.file}")

    kwargs = {}
    if args.temp is not None:
        kwargs["temperature"] = args.temp
    if args.max_tokens is not None:
        kwargs["max_tokens"] = args.max_tokens

    output_dir = args.output or os.path.join(os.path.dirname(__file__), "..", "output")
    config_dir = os.path.dirname(args.config) if args.config else os.path.dirname(__file__)
    config_path = args.config or os.path.join(config_dir, "..", "skill_config.yaml")

    def progress_cb(stage: str, percent: int, message: str):
        bar_len = 30
        filled = int(bar_len * percent / 100)
        bar = "█" * filled + "░" * (bar_len - filled)
        print(f"\r  [{bar}] {percent:3d}% {stage}: {message}", end="", flush=True)

    start_time = time.time()

    try:
        from app.contract_audit_skills.core.skill_orchestrator import audit_contract_file

        result = audit_contract_file(
            file_path=args.file,
            llm_service=llm,
            config_path=config_path,
            embedder=embedder,
            output_dir=output_dir,
            progress_cb=progress_cb,
            **kwargs,
        )

        elapsed = time.time() - start_time
        print(f"\n\n审计完成！耗时: {elapsed:.1f}秒")

        _print_skill_results(result)

    except KeyboardInterrupt:
        print("\n\n用户中断审计")
        sys.exit(130)
    except Exception as e:
        logger.exception("审计执行失败")
        print(f"\n审计失败: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
