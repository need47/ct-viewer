"""Tests for ct-viewer CLI flat parsing and output behavior."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ct_viewer.cli import Node, NodeMetadata, main, read_flat


class FlatHierarchyParsingTests(unittest.TestCase):
    """Verify flat hierarchy parsing behavior."""

    def test_parse_flat_hierarchy_builds_tree_and_xrefs(self) -> None:
        """Parse tab-delimited rows with multiple parents and xrefs."""

        flat_text = "\n".join(
            [
                "root\t\tRoot\tPMID:1|DOI:abc",
                "group\troot\tGroup",
                "child\troot|group\tChild\tCID:2244|CID:2245|PMID:1234",
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            flat_path = Path(temp_dir) / "tree.flat"
            flat_path.write_text(flat_text + "\n", encoding="utf-8")

            root = read_flat(flat_path)

        self.assertEqual(root.metadata.node_id, "root")
        self.assertEqual(root.metadata.label, "Root")
        self.assertEqual(root.metadata.xrefs, {"PMID": ["1"], "DOI": ["abc"]})
        self.assertEqual([child.metadata.node_id for child in root.children], ["group", "child"])
        self.assertEqual(root.children[0].children[0].metadata.node_id, "child")
        self.assertEqual(root.children[1].metadata.xrefs, {"CID": ["2244", "2245"], "PMID": ["1234"]})

    def test_parse_flat_hierarchy_creates_synthetic_root_for_multiple_roots(self) -> None:
        """Wrap multiple parentless nodes in a synthetic hierarchy root."""

        flat_text = "left\t\tLeft\nright\t\tRight\n"

        with tempfile.TemporaryDirectory() as temp_dir:
            flat_path = Path(temp_dir) / "forest.flat"
            flat_path.write_text(flat_text, encoding="utf-8")

            root = read_flat(flat_path)

        self.assertEqual(root.metadata.node_id, "root")
        self.assertEqual(root.metadata.label, "Hierarchy")
        self.assertEqual([child.metadata.node_id for child in root.children], ["left", "right"])

    def test_parse_flat_hierarchy_rejects_invalid_xref(self) -> None:
        """Raise for malformed xref values that do not use ``Type:Value``."""

        with tempfile.TemporaryDirectory() as temp_dir:
            flat_path = Path(temp_dir) / "invalid.flat"
            flat_path.write_text("root\t\tRoot\tPMID\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Invalid XRef value"):
                read_flat(flat_path)


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
                {"file": "input.xml", "flat": False, "exclude_xrefs": False, "output": "-"},
            )()

            result = main()

        self.assertIsNone(result)
        self.assertEqual(stdout.getvalue(), "root\t\tRoot\nchild\troot\tChild\n")

    def test_output_flat_includes_xrefs_column_when_present(self) -> None:
        """Append the optional fourth field only for nodes with xrefs."""

        root = Node(
            metadata=NodeMetadata(
                node_id="root",
                label="Root",
                xrefs={"PMID": ["1"], "DOI": ["abc"]},
            ),
            children=[
                Node(
                    metadata=NodeMetadata(
                        node_id="child",
                        parent_ids=["root"],
                        label="Child",
                    )
                )
            ],
        )

        stdout = io.StringIO()
        with patch("ct_viewer.cli.sys.stdout", stdout):
            root.write_flat()

        self.assertEqual(stdout.getvalue(), "root\t\tRoot\tPMID:1|DOI:abc\nchild\troot\tChild\n")

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
                {"file": "input.xml", "flat": False, "exclude_xrefs": False, "output": "-"},
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
                        "file": "input.xml",
                        "flat": False,
                        "exclude_xrefs": False,
                        "output": str(output_path),
                    },
                )()

                main()

            self.assertEqual(output_path.read_text(encoding="utf-8"), "root\t\tRoot\nchild\troot\tChild\n")


if __name__ == "__main__":
    unittest.main()
