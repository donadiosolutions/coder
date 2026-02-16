#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
FIXTURES_DIR="${SCRIPT_DIR}/fixtures"

RENDER_SCRIPT="${ROOT_DIR}/scripts/release/render_release_body.py"
VERIFY_SCRIPT="${ROOT_DIR}/scripts/release/verify_draft.sh"
PUBLISH_SCRIPT="${ROOT_DIR}/scripts/release/publish.sh"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

assert_contains() {
  local file="$1"
  local pattern="$2"
  if ! grep -Fq "${pattern}" "${file}"; then
    echo "Expected pattern not found: ${pattern}" >&2
    echo "In file: ${file}" >&2
    exit 1
  fi
}

assert_fails() {
  set +e
  "$@" >/dev/null 2>&1
  local rc=$?
  set -e
  if [[ ${rc} -eq 0 ]]; then
    echo "Expected command to fail, but it passed: $*" >&2
    exit 1
  fi
}

assert_succeeds() {
  "$@" >/dev/null
}

output_md="${tmp_dir}/release-body.md"
python3 "${RENDER_SCRIPT}" \
  --tag v9.9.9 \
  --pages-url https://example.github.io/gpubox \
  --image-ghcr ghcr.io/example/gpubox:v9.9.9 \
  --image-dockerhub docker.io/example/gpubox:v9.9.9 \
  --notes-json "${FIXTURES_DIR}/generated-notes.json" \
  --output "${output_md}"

assert_contains "${output_md}" "## Highlights"
assert_contains "${output_md}" "## Install"
assert_contains "${output_md}" "## Full changelog"
assert_contains "${output_md}" "helm repo add gpubox https://example.github.io/gpubox"
assert_contains "${output_md}" 'This release includes `v9.9.9` artifacts'

assert_succeeds "${VERIFY_SCRIPT}" v9.9.9 --json-file "${FIXTURES_DIR}/release-valid.json"
assert_fails "${VERIFY_SCRIPT}" v9.9.9 --json-file "${FIXTURES_DIR}/release-missing-assets.json"
assert_fails "${VERIFY_SCRIPT}" v9.9.9 --json-file "${FIXTURES_DIR}/release-missing-headings.json"
assert_fails "${VERIFY_SCRIPT}" v9.9.9 --json-file "${FIXTURES_DIR}/release-non-draft.json"

assert_succeeds "${PUBLISH_SCRIPT}" v9.9.9 --check-only --json-file "${FIXTURES_DIR}/release-valid.json"
assert_fails "${PUBLISH_SCRIPT}" v9.9.9 --check-only --json-file "${FIXTURES_DIR}/release-missing-assets.json"
assert_fails "${PUBLISH_SCRIPT}" v9.9.9 --check-only --json-file "${FIXTURES_DIR}/release-missing-headings.json"
assert_succeeds "${PUBLISH_SCRIPT}" v9.9.9 --check-only --json-file "${FIXTURES_DIR}/release-non-draft.json"

echo "Release script fixture tests passed."
