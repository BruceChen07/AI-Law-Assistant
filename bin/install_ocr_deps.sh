#!/bin/bash
set -e

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
BIN_DIR="$ROOT_DIR/bin"
OS=$(uname -s | tr '[:upper:]' '[:lower:]')

if [ "$OS" = "darwin" ]; then
  bash "$BIN_DIR/install_ocr_macos.sh"
elif [ "$OS" = "linux" ]; then
  if ! command -v tesseract >/dev/null 2>&1; then
    echo "tesseract not found"
    exit 1
  fi
  if ! command -v pdftoppm >/dev/null 2>&1; then
    echo "poppler not found"
    exit 1
  fi
else
  echo "unsupported os"
  exit 1
fi

PYTHON_BIN=${PYTHON_BIN:-python3}
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "python3 not found"
  exit 1
fi

INDEX_URL=${OCR_PIP_INDEX:-}
PKGS=(
  "pytesseract==0.3.10"
  "pdf2image==1.17.0"
  "pillow>=11.0.0"
  "pypdf>=5.6.0"
  "mineru[all]==2.7.6"
)

if command -v uv >/dev/null 2>&1; then
  INSTALLER="uv pip install"
else
  INSTALLER="$PYTHON_BIN -m pip install"
fi

if [ -n "$INDEX_URL" ]; then
  $INSTALLER -i "$INDEX_URL" -U pip setuptools wheel
  $INSTALLER -i "$INDEX_URL" "${PKGS[@]}"
else
  $INSTALLER -U pip setuptools wheel
  $INSTALLER "${PKGS[@]}"
fi