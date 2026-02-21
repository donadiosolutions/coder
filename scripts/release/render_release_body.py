#!/usr/bin/env python3
"""Render deterministic release notes for GitHub Releases."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a release body with highlights, install instructions, and changelog.",
    )
    parser.add_argument("--tag", required=True, help="Release tag, e.g. v1.2.3")
    parser.add_argument("--pages-url", required=True, help="Helm repo URL")
    parser.add_argument("--image-ghcr", required=True, help="GHCR image reference")
    parser.add_argument("--image-dockerhub", required=True, help="Docker Hub image reference")
    parser.add_argument(
        "--install-image-tag",
        required=True,
        help="Container image tag used in Helm install instructions",
    )
    parser.add_argument(
        "--install-image-digest",
        required=False,
        default="",
        help="Optional container image digest used in Helm install instructions",
    )
    parser.add_argument(
        "--notes-json",
        required=True,
        help="Path to JSON payload returned by repos.generateReleaseNotes",
    )
    parser.add_argument(
        "--highlights-file",
        required=False,
        default="",
        help="Optional path to pre-generated markdown bullet highlights.",
    )
    parser.add_argument("--output", required=True, help="Path to output Markdown file")
    return parser.parse_args()


def load_changelog(notes_json_path: Path) -> str:
    with notes_json_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    notes = payload.get("body")
    if isinstance(notes, str):
        trimmed = notes.strip()
        if trimmed:
            return trimmed

    return "_No generated changelog content was returned by GitHub._"


def load_highlights_file(path: Path) -> list[str]:
    if not path.is_file():
        return []
    bullets: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line.startswith("- "):
            continue
        content = line[2:].strip()
        if content:
            bullets.append(content)
    return bullets


def extract_change_bullets(changelog: str, limit: int = 3) -> list[str]:
    bullets: list[str] = []
    for raw_line in changelog.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("* ") or line.startswith("- "):
            bullet = line[2:].strip()
            if bullet:
                bullets.append(bullet)
        if len(bullets) >= limit:
            break
    return bullets


def extract_pr_titles(changelog: str) -> list[str]:
    titles: list[str] = []
    for bullet in extract_change_bullets(changelog, limit=100):
        cleaned = re.sub(r"\s+by\s+@[^ ]+\s+in\s+https?://\S+$", "", bullet).strip()
        cleaned = re.sub(r"\s*-\s*autoclosed\s*$", "", cleaned, flags=re.IGNORECASE)
        if cleaned:
            titles.append(cleaned)
    return titles


def strip_conventional_prefix(title: str) -> str:
    return re.sub(r"^[a-z]+(?:\([^)]+\))?!?:\s*", "", title).strip()


def sentence_case(text: str) -> str:
    if not text:
        return text
    text = text[0].upper() + text[1:]
    if text[-1] not in ".!?":
        text = f"{text}."
    return text


def human_join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def classify_title(title: str) -> str:
    lower = title.lower()
    if "renovate" in lower and any(
        key in lower
        for key in ("resolve", "repair", "datasource", "regex", "attachment", "digest", "fix(")
    ):
        return "renovate"
    if any(key in lower for key in ("deps", "dependency", "vs code cli", "pin dependencies", " uv ")):
        return "dependencies"
    if "bump chart version" in lower:
        return "release"
    return "other"


def summarize_renovate(titles: list[str]) -> str:
    points = [strip_conventional_prefix(title) for title in titles]
    normalized: list[str] = []
    for point in points:
        if point.startswith("resolve "):
            normalized.append(point.replace("resolve ", "resolving ", 1))
            continue
        if point.startswith("repair "):
            normalized.append(point.replace("repair ", "repairing ", 1))
            continue
        if point.startswith("use "):
            normalized.append(point.replace("use ", "using ", 1))
            continue
        if point.startswith("fix "):
            normalized.append(point.replace("fix ", "fixing ", 1))
            continue
        normalized.append(point)
    summary = sentence_case("Improved Renovate reliability by " + human_join(normalized))
    return re.sub(r"\bgithub\b", "GitHub", summary, flags=re.IGNORECASE)


def summarize_dependencies(titles: list[str]) -> str:
    lower_titles = [title.lower() for title in titles]
    changes: list[str] = []
    if any("pin dependencies" in title for title in lower_titles):
        changes.append("pinned GitHub Actions dependencies")
    if any("vs code cli" in title for title in lower_titles):
        changes.append("updated VS Code CLI")
    if any(re.search(r"\buv\b", title) for title in lower_titles):
        changes.append("updated uv")

    if not changes:
        changes = [strip_conventional_prefix(title) for title in titles]

    return sentence_case(f"Dependency updates: {human_join(changes)}")


def summarize_release(titles: list[str]) -> str:
    first = titles[0]
    match = re.search(r"bump chart version to ([0-9]+\.[0-9]+\.[0-9]+)", first, flags=re.IGNORECASE)
    if match:
        return f"Release readiness: bumped the Helm chart version to {match.group(1)}."
    return sentence_case(f"Release readiness: {strip_conventional_prefix(first)}")


def build_highlights(
    tag: str,
    changelog: str,
    override_bullets: list[str] | None = None,
) -> list[str]:
    if override_bullets:
        return override_bullets

    titles = extract_pr_titles(changelog)
    if not titles:
        return [f"Release `{tag}` includes updates described in the full changelog below."]

    grouped: dict[str, list[str]] = {}
    for title in titles:
        key = classify_title(title)
        grouped.setdefault(key, []).append(title)

    highlights: list[str] = []
    for key, group_titles in grouped.items():
        if key == "renovate":
            highlights.append(summarize_renovate(group_titles))
            continue
        if key == "dependencies":
            highlights.append(summarize_dependencies(group_titles))
            continue
        if key == "release":
            highlights.append(summarize_release(group_titles))
            continue
        for title in group_titles:
            highlights.append(sentence_case(strip_conventional_prefix(title)))

    return highlights[:4]


def render_body(
    tag: str,
    pages_url: str,
    image_ghcr: str,
    image_dockerhub: str,
    install_image_tag: str,
    install_image_digest: str,
    changelog: str,
    override_highlights: list[str] | None = None,
) -> str:
    chart_version = tag[1:] if tag.startswith("v") else tag
    highlights = build_highlights(tag, changelog, override_bullets=override_highlights)
    install_lines: list[str] = [
        "```bash",
        f"helm repo add gpubox {pages_url}",
        "helm repo update",
        "",
        "helm upgrade --install gpubox gpubox/gpubox \\",
        f"  --version {chart_version} \\",
        f"  --set image.tag={install_image_tag} \\",
    ]
    if install_image_digest:
        install_lines.append(f"  --set image.digest={install_image_digest} \\")
    install_lines.extend(
        [
            "  --namespace gpubox \\",
            "  --create-namespace",
            "```",
        ]
    )

    body_lines: list[str] = [
        "## Highlights",
        *(f"- {line}" for line in highlights),
        "",
        "## Install",
        "",
        "Helm chart repo (GitHub Pages):",
        "",
        *install_lines,
        "",
        "Container images:",
        "",
        f"- `{image_ghcr}`",
        f"- `{image_dockerhub}`",
        "",
        "## Full changelog",
        changelog,
        "",
    ]
    return "\n".join(body_lines)


def main() -> int:
    args = parse_args()
    changelog = load_changelog(Path(args.notes_json))
    override_highlights = load_highlights_file(Path(args.highlights_file)) if args.highlights_file else []

    body = render_body(
        tag=args.tag,
        pages_url=args.pages_url,
        image_ghcr=args.image_ghcr,
        image_dockerhub=args.image_dockerhub,
        install_image_tag=args.install_image_tag,
        install_image_digest=args.install_image_digest,
        changelog=changelog,
        override_highlights=override_highlights,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(body, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
