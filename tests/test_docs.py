"""Link checks for the Markdown docs - no network, just the files on disk.

The README doubles as the PyPI project page, and PyPI only receives that
one file: a relative link like `docs/REFERENCE.md` resolves to a 404 under
pypi.org/project/... there. So the README may only use absolute URLs or
in-page `#anchors` (PyPI rewrites those to its own heading ids). On top of
that, every link that points into this repository - relative, or an
absolute github.com/.../blob/master/... URL - must name a file that
exists and, for a Markdown target, a heading that exists in it.
"""

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REPO_BLOB_URL = "https://github.com/danyk20/tutti-scraper/blob/master/"

LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
FENCED_CODE_RE = re.compile(r"^```.*?^```", re.S | re.M)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)

MARKDOWN_FILES = sorted(
    p for p in [*ROOT.glob("*.md"), *ROOT.glob("docs/**/*.md"), *ROOT.glob(".github/**/*.md")] if p.is_file()
)


def _pypi_readme():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return ROOT / pyproject["project"]["readme"]


def _links(path):
    """Every Markdown link/image target in `path`, ignoring code."""
    text = FENCED_CODE_RE.sub("", path.read_text())
    # Blank out inline code but keep the link around it, so [`responses`](url)
    # still counts as a link while `[x](y)` inside backticks doesn't.
    text = INLINE_CODE_RE.sub("code", text)
    return LINK_RE.findall(text)


def _slug(heading):
    """GitHub's heading anchor: lowercase, punctuation dropped, spaces to hyphens."""
    text = heading.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _anchors(path):
    text = FENCED_CODE_RE.sub("", path.read_text())
    return {_slug(h) for h in HEADING_RE.findall(text)}


def _local_target(source, target):
    """Map a link to (file, anchor) if it points into this repo, else None."""
    if target.startswith(REPO_BLOB_URL):
        target = target[len(REPO_BLOB_URL) :]
        base = ROOT
    elif re.match(r"^[a-z][a-z0-9+.-]*:", target):
        return None  # external URL (https:, mailto:, ...)
    else:
        base = source.parent
    file_part, _, anchor = target.partition("#")
    file = (base / file_part).resolve() if file_part else source
    return file, anchor


def test_markdown_files_are_found():
    assert _pypi_readme() in MARKDOWN_FILES
    assert ROOT / "docs" / "REFERENCE.md" in MARKDOWN_FILES


def test_pypi_readme_has_no_relative_file_links():
    readme = _pypi_readme()
    relative = [t for t in _links(readme) if not t.startswith(("https://", "http://", "#"))]

    assert relative == [], (
        f"{readme.name} is rendered on PyPI, where relative links 404 - use {REPO_BLOB_URL}<path> instead: {relative}"
    )


@pytest.mark.parametrize("path", MARKDOWN_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_links_into_this_repo_resolve(path):
    broken = []
    for target in _links(path):
        local = _local_target(path, target)
        if local is None:
            continue
        file, anchor = local
        if not file.exists():
            broken.append(f"{target} (no such file)")
        elif anchor and file.suffix == ".md" and anchor not in _anchors(file):
            broken.append(f"{target} (no heading #{anchor} in {file.relative_to(ROOT)})")

    assert broken == []


@pytest.mark.parametrize(
    "heading, slug",
    [
        ("Interchangeability with autoscout24-scraper", "interchangeability-with-autoscout24-scraper"),
        ("`scrape()` signature", "scrape-signature"),
        ("`ScrapeResult` — the return value", "scraperesult--the-return-value"),
        ("Group IDs — not usable as a filter", "group-ids--not-usable-as-a-filter"),
    ],
)
def test_slug_matches_github_anchor_rules(heading, slug):
    assert _slug(heading) == slug
