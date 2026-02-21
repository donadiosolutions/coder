#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
FIXTURES_DIR="${SCRIPT_DIR}/fixtures"

RENDER_SCRIPT="${ROOT_DIR}/scripts/release/render_release_body.py"
PROMPT_SCRIPT="${ROOT_DIR}/scripts/release/generate_release_highlights_prompt.py"
VERIFY_SCRIPT="${ROOT_DIR}/scripts/release/verify_draft.sh"
PUBLISH_SCRIPT="${ROOT_DIR}/scripts/release/publish.sh"
PROMPT_TEST_SCRIPT="${SCRIPT_DIR}/test_generate_release_highlights_prompt.py"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

assert_contains() {
  local file="$1"
  local pattern="$2"
  if ! grep -Fq -- "${pattern}" "${file}"; then
    echo "Expected pattern not found: ${pattern}" >&2
    echo "In file: ${file}" >&2
    exit 1
  fi
}

assert_order() {
  local file="$1"
  local first="$2"
  local second="$3"
  local first_line
  local second_line

  first_line="$(grep -nF -- "${first}" "${file}" | head -n1 | cut -d: -f1)"
  second_line="$(grep -nF -- "${second}" "${file}" | head -n1 | cut -d: -f1)"

  if [[ -z "${first_line}" || -z "${second_line}" || "${first_line}" -ge "${second_line}" ]]; then
    echo "Expected '${first}' to appear before '${second}' in ${file}" >&2
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
  --image-ghcr ghcr.io/example/gpubox:v9.9.8@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
  --image-dockerhub docker.io/example/gpubox:v9.9.8@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
  --install-image-tag v9.9.8 \
  --install-image-digest sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
  --notes-json "${FIXTURES_DIR}/generated-notes.json" \
  --output "${output_md}"

assert_contains "${output_md}" "## Highlights"
assert_contains "${output_md}" "## Install"
assert_contains "${output_md}" "## Full changelog"
assert_contains "${output_md}" "helm repo add gpubox https://example.github.io/gpubox"
assert_contains "${output_md}" "  --version 9.9.9 \\"
assert_contains "${output_md}" "  --set image.tag=v9.9.8 \\"
assert_contains "${output_md}" "  --set image.digest=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \\"
assert_contains "${output_md}" "- Example change in #123"
assert_order "${output_md}" "## Highlights" "## Install"
assert_order "${output_md}" "## Install" "## Full changelog"

# Validate optional highlights-file override path.
override_highlights="${tmp_dir}/override-highlights.md"
cat >"${override_highlights}" <<'EOF'
- Topline shipped change one.
- Topline shipped change two.
- Topline shipped change three.
EOF

output_md_override="${tmp_dir}/release-body-override.md"
python3 "${RENDER_SCRIPT}" \
  --tag v9.9.9 \
  --pages-url https://example.github.io/gpubox \
  --image-ghcr ghcr.io/example/gpubox:v9.9.8@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
  --image-dockerhub docker.io/example/gpubox:v9.9.8@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
  --install-image-tag v9.9.8 \
  --install-image-digest sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
  --notes-json "${FIXTURES_DIR}/generated-notes.json" \
  --highlights-file "${override_highlights}" \
  --output "${output_md_override}"

assert_contains "${output_md_override}" "- Topline shipped change one."
assert_contains "${output_md_override}" "- Topline shipped change two."
assert_contains "${output_md_override}" "- Topline shipped change three."

# Invalid highlights override falls back to deterministic highlights.
invalid_highlights="${tmp_dir}/invalid-highlights.md"
cat >"${invalid_highlights}" <<'EOF'
not-a-bullet
still-not-a-bullet
EOF

output_md_invalid="${tmp_dir}/release-body-invalid.md"
python3 "${RENDER_SCRIPT}" \
  --tag v9.9.9 \
  --pages-url https://example.github.io/gpubox \
  --image-ghcr ghcr.io/example/gpubox:v9.9.8@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
  --image-dockerhub docker.io/example/gpubox:v9.9.8@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
  --install-image-tag v9.9.8 \
  --install-image-digest sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
  --notes-json "${FIXTURES_DIR}/generated-notes.json" \
  --highlights-file "${invalid_highlights}" \
  --output "${output_md_invalid}"

assert_contains "${output_md_invalid}" "- Example change in #123"

# Prompt-generator unit tests with fixtures.
assert_succeeds python3 "${PROMPT_TEST_SCRIPT}"

# Prompt-generator smoke invocation for syntax/CLI safety.
assert_succeeds python3 "${PROMPT_SCRIPT}" --help
assert_contains <(python3 "${PROMPT_SCRIPT}" --help) "--release"

assert_succeeds "${VERIFY_SCRIPT}" v9.9.9 --json-file "${FIXTURES_DIR}/release-valid.json"
assert_fails "${VERIFY_SCRIPT}" v9.9.9 --json-file "${FIXTURES_DIR}/release-missing-assets.json"
assert_fails "${VERIFY_SCRIPT}" v9.9.9 --json-file "${FIXTURES_DIR}/release-missing-headings.json"
assert_fails "${VERIFY_SCRIPT}" v9.9.9 --json-file "${FIXTURES_DIR}/release-non-draft.json"

assert_succeeds "${PUBLISH_SCRIPT}" v9.9.9 --check-only --json-file "${FIXTURES_DIR}/release-valid.json"
assert_fails "${PUBLISH_SCRIPT}" v9.9.9 --check-only --json-file "${FIXTURES_DIR}/release-missing-assets.json"
assert_fails "${PUBLISH_SCRIPT}" v9.9.9 --check-only --json-file "${FIXTURES_DIR}/release-missing-headings.json"
assert_succeeds "${PUBLISH_SCRIPT}" v9.9.9 --check-only --json-file "${FIXTURES_DIR}/release-non-draft.json"

echo "Release script fixture tests passed."
