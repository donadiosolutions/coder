#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/release/preview_codex_highlights.sh [count]

Generate Codex highlight previews for the most recent non-draft releases.
Defaults to 5 releases.

Requirements:
- gh authenticated for the target repository
- OPENAI_API_KEY available in the environment
- codex CLI available on PATH
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

count="${1:-5}"
if ! [[ "${count}" =~ ^[0-9]+$ ]] || [[ "${count}" -lt 1 ]]; then
  echo "count must be a positive integer" >&2
  exit 2
fi

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY must be set for live Codex preview." >&2
  exit 2
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI is required" >&2
  exit 2
fi

if ! command -v codex >/dev/null 2>&1; then
  echo "codex CLI is required" >&2
  exit 2
fi

repo="${GITHUB_REPOSITORY:-}"
if [[ -z "${repo}" ]]; then
  repo="$(gh repo view --json nameWithOwner -q '.nameWithOwner')"
fi

mapfile -t tags < <(
  gh api "repos/${repo}/releases" \
    --jq "[.[] | select(.draft==false and .tag_name!=null) | .tag_name][0:${count}][]"
)

if [[ "${#tags[@]}" -eq 0 ]]; then
  echo "No non-draft releases found for ${repo}" >&2
  exit 1
fi

for tag in "${tags[@]}"; do
  workdir="$(mktemp -d)"
  trap 'rm -rf "$workdir"' EXIT

  prompt_file="${workdir}/prompt.txt"
  raw_output_file="${workdir}/codex-output.txt"
  codex_log_file="${workdir}/codex.log"

  python3 scripts/release/generate_release_highlights_prompt.py \
    --release "${tag}" \
    --repo "${repo}" \
    --output-prompt "${prompt_file}" \
    --output-context-json "${workdir}/context.json"

  if ! codex exec \
    --model gpt-5.2 \
    -c 'model_reasoning_effort="high"' \
    --sandbox read-only \
    --output-last-message "${raw_output_file}" \
    - < "${prompt_file}" >/dev/null 2>"${codex_log_file}"; then
    echo "Codex execution failed for ${tag}. Log output:" >&2
    cat "${codex_log_file}" >&2
    exit 1
  fi

  echo "===== ${tag} ====="
  if ! grep -E '^[[:space:]]*-[[:space:]]+' "${raw_output_file}" | sed -E 's/^[[:space:]]*//' ; then
    echo "(no bullet output; raw model response follows)"
    cat "${raw_output_file}"
  fi
  echo

  rm -rf "${workdir}"
  trap - EXIT
done
