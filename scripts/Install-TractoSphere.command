#!/bin/bash
# Install the companion alpha wheel into a NEW user-local Python environment.
# Usage: bash Install-TractoSphere.command [NEW_ENV_DIRECTORY] [--no-launch]
# Python 3.12+ must already be installed. No administrator rights or scientific
# tool downloads. Existing environments are never overwritten.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
wheel="$script_dir/dmri_studio-0.2.0a0-py3-none-any.whl"
if [[ ! -f "$wheel" ]]; then
  echo "Place this helper beside dmri_studio-0.2.0a0-py3-none-any.whl." >&2
  exit 2
fi
checksum_file="$script_dir/SHA256SUMS"
if [[ ! -f "$checksum_file" ]]; then
  echo "Place the release SHA256SUMS beside the helper and wheel." >&2
  exit 2
fi
expected="$(awk '$2 == "dmri_studio-0.2.0a0-py3-none-any.whl" {print $1}' "$checksum_file")"
actual="$(shasum -a 256 "$wheel" | awk '{print $1}')"
if [[ ! "$expected" =~ ^[a-fA-F0-9]{64}$ || "$expected" != "$actual" ]]; then
  echo "Wheel checksum verification failed. No installation was started." >&2
  exit 2
fi
python_bin="${TRACTOSPHERE_PYTHON:-}"
if [[ -z "$python_bin" ]]; then
  for candidate in python3.12 python3.13 python3.14 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3,12))'; then
      python_bin="$(command -v "$candidate")"
      break
    fi
  done
fi
if [[ -z "$python_bin" ]] || ! "$python_bin" -c 'import sys; raise SystemExit(sys.version_info < (3,12))'; then
  echo "Install Python 3.12 or newer from a trusted distributor first." >&2
  exit 2
fi
install_dir="${1:-$HOME/Library/Application Support/TractoSphere/alpha-0.2.0}"
if [[ -e "$install_dir" || -L "$install_dir" ]]; then
  echo "Destination exists. Choose a NEW folder; nothing was overwritten: $install_dir" >&2
  exit 2
fi
mkdir -p -- "$(dirname -- "$install_dir")"
"$python_bin" -m venv "$install_dir"
"$install_dir/bin/python" -m pip install --upgrade pip
"$install_dir/bin/python" -m pip install "$wheel[gui]"
"$install_dir/bin/python" -m pip check
"$install_dir/bin/dmri-studio" --version
echo "TractoSphere public alpha installed. Research use only; not for clinical use."
echo "Launch: \"$install_dir/bin/dmri-studio\" gui"
if [[ "${2:-}" != "--no-launch" ]]; then
  exec "$install_dir/bin/dmri-studio" gui
fi
