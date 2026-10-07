"""Project hygiene: one version everywhere, ASCII-only code, no long dashes anywhere."""
import re
import tomllib
from pathlib import Path

import wifilab

ROOT = Path(__file__).resolve().parents[1]
CODE = {".py", ".js", ".mjs", ".sh", ".c", ".css", ".html", ".toml", ".yml", ".yaml", ".csv"}
TEXT = CODE | {".md", ".json", ".txt", ".example"}
SKIP = {".git", ".venv", "build", "dist", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}
LONG_DASH = chr(0x2014)


def files(exts):
    for p in ROOT.rglob("*"):
        if p.is_file() and not SKIP & set(p.relative_to(ROOT).parts) and (p.suffix in exts or p.name in exts):
            yield p


def test_versions_agree():
    v = (ROOT / "VERSION").read_text().strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", v)
    assert wifilab.__version__ == v
    assert tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"] == v


def test_code_is_ascii():
    bad = [str(p.relative_to(ROOT)) for p in files(CODE) if not p.read_bytes().isascii()]
    assert bad == []


def test_no_long_dash():
    bad = [str(p.relative_to(ROOT)) for p in files(TEXT | {"LICENSE", ".gitignore"}) if LONG_DASH in p.read_text("utf-8")]
    assert bad == []


def test_modules_import():
    from wifilab import analysis, cli, fieldtab, projects, report, web  # noqa: F401
