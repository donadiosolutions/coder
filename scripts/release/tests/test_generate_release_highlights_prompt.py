#!/usr/bin/env python3
"""Fixture-backed tests for release highlights prompt generation helpers."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "release" / "generate_release_highlights_prompt.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

spec = importlib.util.spec_from_file_location("generate_release_highlights_prompt", SCRIPT_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError("Failed to load generate_release_highlights_prompt module")

module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def load_fixture(name: str):
    with (FIXTURES / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_parse_release_input() -> None:
    repo, tag = module.parse_release_input("v1.2.0")
    assert repo is None
    assert tag == "v1.2.0"

    repo, tag = module.parse_release_input(
        "https://github.com/example/repo/releases/tag/v1.2.0"
    )
    assert repo == "example/repo"
    assert tag == "v1.2.0"


def test_previous_release_selection() -> None:
    releases = load_fixture("highlights-releases.json")

    previous_for_current = module.select_previous_release_tag(releases, "v1.2.0")
    assert previous_for_current == "v1.1.0"

    previous_for_new_tag = module.select_previous_release_tag(releases, "v9.9.9")
    assert previous_for_new_tag == "v1.3.0"


def test_filtering_and_curation() -> None:
    payloads = load_fixture("highlights-commit-details.json")
    curated, stats = module.curate_commit_payloads(payloads, max_patch_chars=120)

    assert stats["total_commits"] == 2
    assert stats["commits_with_shipping_changes"] == 1
    assert stats["commits_ignored_only"] == 1
    assert stats["shipping_files"] == 1
    assert stats["ignored_files"] == 3

    assert len(curated) == 1
    commit = curated[0]
    assert commit["subject"] == "feat(api): add fast status endpoint"
    assert commit["shipping_files"][0]["filename"] == "src/api/status.ts"
    assert "scripts/release/publish.sh" in commit["ignored_files"]


def test_prompt_contract() -> None:
    compare = load_fixture("highlights-compare.json")
    payloads = load_fixture("highlights-commit-details.json")
    curated, stats = module.curate_commit_payloads(payloads, max_patch_chars=200)

    context = {
        "release": {
            "repo": "example/repo",
            "current_tag": "v1.2.0",
            "previous_tag": "v1.1.0",
        },
        "compare_url": compare["html_url"],
        "stats": stats,
        "commits": curated,
    }

    prompt = module.build_prompt(context)

    assert "Return ONLY markdown bullet lines." in prompt
    assert "Output 3 to 6 bullets." in prompt
    assert "Current tag: v1.2.0" in prompt
    assert "Previous release tag: v1.1.0" in prompt
    assert "Ignore release workflow churn" in prompt
    assert "src/api/status.ts" in prompt
    assert "scripts/release/publish.sh" not in prompt


if __name__ == "__main__":
    test_parse_release_input()
    test_previous_release_selection()
    test_filtering_and_curation()
    test_prompt_contract()
    print("Release highlights prompt fixture tests passed.")
