"""CLI application for viewing TOC XML files as a tree."""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from xml.dom import minidom

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, Static, TextArea, Tree


@dataclass(slots=True)
class TocNode:
    """Represents a TOC section node used to populate the UI tree.

    Args:
        label: Text to display for this tree node.
        children: Child TOC section nodes.
    """

    label: str
    description: str | None = None
    url: str | None = None
    display_controls_xml: str | None = None
    children: list["TocNode"] = field(default_factory=list)


@dataclass(slots=True)
class SectionMetadata:
    """Metadata for a TOC section shown in the detail panel.

    Args:
        description: Section description text.
        url: Section URL.
    """

    label: str
    description: str | None
    url: str | None
    display_controls_xml: str | None


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


def _strip_namespace(tag_name: str) -> str:
    """Strip namespace and prefix from an XML tag or attribute name.

    Args:
        tag_name: Raw XML tag or attribute name.

    Returns:
        The tag name without namespace or prefix.
    """

    name = tag_name
    if "}" in name:
        name = name.rsplit("}", maxsplit=1)[1]
    if ":" in name:
        name = name.split(":", maxsplit=1)[1]
    return name


def _strip_namespaces(element: ET.Element) -> ET.Element:
    """Return a deep copy of an element with namespaces/prefixes stripped.

    Args:
        element: Element to copy and normalize.

    Returns:
        A new element with cleaned tag and attribute names.
    """

    cleaned = ET.Element(_strip_namespace(element.tag))
    cleaned.attrib = {
        _strip_namespace(key): value for key, value in element.attrib.items() if not key.startswith("xmlns")
    }
    cleaned.text = element.text
    cleaned.tail = element.tail
    for child in list(element):
        cleaned.append(_strip_namespaces(child))
    return cleaned


def _pretty_print_xml(element: ET.Element) -> str:
    """Pretty-print an XML element with two-space indentation.

    Args:
        element: Element to serialize.

    Returns:
        Formatted XML string.
    """

    raw_xml = ET.tostring(element, encoding="unicode")
    try:
        dom = minidom.parseString(raw_xml)
    except Exception:
        return raw_xml

    pretty = dom.toprettyxml(indent="  ")
    lines = [line for line in pretty.splitlines() if line.strip()]
    if lines and lines[0].startswith("<?xml"):
        lines = lines[1:]
    return "\n".join(lines)


def _parse_section(section_element: ET.Element) -> TocNode:
    """Parse a ``Section`` XML element recursively into a ``TocNode``.

    Args:
        section_element: XML element with local name ``Section``.

    Returns:
        Parsed ``TocNode`` with all nested section children.
    """

    heading = _first_child_text(section_element, "Heading") or "(Untitled Section)"
    description = _first_child_text(section_element, "Description")
    url = _first_child_text(section_element, "URL")
    display_controls_xml = None
    for child in section_element:
        if _local_name(child.tag) == "DisplayControls":
            cleaned = _strip_namespaces(child)
            display_controls_xml = _pretty_print_xml(cleaned)
            break
    section_node = TocNode(
        label=heading,
        description=description,
        url=url,
        display_controls_xml=display_controls_xml,
    )

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


def _add_nodes(parent: Tree[SectionMetadata | None].Node, nodes: list[TocNode]) -> None:
    """Populate a Textual tree node recursively.

    Args:
        parent: Parent Textual tree node.
        nodes: Child nodes to insert under the parent.
    """

    for node in nodes:
        metadata = SectionMetadata(
            label=node.label,
            description=node.description,
            url=node.url,
            display_controls_xml=node.display_controls_xml,
        )
        if not node.children:
            parent.add_leaf(node.label, data=metadata)
            continue

        child = parent.add(node.label, data=metadata)
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


def _iter_tree_nodes(node: Tree[SectionMetadata | None].Node) -> list[Tree[SectionMetadata | None].Node]:
    """Return a depth-first list of nodes starting from the given node.

    Args:
        node: Root node to traverse.

    Returns:
        List of nodes in depth-first order.
    """

    nodes = [node]
    for child in node.children:
        nodes.extend(_iter_tree_nodes(child))
    return nodes


def _node_label_text(node: Tree[SectionMetadata | None].Node) -> str:
    """Return the plain label text for a tree node.

    Args:
        node: Tree node to read.

    Returns:
        The label text for the node.
    """

    if node.data is not None:
        return node.data.label
    return str(node.label)


def _expand_ancestors(node: Tree[SectionMetadata | None].Node) -> None:
    """Expand all ancestors of a node to ensure it is visible.

    Args:
        node: Tree node whose ancestors should be expanded.
    """

    current = node.parent
    while current is not None:
        current.expand()
        current = current.parent


