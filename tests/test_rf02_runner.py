#!/usr/bin/env python3
"""
tests/test_rf02_runner.py — Unit tests for the differential testing runner.
"""

import tempfile
import unittest
from pathlib import Path

from scripts.rf02_differential_runner import (
    file_sha256,
    parse_rapidflow_output,
    verify_provenance,
)


class TestDifferentialRunner(unittest.TestCase):

    def test_file_sha256(self):
        with tempfile.NamedTemporaryFile("wb", delete=False) as f:
            f.write(b"hello world\n")
            f_path = Path(f.name)
        try:
            # sha256 of b"hello world\n"
            h = file_sha256(f_path)
            self.assertEqual(h, "a948904f2f0f479b8f8197694b30184b0d2ed1c1cd2a1ec0fb85d299a192a447")
        finally:
            if f_path.exists():
                f_path.unlink()

    def test_parse_rapidflow_output_valid(self):
        stdout = (
            "Execution Summary:\n"
            "Updates num: 3\n"
            "Edge process count: 3\n"
            "Result count: 42\n"
            "Done.\n"
        )
        parsed = parse_rapidflow_output(stdout)
        self.assertEqual(parsed["updates_num"], 3)
        self.assertEqual(parsed["edge_process_count"], 3)
        self.assertEqual(parsed["result_count"], 42)

    def test_parse_rapidflow_output_missing_result(self):
        stdout = (
            "Updates num: 1\n"
            "Edge process count: 1\n"
        )
        with self.assertRaises(ValueError):
            parse_rapidflow_output(stdout)

    def test_parse_rapidflow_output_timeout(self):
        stdout = (
            "Updates num: 1\n"
            "Edge process count: 1\n"
            "Result count: 0\n"
            "Time out...\n"
        )
        with self.assertRaises(RuntimeError):
            parse_rapidflow_output(stdout)

    def test_verify_provenance_workspace(self):
        workspace_root = Path(__file__).resolve().parent.parent
        ok, msg = verify_provenance(workspace_root)
        self.assertTrue(ok, msg)


if __name__ == "__main__":
    unittest.main()
