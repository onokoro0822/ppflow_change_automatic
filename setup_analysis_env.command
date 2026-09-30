#!/bin/zsh
set -euo pipefail

script_dir="${0:A:h}"
python_bin="${PYTHON_BIN:-}"

if [[ -z "$python_bin" ]]; then
  for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && \
       "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
      python_bin="$candidate"
      break
    fi
  done
fi

if [[ -z "$python_bin" ]]; then
  echo "Python 3.10以上が必要です。PYTHON_BIN=/path/to/python を指定してください。" >&2
  exit 1
fi

"$python_bin" -m venv "$script_dir/.venv"
"$script_dir/.venv/bin/python" -m pip install --upgrade pip
"$script_dir/.venv/bin/python" -m pip install -r "$script_dir/requirements-analysis.txt"

echo "環境構築完了: $script_dir/.venv"
echo "実行: $script_dir/.venv/bin/python $script_dir/chukyo_pt_origin_distribution.py"
echo "OD差分: $script_dir/.venv/bin/python $script_dir/meitetsu_origin_distribution_replacement.py"
