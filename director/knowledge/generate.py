"""CLI for deterministic Markdown Knowledge Index generation."""

from __future__ import annotations

import argparse
from pathlib import Path

from index import generate_index


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path.cwd())
    args = parser.parse_args()
    project = args.project.resolve()
    for path in generate_index(project):
        print(path.relative_to(project))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
