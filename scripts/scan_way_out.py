"""Add release suggestions for known wheel gaps in the public PyPI tracker scan."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

TOP = 5_000
DEADLINE = 300  # seconds across all packages, with wheelgap's bounded per-request lookups
GAPS = {"source", "missing"}


def scan(path: Path) -> None:
    from packaging.utils import InvalidName, canonicalize_name
    from packaging.version import InvalidVersion, Version
    from wheelgap import origin
    from wheelgap.scan import TOP_PACKAGES_URL
    from wheelgap.suggest import look_up_all
    from wheelgap.targets import build_targets

    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1:
        raise ValueError("expected coverage schema 1")
    if (
        data.get("top_packages", {}).get("source") != TOP_PACKAGES_URL
        or origin.other_index() is not None
        or origin.index_env() is not None
    ):
        raise ValueError("release suggestions require a public PyPI snapshot and index settings")

    targets = [
        build_targets([row["python"]], [row["platform"]],
                      glibc=row["glibc"], musl=row["musl"], macos=row["macos"])[0]
        for row in data["targets"]
    ]
    labels = {target: row["label"] for target, row in zip(targets, data["targets"], strict=True)}
    for package in data["packages"]:
        package.pop("way_out", None)
    packages_by_name = {}
    wanted = {}
    for package in sorted(data["packages"], key=lambda row: row["rank"])[:TOP]:
        name, release = package["name"], package["version"]
        if package.get("error") or not isinstance(name, str) or not isinstance(release, str):
            continue
        results = dict(zip(targets, package["results"], strict=True))
        gaps = [target for target, status in results.items() if status in GAPS]
        if not gaps:
            continue
        try:
            key = canonicalize_name(name, validate=True)
            Version(release)
        except (InvalidName, InvalidVersion):
            continue
        if key in packages_by_name:
            continue
        keep = [target for target, status in results.items() if status == "wheel"]
        packages_by_name[key] = package
        wanted[key] = [(release, gaps, keep)]

    # The scan's JSON has statuses, not the release histories. Fetch at most once per affected project.
    found, failed = look_up_all(wanted, deadline=DEADLINE)
    for key, (answer,) in found.items():
        hints = {
            labels[target]: {"release": release, "kind": kind}
            for kind, suggestions in (("newer", answer.found), ("older", answer.last))
            for target, release in suggestions.items()
        }
        if hints:
            packages_by_name[key]["way_out"] = hints

    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(json.dumps(data, separators=(",", ":")) + "\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"wheel suggestions: {len(found)} of {len(wanted)} projects checked, {len(failed)} unavailable")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage", type=Path)
    scan(parser.parse_args().coverage)
