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
if command -v python3 >/dev/null 2>&1; then
  python3 -m pip install -U "mineru==3.1.5"
else
  echo "python3 not found"
  exit 1
fi
echo "mineru: $(mineru --version | head -n 1)"
