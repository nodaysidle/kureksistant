#!/usr/bin/env bash
# Build a portable Linux release tarball for Kureksistant.
# Fails hard if secrets or personal config would be packaged.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${VERSION:-0.1.0}"
OUT_DIR="${OUT_DIR:-${ROOT}/dist}"
STAGE_NAME="kureksistant-${VERSION}"
ARCHIVE_NAME="kureksistant-v${VERSION}-linux-x86_64.tar.gz"

# Paths that must never appear in a public release archive (relative to stage root).
DENY_PATHS=(
  "config/api_keys.json"
  ".env"
  ".env.local"
  ".env.production"
  "memory/kurek_history.json"
  "memory/long_term.json"
  "memory/clipboard_history.json"
)

DENY_BASENAMES=(
  "api_keys.json"
  ".env"
)

mkdir -p "$OUT_DIR"
STAGE="${OUT_DIR}/${STAGE_NAME}"
rm -rf "$STAGE"
mkdir -p "$STAGE"

echo "==> Staging release tree from ${ROOT}"
# Prefer git archive so untracked local secrets never leak into the package.
if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "$ROOT" archive --format=tar HEAD | tar -x -C "$STAGE"
else
  # Fallback copy with explicit exclusions
  rsync -a \
    --exclude '.git' \
    --exclude '.venv' \
    --exclude 'venv' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.mypy_cache' \
    --exclude '.ruff_cache' \
    --exclude 'dist' \
    --exclude 'config/api_keys.json' \
    --exclude '.env' \
    --exclude '.env.*' \
    --include '.env.example' \
    "$ROOT/" "$STAGE/"
fi

# Ensure example configs exist; never ship real key files.
if [[ ! -f "${STAGE}/.env.example" ]]; then
  echo "ERROR: .env.example missing from staged tree" >&2
  exit 1
fi
if [[ ! -f "${STAGE}/config/api_keys.json.example" ]]; then
  echo "ERROR: config/api_keys.json.example missing from staged tree" >&2
  exit 1
fi

# ── Deny-list check (packaging gate) ──────────────────────────────────────────
fail=0
echo "==> Running packaging deny-list checks"
for rel in "${DENY_PATHS[@]}"; do
  if [[ -e "${STAGE}/${rel}" ]]; then
    echo "DENY: staged path must not exist: ${rel}" >&2
    fail=1
  fi
done

while IFS= read -r -d '' path; do
  base="$(basename "$path")"
  for denied in "${DENY_BASENAMES[@]}"; do
    if [[ "$base" == "$denied" ]]; then
      # Allow only the documented .example siblings (already checked above).
      echo "DENY: forbidden basename in archive staging: ${path#"$STAGE"/}" >&2
      fail=1
    fi
  done
done < <(find "$STAGE" -type f -print0)

# Content greps for common live-key prefixes / env dumps
if grep -RInE --exclude='*.example' --exclude='*.md' --exclude='LICENSE' \
    -e 'AIzaSy[0-9A-Za-z_-]{20,}' \
    -e 'sk-or-[0-9A-Za-z_-]{20,}' \
    -e 'sk-[a-zA-Z0-9]{20,}' \
    -e 'xai-[a-zA-Z0-9]{20,}' \
    "$STAGE" 2>/dev/null | grep -vE 'your-|YOUR_|placeholder|example|sk-your|xai-your|AIzaSy-your' ; then
  echo "DENY: staged tree appears to contain live API key material" >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  echo "ERROR: packaging deny-list failed — refusing to build ${ARCHIVE_NAME}" >&2
  exit 1
fi
echo "==> Deny-list OK"

ARCHIVE_PATH="${OUT_DIR}/${ARCHIVE_NAME}"
rm -f "$ARCHIVE_PATH"
echo "==> Creating ${ARCHIVE_PATH}"
tar -C "$OUT_DIR" -czf "$ARCHIVE_PATH" "$STAGE_NAME"

SUMS_PATH="${OUT_DIR}/SHA256SUMS.txt"
(
  cd "$OUT_DIR"
  sha256sum "$ARCHIVE_NAME" > SHA256SUMS.txt
)
echo "==> Wrote ${SUMS_PATH}"
cat "$SUMS_PATH"
echo "==> Done"
