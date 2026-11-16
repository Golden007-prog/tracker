"""Offline checks for publishing package install coverage to JSON and HTML."""

import json
import tempfile
import unittest
from pathlib import Path

from scripts import build_lookup, build_pages


class InstallCoverageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.install = {
            "3.14 linux-x86_64": {"status": "wheel", "blockers": []},
            "3.14 win-arm64": {
                "status": "source",
                "blockers": [
                    {"name": "crypto<test>", "version": "2&3", "status": "source", "via": ["demo", "middle>", "crypto<test>"]},
                ],
            },
            "3.14 macos-arm64": {"status": "not_checked", "blockers": []},
            "3.14 musl-aarch64": {
                "status": "missing",
                "blockers": [{"name": "native", "version": None, "status": "missing", "via": ["demo", "native"]}],
            },
        }
        package = {
            "rank": 1, "name": "demo", "version": "1.2", "pure": True,
            "error": None, "results": ["wheel"] * len(self.install),
        }
        self.scan = {
            "schema": 1,
            "generated": "2026-11-16T03:20:00+00:00",
            "wheelgap": "0.11.0",
            "targets": [
                {"label": label, "python": "3.14", "platform": label.split(" ", 1)[1]}
                for label in self.install
            ],
            "packages": [{**package, "install": self.install}, {**package, "name": "older", "rank": 2}],
        }

    def coverage(self):
        source = self.root / "coverage.json"
        source.write_text(json.dumps(self.scan), encoding="utf-8")
        return source

    def test_lookup_preserves_optional_install_results(self):
        output = self.root / "data" / "packages"
        build_lookup.build(self.coverage(), output, root=self.root)
        current = json.loads((output / "demo.json").read_text(encoding="utf-8"))
        older = json.loads((output / "older.json").read_text(encoding="utf-8"))
        self.assertEqual(current["schema"], 1)
        self.assertEqual(current.get("install"), self.install)
        self.assertNotIn("install", older)
        self.assertEqual(current["results"], {label: "wheel" for label in self.install})

    def test_page_shows_target_outcomes_and_escapes_dependency_paths(self):
        output = self.root / "site"
        build_pages.build(self.coverage(), output, root=self.root)
        page = (output / "p" / "demo" / "index.html").read_text(encoding="utf-8")
        older = (output / "p" / "older" / "index.html").read_text(encoding="utf-8")
        self.assertIn('aria-labelledby="install-coverage"', page)
        self.assertIn("Wheels for the whole tree", page)
        self.assertIn("Builds from source", page)
        self.assertIn("Missing distribution", page)
        self.assertIn("Not checked", page)
        self.assertIn("could not resolve the full dependency tree", page)
        self.assertIn('<th scope="row">3.14 win-arm64</th>', page)
        self.assertIn("<code>crypto&lt;test&gt;==2&amp;3</code>", page)
        self.assertIn("<code>demo → middle&gt; → crypto&lt;test&gt;</code>", page)
        self.assertIn("<code>native</code>", page)
        self.assertNotIn("crypto<test>", page)
        self.assertNotIn("installs everywhere", page)
        self.assertNotIn("install-coverage", older)
