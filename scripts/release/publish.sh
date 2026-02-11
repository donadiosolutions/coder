#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/release/publish.sh <tag> [--check-only] [--json-file <path>]

Publishes a draft release as latest after validating required preconditions.
Options:
  --check-only        Run validations without publishing.
  --json-file <path>  Use a local release JSON fixture (tests only).
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

TAG=""
CHECK_ONLY="false"
JSON_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-only)
      CHECK_ONLY="true"
      shift
      ;;
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
      if [[ -z "${TAG}" ]]; then
        TAG="$1"
        shift
      else
        usage
        exit 2
      fi
      ;;
  esac
done

if [[ -z "${TAG}" ]]; then
  usage
  exit 2
fi

if [[ -n "${JSON_FILE}" && ! -f "${JSON_FILE}" ]]; then
  die \
    "Release fixture file not found." \
    "The --json-file argument points to a missing file: ${JSON_FILE}." \
    "Use an existing fixture file or omit --json-file for live GitHub checks." \
    "Validate fixture setup in tests before running publish checks."
fi

if ! command -v jq >/dev/null 2>&1; then
  die \
    "jq is required but not installed." \
    "publish.sh uses jq to evaluate release metadata fields." \
    "Install jq and rerun publish.sh." \
    "Include jq in local bootstrap docs and release automation environments."
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERIFY_SCRIPT="${SCRIPT_DIR}/verify_draft.sh"

if [[ ! -x "${VERIFY_SCRIPT}" ]]; then
  die \
    "Draft verification script is missing or not executable." \
    "Expected executable helper at ${VERIFY_SCRIPT}." \
    "Ensure scripts/release/verify_draft.sh exists and has execute permissions." \
    "Keep release helper scripts versioned and executable in the repository."
fi

fetch_release_json() {
  local repo="$1"
  local tag="$2"
  local api_err
  local view_err
  local api_json
  local view_json
  local api_msg
  local view_msg

  api_err="$(mktemp)"
  if api_json="$(gh api "repos/${repo}/releases/tags/${tag}" 2>"${api_err}")"; then
    rm -f "${api_err}"
    printf '%s' "${api_json}"
    return 0
  fi
  api_msg="$(tr '\n' ' ' <"${api_err}")"
  rm -f "${api_err}"

  view_err="$(mktemp)"
  if view_json="$(gh release view "${tag}" --repo "${repo}" \
    --json databaseId,isDraft,url,body,assets,tagName,publishedAt 2>"${view_err}")"; then
    rm -f "${view_err}"
    jq -c '{
      id: .databaseId,
      draft: .isDraft,
      html_url: .url,
      body: .body,
      assets: .assets,
      tag_name: .tagName,
      published_at: .publishedAt
    }' <<<"${view_json}"
    return 0
  fi
  view_msg="$(tr '\n' ' ' <"${view_err}")"
  rm -f "${view_err}"

  {
    echo "${api_msg}"
    echo "${view_msg}"
  } >&2
  return 1
}

if [[ -n "${JSON_FILE}" ]]; then
  RELEASE_JSON="$(cat "${JSON_FILE}")"
  REPO="fixture/repo"
else
  if ! command -v gh >/dev/null 2>&1; then
    die \
      "gh CLI is required but not installed." \
      "publish.sh uses gh to query and patch GitHub Releases." \
      "Install gh and authenticate before publishing." \
      "Document gh as a hard prerequisite for release operators."
  fi

  if [[ -z "${GH_TOKEN:-}" && -n "${GITHUB_TOKEN:-}" ]]; then
    export GH_TOKEN="${GITHUB_TOKEN}"
  fi

  REPO="${GITHUB_REPOSITORY:-}"
  if [[ -z "${REPO}" ]]; then
    REPO="$(gh repo view --json nameWithOwner -q '.nameWithOwner')"
  fi

  if ! RELEASE_JSON="$(fetch_release_json "${REPO}" "${TAG}" 2>/tmp/publish_release.err)"; then
    die \
      "Unable to fetch release metadata for publication." \
      "$(tr '\n' ' ' </tmp/publish_release.err)" \
      "Confirm the tag exists and a release has been created by CI for ${TAG}." \
      "Publish only after tag-triggered release automation completes successfully."
  fi
fi

DRAFT="$(jq -r '.draft // .isDraft // false' <<<"${RELEASE_JSON}")"
RELEASE_ID="$(jq -r '.id // .databaseId // empty' <<<"${RELEASE_JSON}")"
HTML_URL="$(jq -r '.html_url // .url // ""' <<<"${RELEASE_JSON}")"

if [[ "${DRAFT}" != "true" ]]; then
  echo "Release ${TAG} is already published."
  if [[ -n "${HTML_URL}" ]]; then
    echo "${HTML_URL}"
  fi
  exit 0
fi

if [[ -n "${JSON_FILE}" ]]; then
  "${VERIFY_SCRIPT}" "${TAG}" --json-file "${JSON_FILE}"
else
  "${VERIFY_SCRIPT}" "${TAG}"
fi

if [[ "${CHECK_ONLY}" == "true" ]]; then
  echo "Check-only verification passed for ${TAG}"
  exit 0
fi

if [[ -n "${JSON_FILE}" ]]; then
  die \
    "Cannot publish when using a local fixture." \
    "--json-file mode is intended for offline checks only." \
    "Run publish.sh without --json-file to publish a real release." \
    "Use --check-only with fixtures for deterministic local tests."
fi

if [[ -z "${RELEASE_ID}" ]]; then
  die \
    "Release ID is missing from API response." \
    "The release payload for ${TAG} did not include a valid numeric ID." \
    "Retry after fetching release metadata again." \
    "Avoid partial release objects by relying on tag-based release endpoints."
fi

if ! gh api -X PATCH "repos/${REPO}/releases/${RELEASE_ID}" \
  -f draft=false \
  -f make_latest=true >/tmp/publish_patch.out 2>/tmp/publish_patch.err; then
  die \
    "Failed to publish draft release." \
    "$(tr '\n' ' ' </tmp/publish_patch.err)" \
    "Verify token permissions include repository contents:write and retry." \
    "Use API-based patching with explicit draft/latest fields for predictable publish behavior."
fi

attempt=1
max_attempts=12
while [[ "${attempt}" -le "${max_attempts}" ]]; do
  if ! LIVE_JSON="$(fetch_release_json "${REPO}" "${TAG}" 2>/tmp/publish_poll.err)"; then
    sleep 3
    attempt=$((attempt + 1))
    continue
  fi
  LIVE_DRAFT="$(jq -r '.draft // .isDraft // false' <<<"${LIVE_JSON}")"
  LIVE_PUBLISHED_AT="$(jq -r '.published_at // .publishedAt // ""' <<<"${LIVE_JSON}")"
  LIVE_URL="$(jq -r '.html_url // .url // ""' <<<"${LIVE_JSON}")"

  if [[ "${LIVE_DRAFT}" == "false" && -n "${LIVE_PUBLISHED_AT}" ]]; then
    echo "${LIVE_URL}"
    exit 0
  fi

  sleep 3
  attempt=$((attempt + 1))
done

die \
  "Release did not reach published state within the expected polling window." \
  "GitHub API still reports draft=true or empty published_at for ${TAG}." \
  "Re-run publish.sh for ${TAG}; if it persists, inspect release metadata in GitHub UI." \
  "Keep bounded polling after publish calls to handle eventual consistency."
