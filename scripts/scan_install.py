"""Add pinned install-tree coverage to a tracker snapshot using wheelgap's released CLI."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if __package__:
    from .build_lab import TARGETS as LAB_TARGETS
else:
    from build_lab import TARGETS as LAB_TARGETS

MAIN_TARGETS = {"3.14 linux-x86_64", "3.14 linux-aarch64", "3.14 macos-arm64", "3.14 macos-x86_64"}
NAME = re.compile(r"[a-zA-Z0-9]+(?:[-_.]+[a-zA-Z0-9]+)*")
VERSION = re.compile(r"[0-9][a-zA-Z0-9.!+_-]*")
ORDER = {"wheel": 0, "source": 1, "missing": 2}
TOP = 500
WORKERS = 4
PACKAGE_TIMEOUT = 25
SCAN_TIMEOUT = 20 * 60


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def not_checked() -> dict:
    return {"status": "not_checked", "blockers": []}


def coverage(report: dict, name: str, version: str, label: str) -> dict:
    """A target is checked only when the pinned root and every needed result are known."""
    rows = report.get("packages")
    if not isinstance(rows, list):
        return not_checked()
    root = False
    status = "wheel"
    blockers = []
    for row in rows:
        package = row.get("name")
        release = row.get("version")
        result = row.get("results", {}).get(label)
        if not isinstance(package, str) or not NAME.fullmatch(package):
            return not_checked()
        if result == "n/a":
            continue
        if result not in ORDER:
            return not_checked()
        if canonical(package) == canonical(name):
            if release != version:
                return not_checked()
            root = True
        if ORDER[result] > ORDER[status]:
            status = result
        if result != "wheel":
            via = row.get("via", {}).get(label, [])
            blockers.append({"name": package, "version": release, "status": result, "via": via})
    if not root:
        return not_checked()
    return {"status": status, "blockers": blockers}


def check(package: dict, targets: list[dict], deadline: float) -> dict:
    results = {target["label"]: not_checked() for target in targets}
    name, version = package["name"], package["version"]
    if package.get("error") or not NAME.fullmatch(name) or not isinstance(version, str) or not VERSION.fullmatch(version):
        return results
    groups: dict[tuple[str, str, str, str], list[dict]] = {}
    for target in targets:
        key = (target["python"], target["glibc"], target["musl"], target["macos"])
        groups.setdefault(key, []).append(target)
    for (python, glibc, musl, macos), group in groups.items():
        timeout = min(PACKAGE_TIMEOUT, deadline - time.monotonic())
        if timeout <= 0:
            break
        platforms = ",".join(target["platform"] for target in group)
        try:
            process = subprocess.run(
                [sys.executable, "-m", "wheelgap", f"{name}=={version}", "--deps", "--all", "--explain", "--json",
                 "--no-notes", "-p", python, "-t", platforms, "--glibc", glibc, "--musl", musl, "--macos", macos],
                capture_output=True, encoding="utf-8", timeout=timeout, check=False,
            )
            # 1 is a valid wheelgap report with gaps. 2 includes unsatisfied resolution.
            if process.returncode not in (0, 1):
                continue
            report = json.loads(process.stdout)
            labels = {target["platform"]: target["label"] for target in report["targets"] if target["python"] == python}
            for target in group:
                label = labels.get(target["platform"])
                if label is not None:
                    results[target["label"]] = coverage(report, name, version, label)
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError, AttributeError):
            continue
    return results


def scan(path: Path, top: int) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1:
        raise ValueError("expected coverage schema 1")
    targets = [t for t in data["targets"] if t["label"] in LAB_TARGETS or t["label"] in MAIN_TARGETS]
    if not targets:
        raise ValueError("no install coverage targets in the snapshot")
    packages = sorted(data["packages"], key=lambda package: package["rank"])[:top]
    deadline = time.monotonic() + SCAN_TIMEOUT
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for package, results in zip(packages, pool.map(lambda p: check(p, targets, deadline), packages), strict=True):
            package["install"] = results
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        output.write(json.dumps(data, separators=(",", ":")) + "\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return len(packages)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage", type=Path)
    parser.add_argument("--top", type=int, choices=range(1, TOP + 1), default=TOP, metavar=f"1–{TOP}")
    args = parser.parse_args()
    print(f"install coverage: {scan(args.coverage, args.top)} packages")