class TOCTreeViewer(App):
    """Textual application that renders TOC sections in a tree widget."""

    TITLE = "TOC Tree Viewer"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("z", "expand_all", "Fold/Unfold"),
        ("/", "search", "Search"),
    ]
    CSS = """
        Horizontal {
            height: 1fr;
        }

        #toc-tree {
            width: 70%;
        }

        #metadata-panel {
            width: 30%;
            padding: 1;
            border: round $primary;
        }

        #search-input {
            margin-bottom: 1;
        }

        #display-controls {
            height: 1fr;
            margin-top: 1;
        }
    """

    def __init__(self, toc_root: TocNode) -> None:
        """Initialize the tree viewer application.

        Args:
            toc_root: Parsed root node to visualize.
        """

        super().__init__()
        self._toc_root = toc_root
        self._search_query: str | None = None
        self._search_results: list[Tree[SectionMetadata | None].Node] = []
        self._search_index = -1

    def compose(self) -> ComposeResult:
        """Compose top-level UI widgets."""

        yield Header(show_clock=True)
        tree = Tree[SectionMetadata | None](self._toc_root.label, id="toc-tree")
        tree.root.data = SectionMetadata(
            label=self._toc_root.label,
            description=self._toc_root.description,
            url=self._toc_root.url,
            display_controls_xml=self._toc_root.display_controls_xml,
        )
        _add_nodes(tree.root, self._toc_root.children)
        tree.root.expand()
        search_input = Input(
            placeholder="Search...",
            id="search-input",
        )
        metadata_text = Static("", id="metadata-text", markup=True)
        metadata_text.update(self._format_metadata(None))
        display_controls = TextArea("", id="display-controls", language="xml")
        display_controls.read_only = True
        display_controls.text = self._format_display_controls(None)
        with Horizontal():
            yield tree
            with Vertical(id="metadata-panel"):
                yield search_input
                yield metadata_text
                yield display_controls
        yield Footer()

    def action_expand_all(self) -> None:
        """Toggle expanding or folding all descendants of the highlighted node."""

        tree = self.query_one(Tree)
        node = tree.cursor_node or tree.root

        if _has_expanded_descendant(node):
            _collapse_node_recursively(node)
        else:
            _expand_node_recursively(node)

    def action_search(self) -> None:
        """Focus the search input for querying tree nodes."""

        search_input = self.query_one("#search-input", Input)
        search_input.focus()
        search_input.cursor_position = len(search_input.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle submitted search input.

        Args:
            event: Submitted input event.
        """

        if event.input.id != "search-input":
            return

        query = event.value.strip()
        if not query:
            return

        self._run_search(query)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[SectionMetadata | None]) -> None:
        """Update the metadata panel when a tree node is highlighted.

        Args:
            event: Highlight event containing the current node.
        """

        metadata_text = self.query_one("#metadata-text", Static)
        display_controls = self.query_one("#display-controls", TextArea)
        metadata_text.update(self._format_metadata(event.node.data))
        display_controls.text = self._format_display_controls(event.node.data)

    def _run_search(self, query: str) -> None:
        """Search the tree for nodes containing the query and select the next match.

        Args:
            query: Search query string.
        """

        tree = self.query_one("#toc-tree", Tree)
        nodes = _iter_tree_nodes(tree.root)
        lowered = query.casefold()
        matches = [node for node in nodes if lowered in _node_label_text(node).casefold()]

        if not matches:
            return

        if query == self._search_query:
            self._search_index = (self._search_index + 1) % len(matches)
        else:
            self._search_query = query
            self._search_index = 0

        self._search_results = matches
        target = matches[self._search_index]
        _expand_ancestors(target)

        if hasattr(tree, "select_node"):
            tree.select_node(target)
        else:
            tree.cursor_node = target

        tree.refresh()

    @staticmethod
    def _format_metadata(metadata: SectionMetadata | None) -> str:
        """Format metadata for display in the side panel.

        Args:
            metadata: Metadata from the highlighted node.

        Returns:
            Formatted text for the metadata panel.
        """

        label_style = "bold #4ea1ff"
        description_label = f"[{label_style}]Description[/]"
        url_label = f"[{label_style}]URL[/]"

        if metadata is None:
            return f"{description_label}\n(none)\n\n{url_label}\n(none)"
        description = escape(metadata.description or "(none)")
        url = escape(metadata.url or "(none)")
        return f"{description_label}\n{description}\n\n{url_label}\n{url}"

    @staticmethod
    def _format_display_controls(metadata: SectionMetadata | None) -> str:
        """Format display controls XML for the text area.

        Args:
            metadata: Metadata from the highlighted node.

        Returns:
            XML string for the DisplayControls block.
        """

        if metadata is None or not metadata.display_controls_xml:
            return ""
        return metadata.display_controls_xml


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
