#!/usr/bin/env python3
"""
Contract Markdown Export CLI
==============================
Export contract files (PDF/DOCX/TXT) to structured Markdown locally.

Usage:
  python bin/export_contract_markdown.py contract.pdf
  python bin/export_contract_markdown.py contract.docx -o ./output/
  python bin/export_contract_markdown.py contract.pdf --no-header
  python bin/export_contract_markdown.py ./contracts/ --batch
"""

import argparse
import os
import sys
import glob

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    parser = argparse.ArgumentParser(
        description="Export contract files to structured Markdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s contract.pdf
  %(prog)s contract.docx -o ./markdown_output/
  %(prog)s contract.pdf --no-header
  %(prog)s ./contracts/ --batch
  %(prog)s contract.txt --no-header -o result.md
        """,
    )
    parser.add_argument("input", help="Contract file path (PDF/DOCX/TXT) or directory (with --batch)")
    parser.add_argument("-o", "--output", default="", help="Output .md file path or directory")
    parser.add_argument("--no-header", action="store_true", help="Skip YAML metadata header")
    parser.add_argument("--batch", action="store_true", help="Batch mode: export all supported files in directory")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    from app.services.markdown_export import export_contract_to_markdown
    from app.core.config import get_config

    cfg = get_config()
    include_header = not args.no_header

    if args.batch:
        # Batch mode: process all supported files in directory
        if not os.path.isdir(args.input):
            print(f"Error: '{args.input}' is not a directory")
            sys.exit(1)

        supported = []
        for ext in ("*.pdf", "*.docx", "*.txt"):
            supported.extend(glob.glob(os.path.join(args.input, ext)))

        if not supported:
            print(f"No supported files found in '{args.input}'")
            sys.exit(1)

        output_dir = args.output or args.input
        os.makedirs(output_dir, exist_ok=True)

        print(f"Batch export: {len(supported)} file(s) -> {output_dir}")
        success_count = 0
        for file_path in sorted(supported):
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            out_path = os.path.join(output_dir, f"{base_name}.md")
            result = export_contract_to_markdown(
                file_path=file_path,
                output_path=out_path,
                cfg=cfg,
                include_metadata_header=include_header,
            )
            if result["success"]:
                success_count += 1
                print(f"  [OK] {os.path.basename(file_path)} -> {os.path.basename(result['output_path'])} "
                      f"({result['text_length']} chars)")
            else:
                print(f"  [FAIL] {os.path.basename(file_path)}: {result['error']}")

        print(f"\nDone: {success_count}/{len(supported)} exported successfully")
        sys.exit(0 if success_count == len(supported) else 1)

    # Single file mode
    if not os.path.exists(args.input):
        print(f"Error: File not found: {args.input}")
        sys.exit(1)

    output_path = args.output
    if output_path and os.path.isdir(output_path):
        base_name = os.path.splitext(os.path.basename(args.input))[0]
        output_path = os.path.join(output_path, f"{base_name}.md")

    if args.verbose:
        print(f"Input:  {os.path.abspath(args.input)}")
        print(f"Output: {output_path or '(auto)'}")

    result = export_contract_to_markdown(
        file_path=args.input,
        output_path=output_path,
        cfg=cfg,
        include_metadata_header=include_header,
    )

    if result["success"]:
        print(f"Exported: {result['output_path']}")
        print(f"  Source: {result['source_file']} ({result.get('source_format', '?')})")
        print(f"  Text:   {result['text_length']} chars")
        print(f"  MD:     {result['markdown_length']} chars")
        if result.get("meta", {}).get("ocr_used"):
            print(f"  OCR:    {result['meta'].get('ocr_engine', 'unknown')}")
        if result.get("meta", {}).get("page_count"):
            print(f"  Pages:  {result['meta']['page_count']}")
    else:
        print(f"Error: {result['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
