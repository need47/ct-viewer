"""Tests for ct-viewer CLI flat-output behavior."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ct_viewer.cli import Node, NodeMetadata, main


class CliOutputTests(unittest.TestCase):
    """Verify flat output targets stdout and files correctly."""

    def _build_root(self) -> Node:
        """Create a small parsed hierarchy for CLI tests.

        Returns:
            Node: Root hierarchy node.
        """

        return Node(
            metadata=NodeMetadata(node_id="root", label="Root"),
            children=[Node(metadata=NodeMetadata(node_id="child", parent_ids=["root"], label="Child"))],
        )

    def test_main_writes_to_stdout_when_output_flag_has_no_path(self) -> None:
        """Treat bare ``--output`` as a request to write to stdout."""

        stdout = io.StringIO()
        with (
            patch("ct_viewer.cli._build_argument_parser") as parser_factory,
            patch("ct_viewer.cli.parse_toc_xml", return_value=self._build_root()),
            patch("ct_viewer.cli.sys.stdout", stdout),
        ):
            parser_factory.return_value.parse_args.return_value = type(
                "Args",
                (),
                {"xml_file": "input.xml", "exclude_xrefs": False, "output": "-"},
            )()

            result = main()

        self.assertIsNone(result)
        self.assertEqual(stdout.getvalue(), "root\t\tRoot\nchild\troot\tChild\n")

    def test_main_writes_to_stdout_when_output_is_dash(self) -> None:
        """Treat explicit ``--output -`` as standard output."""

        stdout = io.StringIO()
        with (
            patch("ct_viewer.cli._build_argument_parser") as parser_factory,
            patch("ct_viewer.cli.parse_toc_xml", return_value=self._build_root()),
            patch("ct_viewer.cli.sys.stdout", stdout),
        ):
            parser_factory.return_value.parse_args.return_value = type(
                "Args",
                (),
                {"xml_file": "input.xml", "exclude_xrefs": False, "output": "-"},
            )()

            main()

        self.assertEqual(stdout.getvalue(), "root\t\tRoot\nchild\troot\tChild\n")

    def test_main_writes_to_file_when_output_path_is_provided(self) -> None:
        """Keep file output behavior unchanged for explicit output paths."""

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "tree.flat"
            with (
                patch("ct_viewer.cli._build_argument_parser") as parser_factory,
                patch("ct_viewer.cli.parse_toc_xml", return_value=self._build_root()),
            ):
                parser_factory.return_value.parse_args.return_value = type(
                    "Args",
                    (),
                    {
                        "xml_file": "input.xml",
                        "exclude_xrefs": False,
                        "output": str(output_path),
                    },
                )()

                main()

            self.assertEqual(output_path.read_text(encoding="utf-8"), "root\t\tRoot\nchild\troot\tChild\n")


if __name__ == "__main__":
    unittest.main()
