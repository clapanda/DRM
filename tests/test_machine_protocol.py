from __future__ import annotations

import contextlib
import io
import json
import shutil
import unittest
import uuid
from pathlib import Path

from docx import Document

from drm.cli import main
from drm.result_protocol import OperationResult
from tools.create_test_fixture import create_fixture


class MachineProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        local_temp = Path(__file__).parent / ".tmp"
        local_temp.mkdir(exist_ok=True)
        self.root = local_temp / f"protocol-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.source = create_fixture(self.root / "source.docx")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_search_result_conforms_to_uniform_protocol(self) -> None:
        result_file = self.root / "search-result.json"
        details = self.root / "search-details.json"
        exit_code = self._main(
            [
                "search",
                str(self.source),
                "FE250",
                "--profile",
                "ofs-alm",
                "--output",
                str(details),
                "--result-file",
                str(result_file),
            ]
        )
        self.assertEqual(exit_code, 0)
        result = OperationResult.model_validate_json(result_file.read_text(encoding="utf-8"))
        self.assertEqual(result.operation, "search")
        self.assertEqual(result.status.value, "success")
        self.assertGreaterEqual(result.findings[0]["match_count"], 2)
        self.assertTrue(details.exists())

    def test_compare_is_read_only_and_reports_semantic_change(self) -> None:
        right = self.root / "right.docx"
        shutil.copy2(self.source, right)
        document = Document(right)
        paragraph = next(p for p in document.paragraphs if p.text.startswith("FE250 重定价前余额"))
        paragraph.text = "FE250 重定价前余额：合成比较版本。"
        document.save(right)
        source_before = self.source.read_bytes()
        result_file = self.root / "compare-result.json"
        exit_code = self._main(
            [
                "compare",
                str(self.source),
                str(right),
                "--output",
                str(self.root / "compare-details.json"),
                "--profile",
                "ofs-alm",
                "--result-file",
                str(result_file),
            ]
        )
        self.assertEqual(exit_code, 0)
        result = OperationResult.model_validate_json(result_file.read_text(encoding="utf-8"))
        self.assertGreater(result.findings[0]["semantic_change_group_count"], 0)
        self.assertEqual(self.source.read_bytes(), source_before)

    def test_failed_call_still_writes_uniform_result(self) -> None:
        result_file = self.root / "failure-result.json"
        exit_code = self._main(
            [
                "search",
                str(self.root / "missing.docx"),
                "FE250",
                "--result-file",
                str(result_file),
            ]
        )
        self.assertEqual(exit_code, 2)
        result = OperationResult.model_validate_json(result_file.read_text(encoding="utf-8"))
        self.assertEqual(result.status.value, "failed")
        self.assertTrue(result.errors)

    @staticmethod
    def _main(arguments: list[str]) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(arguments)


if __name__ == "__main__":
    unittest.main()
