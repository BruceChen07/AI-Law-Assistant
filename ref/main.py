from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


SUPPORTED_PDF_EXTENSIONS = {".pdf"}
SUPPORTED_DOCX_EXTENSIONS = {".docx"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--path", required=True,
                        help="Input file or directory path")
    parser.add_argument("-o", "--output", required=True,
                        help="MinerU output directory path")
    parser.add_argument("-b", "--backend",
                        default="pipeline", help="MinerU backend")
    parser.add_argument("--source", default="local", help="MinerU source")
    parser.add_argument(
        "--converted-dir",
        default=None,
        help="Directory to store converted PDF files from DOCX",
    )
    parser.add_argument(
        "--keep-converted",
        action="store_true",
        help="Keep converted PDF files when conversion directory is temporary",
    )
    return parser.parse_args()


def discover_input_files(input_path: Path) -> tuple[list[Path], list[Path]]:
    if input_path.is_file():
        suffix = input_path.suffix.lower()
        if suffix in SUPPORTED_PDF_EXTENSIONS:
            return [input_path], []
        if suffix in SUPPORTED_DOCX_EXTENSIONS:
            return [], [input_path]
        raise ValueError(f"Unsupported file type: {input_path}")

    if not input_path.is_dir():
        raise ValueError(f"Input path does not exist: {input_path}")

    pdf_files: list[Path] = []
    docx_files: list[Path] = []
    for file_path in input_path.rglob("*"):
        if not file_path.is_file():
            continue
        suffix = file_path.suffix.lower()
        if suffix in SUPPORTED_PDF_EXTENSIONS:
            pdf_files.append(file_path)
        elif suffix in SUPPORTED_DOCX_EXTENSIONS:
            docx_files.append(file_path)
    return pdf_files, docx_files


def convert_docx_to_pdf_with_docx2pdf(docx_files: list[Path], target_dir: Path) -> list[Path]:
    from docx2pdf import convert

    converted_files: list[Path] = []
    for docx_path in docx_files:
        output_pdf = target_dir / f"{docx_path.stem}.pdf"
        convert(str(docx_path), str(output_pdf))
        if not output_pdf.exists():
            raise RuntimeError(f"Failed to convert DOCX to PDF: {docx_path}")
        converted_files.append(output_pdf)
    return converted_files


def convert_docx_to_pdf_with_win32com(docx_files: list[Path], target_dir: Path) -> list[Path]:
    import win32com.client

    word = win32com.client.Dispatch("Word.Application")
    word.Visible = False

    converted_files: list[Path] = []
    try:
        for docx_path in docx_files:
            output_pdf = target_dir / f"{docx_path.stem}.pdf"
            document = word.Documents.Open(str(docx_path.resolve()))
            document.SaveAs(str(output_pdf.resolve()), FileFormat=17)
            document.Close(False)
            if not output_pdf.exists():
                raise RuntimeError(
                    f"Failed to convert DOCX to PDF: {docx_path}")
            converted_files.append(output_pdf)
    finally:
        word.Quit()
    return converted_files


def convert_docx_to_pdf(docx_files: list[Path], target_dir: Path) -> list[Path]:
    if not docx_files:
        return []

    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        return convert_docx_to_pdf_with_docx2pdf(docx_files, target_dir)
    except Exception:
        pass

    try:
        return convert_docx_to_pdf_with_win32com(docx_files, target_dir)
    except Exception as error:
        raise RuntimeError(
            "DOCX conversion failed. Install docx2pdf or pywin32, and ensure Microsoft Word is available."
        ) from error


def resolve_mineru_entry() -> list[str]:
    return [sys.executable, "-m", "mineru.cli.client"]


def run_mineru_for_files(
    files: list[Path],
    output_dir: Path,
    backend: str,
    source: str,
) -> None:
    mineru_cmd_prefix = resolve_mineru_entry()
    output_dir.mkdir(parents=True, exist_ok=True)

    for input_file in files:
        command = [
            *mineru_cmd_prefix,
            "-p",
            str(input_file),
            "-o",
            str(output_dir),
            "-b",
            backend,
            "--source",
            source,
        ]
        print("Running:", " ".join(command))
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
        )
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)

        combined_output = f"{result.stdout}\n{result.stderr}"
        if (
            result.returncode != 0
            or "Traceback (most recent call last):" in combined_output
            or "ModuleNotFoundError:" in combined_output
            or "FileNotFoundError:" in combined_output
        ):
            raise RuntimeError(f"MinerU parse failed: {input_file}")


def main() -> int:
    args = parse_args()
    input_path = Path(args.path).resolve()
    output_dir = Path(args.output).resolve()

    pdf_files, docx_files = discover_input_files(input_path)
    convert_dir = (
        Path(args.converted_dir).resolve()
        if args.converted_dir
        else (output_dir / "_converted_pdf")
    )

    converted_files = convert_docx_to_pdf(docx_files, convert_dir)
    all_pdf_inputs = [*pdf_files, *converted_files]

    if not all_pdf_inputs:
        print("No supported input files found. Provide PDF or DOCX.")
        return 1

    run_mineru_for_files(
        files=all_pdf_inputs,
        output_dir=output_dir,
        backend=args.backend,
        source=args.source,
    )

    if converted_files and not args.keep_converted and not args.converted_dir:
        shutil.rmtree(convert_dir, ignore_errors=True)

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
