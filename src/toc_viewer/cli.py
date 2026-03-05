"""CLI application for viewing TOC XML files as a tree."""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Tree


@dataclass(slots=True)
class TocNode:
    """Represents a TOC section node used to populate the UI tree.

    Args:
        label: Text to display for this tree node.
        children: Child TOC section nodes.
    """

    label: str
    children: list["TocNode"] = field(default_factory=list)


def _local_name(tag_name: str) -> str:
    """Return the namespace-stripped local tag name.

    Args:
        tag_name: Raw XML tag name.

    Returns:
        The local (namespace-free) tag name.
    """

    if "}" in tag_name:
        return tag_name.rsplit("}", maxsplit=1)[1]
    return tag_name


def _first_child_text(element: ET.Element, child_name: str) -> str | None:
    """Get text from the first direct child element with a specific local name.

    Args:
        element: XML element to inspect.
        child_name: Expected local tag name.

    Returns:
        Trimmed text when available, otherwise ``None``.
    """

    for child in element:
        if _local_name(child.tag) != child_name:
            continue
        if child.text is None:
            return None
        text = child.text.strip()
        return text or None
    return None


def _parse_section(section_element: ET.Element) -> TocNode:
    """Parse a ``Section`` XML element recursively into a ``TocNode``.

    Args:
        section_element: XML element with local name ``Section``.

    Returns:
        Parsed ``TocNode`` with all nested section children.
    """

    heading = _first_child_text(section_element, "Heading") or "(Untitled Section)"
    section_node = TocNode(label=heading)

    for child in section_element:
        if _local_name(child.tag) == "Section":
            section_node.children.append(_parse_section(child))

    return section_node


def parse_toc_xml(xml_path: Path) -> TocNode:
    """Parse a TOC XML file into a simple tree structure.

    The parser intentionally focuses on the ``TOC``, ``Section``, and ``Heading`` tags.

    Args:
        xml_path: Path to input XML file.

    Returns:
        Root ``TocNode`` representing the XML ``TOC`` element.

    Raises:
        FileNotFoundError: If ``xml_path`` does not exist.
        ValueError: If the root tag is not ``TOC``.
        ET.ParseError: If XML is malformed.
    """

    if not xml_path.exists():
        raise FileNotFoundError(f"XML file not found: {xml_path}")

    tree = ET.parse(xml_path)
    root = tree.getroot()

    if _local_name(root.tag) != "TOC":
        raise ValueError("The XML root element must be TOC.")

    toc_node = TocNode(label="TOC")
    for child in root:
        if _local_name(child.tag) == "Section":
            toc_node.children.append(_parse_section(child))

    return toc_node


def _add_nodes(parent: Tree[None].Node, nodes: list[TocNode]) -> None:
    """Populate a Textual tree node recursively.

    Args:
        parent: Parent Textual tree node.
        nodes: Child nodes to insert under the parent.
    """

    for node in nodes:
        if not node.children:
            parent.add_leaf(node.label, data=None)
            continue

        child = parent.add(node.label, data=None)
        _add_nodes(child, node.children)


def _expand_node_recursively(node: Tree[None].Node) -> None:
    """Expand a tree node and all of its descendants.

    Args:
        node: The tree node from which recursive expansion starts.
    """

    node.expand()
    for child in node.children:
        _expand_node_recursively(child)


def _collapse_node_recursively(node: Tree[None].Node) -> None:
    """Collapse all descendants of a tree node.

    Args:
        node: The tree node from which recursive collapsing starts.
    """

    for child in node.children:
        _collapse_node_recursively(child)
        child.collapse()

    node.collapse()


def _has_expanded_descendant(node: Tree[None].Node) -> bool:
    """Check whether a node has any expanded descendants.

    Args:
        node: The tree node to inspect.

    Returns:
        ``True`` if any descendant is expanded, otherwise ``False``.
    """

    for child in node.children:
        if child.is_expanded:
            return True
        if _has_expanded_descendant(child):
            return True
    return False


class TOCTreeViewer(App):
    """Textual application that renders TOC sections in a tree widget."""

    TITLE = "TOC Tree Viewer"
    BINDINGS = [("q", "quit", "Quit"), ("z", "expand_all", "Fold/Unfold")]

    def __init__(self, toc_root: TocNode) -> None:
        """Initialize the tree viewer application.

        Args:
            toc_root: Parsed root node to visualize.
        """

        super().__init__()
        self._toc_root = toc_root

    def compose(self) -> ComposeResult:
        """Compose top-level UI widgets."""

        yield Header(show_clock=True)
        tree = Tree[None](self._toc_root.label)
        _add_nodes(tree.root, self._toc_root.children)
        tree.root.expand()
        yield tree
        yield Footer()

    def action_expand_all(self) -> None:
        """Toggle expanding or folding all descendants of the highlighted node."""

        tree = self.query_one(Tree)
        node = tree.cursor_node or tree.root

        if _has_expanded_descendant(node):
            _collapse_node_recursively(node)
        else:
            _expand_node_recursively(node)


def _build_argument_parser() -> argparse.ArgumentParser:
    """Create and configure the command-line argument parser."""

    parser = argparse.ArgumentParser(description="Display TOC XML as an interactive tree.")
    parser.add_argument("xml_file", help="Path to the TOC XML file (e.g., compound-toc.xml)")
    return parser


def main() -> None:
    """Run the TOC tree viewer TUI."""

    args = _build_argument_parser().parse_args()
    xml_path = Path(args.xml_file)

    try:
        toc_root = parse_toc_xml(xml_path)
    except (FileNotFoundError, ET.ParseError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    app = TOCTreeViewer(toc_root)
    app.run()
