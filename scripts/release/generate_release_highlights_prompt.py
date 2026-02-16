#!/usr/bin/env python3
"""Build a Codex prompt for release highlights from GitHub release deltas.

The script is repository-agnostic and supports either a release tag (vX.Y.Z) or
canonical release URL input. It gathers commit content between the current and
previous non-draft releases, de-prioritizes non-shipping files, and writes a
strict prompt suitable for Codex-generated human-readable highlights.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_MAX_COMMITS = 40
DEFAULT_MAX_PATCH_CHARS = 16000

IGNORED_PATH_PATTERNS = [
    ".github/workflows/*",
    ".github/actions/*",
    "scripts/release/*",
    "docs/*",
    "charts/*/README*",
]


@dataclass(frozen=True)
class ReleaseTarget:
    repo: str
    tag: str


class GitHubApiError(RuntimeError):
    """Raised when GitHub API calls fail."""


class GitHubClient:
    def __init__(self, repo: str, token: str | None) -> None:
        self.repo = repo
        self.base_url = "https://api.github.com"
        self.token = token or ""

    def _request(self, path: str, query: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "release-highlights-prompt/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req) as response:
                payload = response.read().decode("utf-8")
                return json.loads(payload)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise GitHubApiError(
                f"GitHub API request failed for {path} ({exc.code}): {body}"
            ) from exc
        except urllib.error.URLError as exc:
            raise GitHubApiError(f"GitHub API request failed for {path}: {exc}") from exc

    def list_releases(self, per_page: int = 100, max_pages: int = 5) -> list[dict[str, Any]]:
        releases: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            chunk = self._request(
                f"/repos/{self.repo}/releases",
                query={"per_page": per_page, "page": page},
            )
            if not isinstance(chunk, list):
                raise GitHubApiError("Unexpected releases payload shape.")
            releases.extend(chunk)
            if len(chunk) < per_page:
                break
        return releases

    def compare(self, base_ref: str, head_ref: str) -> dict[str, Any]:
        return self._request(f"/repos/{self.repo}/compare/{base_ref}...{head_ref}")

    def list_commits(self, ref: str, per_page: int) -> list[dict[str, Any]]:
        payload = self._request(
            f"/repos/{self.repo}/commits",
            query={"sha": ref, "per_page": per_page},
        )
        if not isinstance(payload, list):
            raise GitHubApiError("Unexpected commits list payload shape.")
        return payload

    def get_commit(self, sha: str) -> dict[str, Any]:
        return self._request(f"/repos/{self.repo}/commits/{sha}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a Codex prompt for release highlights.",
    )
    parser.add_argument(
        "--release",
        required=True,
        help="Release tag (vX.Y.Z) or canonical release URL (.../releases/tag/vX.Y.Z).",
    )
    parser.add_argument(
        "--repo",
        default="",
        help="GitHub repository in owner/repo format. Defaults from URL or GITHUB_REPOSITORY.",
    )
    parser.add_argument(
        "--github-token",
        default="",
        help="Optional GitHub token. Defaults to GITHUB_TOKEN, then GH_TOKEN.",
    )
    parser.add_argument(
        "--output-prompt",
        required=True,
        help="Path to write the generated prompt text.",
    )
    parser.add_argument(
        "--output-context-json",
        default="",
        help="Optional path for machine-readable context debug JSON.",
    )
    parser.add_argument(
        "--max-commits",
        type=int,
        default=DEFAULT_MAX_COMMITS,
        help=f"Maximum commits to include (default: {DEFAULT_MAX_COMMITS}).",
    )
    parser.add_argument(
        "--max-patch-chars",
        type=int,
        default=DEFAULT_MAX_PATCH_CHARS,
        help=f"Maximum aggregate patch chars to include (default: {DEFAULT_MAX_PATCH_CHARS}).",
    )
    return parser.parse_args()


def parse_release_input(value: str) -> tuple[str | None, str]:
    if value.startswith("http://") or value.startswith("https://"):
        return parse_release_url(value)
    return None, value.strip()


def parse_release_url(url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc not in {"github.com", "www.github.com"}:
        raise ValueError(f"Unsupported release URL host: {parsed.netloc}")

    path = parsed.path.strip("/")
    parts = path.split("/")
    if len(parts) < 5 or parts[2] != "releases" or parts[3] != "tag":
        raise ValueError(
            "Release URL must be in canonical format /<owner>/<repo>/releases/tag/<tag>."
        )

    owner, repo = parts[0], parts[1]
    tag = urllib.parse.unquote("/".join(parts[4:])).strip()
    if not owner or not repo or not tag:
        raise ValueError("Could not parse owner/repo/tag from release URL.")
    return f"{owner}/{repo}", tag


def resolve_target(release_value: str, repo_arg: str) -> ReleaseTarget:
    repo_from_release, tag = parse_release_input(release_value)
    repo = repo_arg.strip() or repo_from_release or os.getenv("GITHUB_REPOSITORY", "").strip()
    if not repo:
        raise ValueError(
            "Repository is required. Provide --repo, a release URL, or GITHUB_REPOSITORY."
        )
    if "/" not in repo:
        raise ValueError(f"Repository must be owner/repo, got: {repo}")
    if not tag:
        raise ValueError("Release tag is empty.")
    return ReleaseTarget(repo=repo, tag=tag)


def is_ignored_path(path: str) -> bool:
    normalized = path.strip("/")
    base = normalized.rsplit("/", 1)[-1]
    if base.upper().startswith("README"):
        return True

    if normalized.startswith("docs/"):
        return True

    for pattern in IGNORED_PATH_PATTERNS:
        if fnmatch.fnmatch(normalized, pattern):
            return True
    return False


def select_previous_release_tag(
    releases: list[dict[str, Any]], current_tag: str
) -> str | None:
    filtered = [
        release
        for release in releases
        if not release.get("draft") and release.get("tag_name")
    ]

    for index, release in enumerate(filtered):
        if release["tag_name"] == current_tag:
            if index + 1 < len(filtered):
                return str(filtered[index + 1]["tag_name"])
            return None

    for release in filtered:
        tag_name = str(release["tag_name"])
        if tag_name != current_tag:
            return tag_name
    return None


def first_line(text: str) -> str:
    return text.strip().splitlines()[0].strip() if text.strip() else ""


def extract_commit_subject(payload: dict[str, Any]) -> str:
    message = (
        payload.get("commit", {})
        .get("message", "")
        if isinstance(payload.get("commit"), dict)
        else ""
    )
    return first_line(str(message)) or "(no commit subject)"


def truncate_patch(text: str, remaining_chars: int) -> tuple[str, int]:
    if remaining_chars <= 0 or not text:
        return "", remaining_chars
    snippet = text[:remaining_chars]
    if len(text) > len(snippet):
        snippet += "\n... [truncated]"
    return snippet, max(0, remaining_chars - len(snippet))


def curate_commit_payloads(
    commit_payloads: list[dict[str, Any]],
    max_patch_chars: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    curated: list[dict[str, Any]] = []
    stats = {
        "total_commits": len(commit_payloads),
        "commits_with_shipping_changes": 0,
        "commits_ignored_only": 0,
        "shipping_files": 0,
        "ignored_files": 0,
    }

    remaining_patch_budget = max_patch_chars

    for payload in commit_payloads:
        sha = str(payload.get("sha", ""))
        subject = extract_commit_subject(payload)
        html_url = str(payload.get("html_url", ""))
        files = payload.get("files", [])
        if not isinstance(files, list):
            files = []

        shipping_files: list[dict[str, Any]] = []
        ignored_files: list[str] = []

        for file_payload in files:
            if not isinstance(file_payload, dict):
                continue
            filename = str(file_payload.get("filename", ""))
            if not filename:
                continue

            if is_ignored_path(filename):
                ignored_files.append(filename)
                continue

            patch_text = str(file_payload.get("patch", ""))
            patch_snippet, remaining_patch_budget = truncate_patch(
                patch_text,
                remaining_patch_budget,
            )

            shipping_files.append(
                {
                    "filename": filename,
                    "status": str(file_payload.get("status", "unknown")),
                    "additions": int(file_payload.get("additions", 0) or 0),
                    "deletions": int(file_payload.get("deletions", 0) or 0),
                    "changes": int(file_payload.get("changes", 0) or 0),
                    "patch": patch_snippet,
                }
            )

        stats["ignored_files"] += len(ignored_files)
        stats["shipping_files"] += len(shipping_files)

        if shipping_files:
            stats["commits_with_shipping_changes"] += 1
            curated.append(
                {
                    "sha": sha,
                    "short_sha": sha[:8],
                    "subject": subject,
                    "html_url": html_url,
                    "shipping_files": shipping_files,
                    "ignored_files": ignored_files,
                }
            )
        else:
            stats["commits_ignored_only"] += 1

    return curated, stats


def build_prompt(context: dict[str, Any]) -> str:
    commits = context.get("commits", [])
    release = context.get("release", {})
    compare_url = context.get("compare_url", "")

    commit_lines: list[str] = []
    for commit in commits:
        commit_lines.append(
            f"- {commit['short_sha']} {commit['subject']} ({commit['html_url']})"
        )
        for file_info in commit.get("shipping_files", []):
            commit_lines.append(
                "  - file: "
                f"{file_info['filename']} "
                f"[{file_info['status']}, +{file_info['additions']}/-{file_info['deletions']}]"
            )
            patch = file_info.get("patch", "")
            if patch:
                commit_lines.append("    patch:")
                for line in patch.splitlines():
                    commit_lines.append(f"      {line}")

    if not commit_lines:
        commit_lines.append("- No shipping-relevant commit/file details were extracted.")

    stats = context.get("stats", {})
    prompt = textwrap.dedent(
        f"""
        You are writing release highlights for humans.

        Output contract:
        - Return ONLY markdown bullet lines.
        - Output 3 to 6 bullets.
        - Each line MUST start with "- ".
        - No headings, no preamble, no code fences, no JSON.
        - Focus only on shipped behavior and noteworthy user/developer impact.
        - Merge related low-level changes into cohesive high-level bullets.
        - Ignore release workflow churn, CI plumbing, docs-only edits, and script-only release process changes unless they directly affect shipped runtime behavior.

        Release metadata:
        - Repository: {release.get('repo', '')}
        - Current tag: {release.get('current_tag', '')}
        - Previous release tag: {release.get('previous_tag', '(none)')}
        - Compare URL: {compare_url or '(not available)'}

        Context quality stats:
        - Total commits inspected: {stats.get('total_commits', 0)}
        - Commits with shipping-relevant file changes: {stats.get('commits_with_shipping_changes', 0)}
        - Commits ignored as non-shipping-only: {stats.get('commits_ignored_only', 0)}
        - Shipping files considered: {stats.get('shipping_files', 0)}
        - Ignored files filtered: {stats.get('ignored_files', 0)}

        Curated commit and code context:
        {chr(10).join(commit_lines)}
        """
    ).strip()

    return f"{prompt}\n"


def load_release_delta(
    client: GitHubClient,
    current_tag: str,
    max_commits: int,
) -> tuple[str | None, str, list[str]]:
    releases = client.list_releases()
    previous_tag = select_previous_release_tag(releases, current_tag)

    commit_shas: list[str] = []
    compare_url = ""

    if previous_tag:
        compare_payload = client.compare(previous_tag, current_tag)
        compare_url = str(compare_payload.get("html_url", ""))
        compare_commits = compare_payload.get("commits", [])
        if isinstance(compare_commits, list):
            commit_shas = [
                str(item.get("sha", ""))
                for item in compare_commits
                if isinstance(item, dict) and item.get("sha")
            ]
    else:
        commit_list = client.list_commits(current_tag, max_commits)
        commit_shas = [
            str(item.get("sha", ""))
            for item in commit_list
            if isinstance(item, dict) and item.get("sha")
        ]

    unique_shas: list[str] = []
    seen: set[str] = set()
    for sha in commit_shas:
        if not sha or sha in seen:
            continue
        seen.add(sha)
        unique_shas.append(sha)

    if len(unique_shas) > max_commits:
        unique_shas = unique_shas[-max_commits:]

    return previous_tag, compare_url, unique_shas


def choose_token(cli_token: str) -> str:
    if cli_token:
        return cli_token
    env_token = os.getenv("GITHUB_TOKEN", "").strip()
    if env_token:
        return env_token
    return os.getenv("GH_TOKEN", "").strip()


def write_text(path: str, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def main() -> int:
    args = parse_args()

    try:
        target = resolve_target(args.release, args.repo)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    token = choose_token(args.github_token)
    client = GitHubClient(target.repo, token)

    try:
        previous_tag, compare_url, commit_shas = load_release_delta(
            client,
            target.tag,
            args.max_commits,
        )

        commit_payloads = [client.get_commit(sha) for sha in commit_shas]
        curated_commits, stats = curate_commit_payloads(
            commit_payloads,
            max_patch_chars=args.max_patch_chars,
        )
    except GitHubApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    context = {
        "release": {
            "repo": target.repo,
            "current_tag": target.tag,
            "previous_tag": previous_tag,
        },
        "compare_url": compare_url,
        "stats": stats,
        "commits": curated_commits,
    }

    prompt = build_prompt(context)
    write_text(args.output_prompt, prompt)

    if args.output_context_json:
        write_text(args.output_context_json, json.dumps(context, indent=2, sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
