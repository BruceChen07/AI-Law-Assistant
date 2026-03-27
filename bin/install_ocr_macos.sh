#!/bin/bash
set -e
if ! command -v brew >/dev/null 2>&1; then
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  if [ -x /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [ -x /usr/local/bin/brew ]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
fi
if ! command -v tesseract >/dev/null 2>&1; then
  brew install tesseract
fi
if ! command -v pdftoppm >/dev/null 2>&1; then
  brew install poppler
fi
if ! command -v tesseract >/dev/null 2>&1; then
  echo "tesseract not found"
  exit 1
fi
if ! command -v pdftoppm >/dev/null 2>&1; then
  echo "poppler not found"
  exit 1
fi
if command -v python3 >/dev/null 2>&1; then
  python3 -m pip install -U "mineru[all]==2.7.6"
else
  echo "python3 not found"
  exit 1
fi
echo "tesseract: $(tesseract --version | head -n 1)"
echo "poppler: $(pdftoppm -v 2>&1 | head -n 1)"
