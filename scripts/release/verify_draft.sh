#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/release/verify_draft.sh <tag> [--json-file <path>]

Checks that a release is a valid draft candidate:
- draft=true
- body contains "## Highlights" and "## Full changelog"
- assets include at least one .tgz and one .spdx.json file
EOF
}

log_failure() {
  local failure="$1"
  local cause="$2"
  local fix="$3"
  local prevention="$4"

  {
    echo "Failure: ${failure}"
    echo "Cause: ${cause}"
    echo "Fix: ${fix}"
    echo "Prevention: ${prevention}"
  } >&2
}

die() {
  log_failure "$1" "$2" "$3" "$4"
  exit 1
}

if [[ $# -lt 1 ]]; then
  usage
  exit 2
fi

TAG="$1"
shift

JSON_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --json-file)
      if [[ $# -lt 2 ]]; then
        usage
        exit 2
      fi
      JSON_FILE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

if [[ -n "${JSON_FILE}" && ! -f "${JSON_FILE}" ]]; then
  die \
    "Release fixture file not found." \
    "The --json-file argument points to a missing file: ${JSON_FILE}." \
    "Use an existing JSON fixture path or omit --json-file to fetch from GitHub." \
    "Validate fixture paths in tests before invoking verify_draft.sh."
fi

if ! command -v jq >/dev/null 2>&1; then
  die \
    "jq is required but not installed." \
    "verify_draft.sh uses jq to inspect release JSON payloads." \
    "Install jq and rerun the command." \
    "Include jq in local bootstrap docs and CI images where release tooling is used."
fi

if [[ -n "${JSON_FILE}" ]]; then
  RELEASE_JSON="$(cat "${JSON_FILE}")"
  REPO="fixture/repo"
else
  if ! command -v gh >/dev/null 2>&1; then
    die \
      "gh CLI is required but not installed." \
      "Live release verification queries GitHub APIs via gh." \
      "Install gh or use --json-file for fixture-based verification." \
      "Document gh as a prerequisite for release operators."
  fi

  REPO="${GITHUB_REPOSITORY:-}"
  if [[ -z "${REPO}" ]]; then
    REPO="$(gh repo view --json nameWithOwner -q '.nameWithOwner')"
  fi

  if ! RELEASE_JSON="$(gh api "repos/${REPO}/releases/tags/${TAG}" 2>/tmp/verify_draft.err)"; then
    die \
      "Unable to fetch release metadata." \
      "$(tr '\n' ' ' </tmp/verify_draft.err)" \
      "Ensure the tag exists and GitHub authentication has access to ${REPO}." \
      "Create the draft release via tag-triggered CI before running verification."
  fi
fi

DRAFT="$(jq -r '.draft // false' <<<"${RELEASE_JSON}")"
BODY="$(jq -r '.body // ""' <<<"${RELEASE_JSON}")"
HAS_TGZ="$(jq -r '[.assets[]?.name | select(test("\\.tgz$"))] | length' <<<"${RELEASE_JSON}")"
HAS_SPDX="$(jq -r '[.assets[]?.name | select(test("\\.spdx\\.json$"))] | length' <<<"${RELEASE_JSON}")"
URL="$(jq -r '.html_url // .url // ""' <<<"${RELEASE_JSON}")"

if [[ "${DRAFT}" != "true" ]]; then
  die \
    "Release is not in draft state." \
    "Release ${TAG} in ${REPO} returned draft=${DRAFT}." \
    "Recreate or convert the release to draft before verification." \
    "Keep tag-triggered release automation draft-first for predictable publish gates."
fi

if ! grep -Fq "## Highlights" <<<"${BODY}"; then
  die \
    "Release body is missing Highlights section." \
    "The release body does not contain '## Highlights'." \
    "Regenerate release body with scripts/release/render_release_body.py and update the release." \
    "Use a single release-body generator to avoid drift across automation paths."
fi

if ! grep -Fq "## Full changelog" <<<"${BODY}"; then
  die \
    "Release body is missing Full changelog section." \
    "The release body does not contain '## Full changelog'." \
    "Regenerate release body with scripts/release/render_release_body.py and update the release." \
    "Fail CI when required headings are missing."
fi

if [[ "${HAS_TGZ}" -lt 1 ]]; then
  die \
    "Helm package asset is missing." \
    "No .tgz asset was found on the release for tag ${TAG}." \
    "Attach dist/*.tgz to the release and rerun verification." \
    "Keep release workflow artifact normalization and upload checks enabled."
fi

if [[ "${HAS_SPDX}" -lt 1 ]]; then
  die \
    "SBOM asset is missing." \
    "No .spdx.json asset was found on the release for tag ${TAG}." \
    "Attach dist/*.spdx.json to the release and rerun verification." \
    "Keep SBOM generation and upload steps mandatory in release automation."
fi

echo "Release draft verification passed for ${TAG}"
if [[ -n "${URL}" ]]; then
  echo "URL: ${URL}"
fi
