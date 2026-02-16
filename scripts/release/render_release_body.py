#!/usr/bin/env python3
"""Render deterministic release notes for GitHub Releases."""

from __future__ import annotations

import argparse
import json
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
        "--notes-json",
        required=True,
        help="Path to JSON payload returned by repos.generateReleaseNotes",
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


def build_highlights(tag: str, changelog: str) -> list[str]:
    highlights = extract_change_bullets(changelog)
    if highlights:
        return highlights
    return [f"Release `{tag}` includes updates described in the full changelog below."]


def render_body(
    tag: str,
    pages_url: str,
    image_ghcr: str,
    image_dockerhub: str,
    changelog: str,
) -> str:
    highlights = build_highlights(tag, changelog)

    body_lines: list[str] = [
        "## Highlights",
        *(f"- {line}" for line in highlights),
        "",
        "## Install",
        "",
        "Helm chart repo (GitHub Pages):",
        "",
        "```bash",
        f"helm repo add gpubox {pages_url}",
        "helm repo update",
        "",
        "helm upgrade --install gpubox gpubox/gpubox \\",
        "  --namespace gpubox \\",
        "  --create-namespace",
        "```",
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

    body = render_body(
        tag=args.tag,
        pages_url=args.pages_url,
        image_ghcr=args.image_ghcr,
        image_dockerhub=args.image_dockerhub,
        changelog=changelog,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(body, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
