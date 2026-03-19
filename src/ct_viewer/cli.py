"""CLI application for viewing hierarchy XML files as a tree."""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, Select, Static, Tree


@dataclass(slots=True)
class NodeMetadata:
    """Metadata shown for a hierarchy or node in the detail panel.

    Args:
        node_id: Identifier for the current node.
        parent_ids: Parent identifiers for the current node.
        label: Display name for the hierarchy or node.
        description: Long-form description text.
        comments: Additional comments text.
        url: Associated URL.
        source_name: Root hierarchy source name.
        source_id: Root hierarchy source identifier.
        license_note: Root hierarchy license note.
        license_url: Root hierarchy license URL.
        xrefs: Cross-reference values grouped by type.
    """

    node_id: str | None = None
    parent_ids: list[str] = field(default_factory=list)
    label: str = ""
    description: str | None = None
    comments: str | None = None
    url: str | None = None
    source_name: str | None = None
    source_id: str | None = None
    license_note: str | None = None
    license_url: str | None = None
    xrefs: dict[str, list[str]] = field(default_factory=dict)


@dataclass(slots=True)
class TocNode:
    """Represents a hierarchy node used to populate the UI tree.

    Args:
        metadata: Metadata displayed for this tree node.
        children: Child hierarchy nodes.
    """

    metadata: NodeMetadata
    children: list["TocNode"] = field(default_factory=list)


@dataclass(slots=True)
class FlatHierarchyNode:
    """Flat node parsed from XML before parent-child reconstruction.

    Args:
        node_id: Identifier for the node.
        parent_ids: Parent node identifiers.
        metadata: Display metadata for the node.
    """

    node_id: str
    parent_ids: list[str]
    metadata: NodeMetadata


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


def _find_first_child(element: ET.Element, child_name: str) -> ET.Element | None:
    """Return the first direct child element with the given local name.

    Args:
        element: XML element to inspect.
        child_name: Expected local tag name.

    Returns:
        Matching child element or ``None``.
    """

    for child in element:
        if _local_name(child.tag) == child_name:
            return child
    return None


def _find_children(element: ET.Element, child_name: str) -> list[ET.Element]:
    """Return all direct child elements with the given local name.

    Args:
        element: XML element to inspect.
        child_name: Expected local tag name.

    Returns:
        Matching child elements in document order.
    """

    return [child for child in element if _local_name(child.tag) == child_name]


def _extract_string_with_markup_text(string_with_markup: ET.Element) -> str | None:
    """Extract plain text from a ``StringWithMarkup`` element.

    Args:
        string_with_markup: XML element with local name ``StringWithMarkup``.

    Returns:
        Plain text from the nested ``String`` child when available.
    """

    for child in string_with_markup.iter():
        if _local_name(child.tag) != "String":
            continue
        text = "".join(child.itertext()).strip()
        return text or None
    return None


def _extract_information_text(information: ET.Element | None, field_name: str) -> str | None:
    """Extract text from an ``Information`` field using ``StringWithMarkup``.

    Args:
        information: XML element with local name ``Information``.
        field_name: Field name under ``Information`` to read.

    Returns:
        Joined plain-text value for the field or ``None``.
    """

    if information is None:
        return None

    field_element = _find_first_child(information, field_name)
    if field_element is None:
        return None

    values: list[str] = []
    for child in field_element:
        if _local_name(child.tag) != "StringWithMarkup":
            continue
        text = _extract_string_with_markup_text(child)
        if text:
            values.append(text)

    if not values:
        return None
    return "\n\n".join(values)


def _parse_information(information: ET.Element | None) -> dict[str, str | None]:
    """Parse the standard metadata fields from an ``Information`` element.

    Args:
        information: XML element with local name ``Information``.

    Returns:
        Mapping of display fields extracted from the element.
    """

    return {
        "label": _extract_information_text(information, "Name"),
        "description": _extract_information_text(information, "Description"),
        "comments": _extract_information_text(information, "Comments"),
        "url": _first_child_text(information, "URL") if information is not None else None,
    }


def _parse_xrefs(xrefs_element: ET.Element | None) -> dict[str, list[str]]:
    """Parse an ``XRefs`` element into grouped values keyed by xref type.

    Args:
        xrefs_element: XML element with local name ``XRefs``.

    Returns:
        Mapping of xref type names to their values.
    """

    if xrefs_element is None:
        return {}

    grouped_values: dict[str, list[str]] = defaultdict(list)
    for child in xrefs_element:
        child_name = _local_name(child.tag)
        if not child_name.startswith("XRef_"):
            continue

        xref_type = child_name.removeprefix("XRef_")
        if len(child):
            nested_values = [text.strip() for text in child.itertext() if text.strip()]
            if nested_values:
                grouped_values[xref_type].append(" | ".join(nested_values))
            continue

        text = (child.text or "").strip()
        if text:
            grouped_values[xref_type].append(text)

    return {xref_type: values for xref_type, values in grouped_values.items() if values}


def _parse_flat_node(node_element: ET.Element, include_xrefs: bool = True) -> FlatHierarchyNode:
    """Parse a flat ``Node`` XML element.

    Args:
        node_element: XML element with local name ``Node``.
        include_xrefs: Whether to parse the optional ``XRefs`` block.

    Returns:
        Parsed flat node record.

    Raises:
        ValueError: If the node does not contain ``NodeID``.
    """

    node_id = _first_child_text(node_element, "NodeID")
    if not node_id:
        raise ValueError("Each Node element must contain a NodeID.")

    parent_ids = [
        parent_id for parent in _find_children(node_element, "ParentID") if (parent_id := (parent.text or "").strip())
    ]
    information = _find_first_child(node_element, "Information")
    parsed = _parse_information(information)
    xrefs = _parse_xrefs(_find_first_child(node_element, "XRefs")) if include_xrefs else {}
    metadata = NodeMetadata(
        node_id=node_id,
        parent_ids=parent_ids,
        label=parsed["label"] or node_id,
        description=parsed["description"],
        comments=parsed["comments"],
        url=parsed["url"],
        xrefs=xrefs,
    )
    return FlatHierarchyNode(node_id=node_id, parent_ids=parent_ids, metadata=metadata)


def _build_hierarchy_children(
    parent_id: str,
    children_by_parent: dict[str, list[str]],
    nodes_by_id: dict[str, FlatHierarchyNode],
    attached_node_ids: set[str],
    active_path: tuple[str, ...] = (),
) -> list[TocNode]:
    """Construct tree children recursively from flat node records.

    Args:
        parent_id: Parent identifier whose children should be expanded.
        children_by_parent: Mapping of parent IDs to child node IDs.
        nodes_by_id: Parsed flat nodes indexed by ``NodeID``.
        attached_node_ids: Accumulator for nodes reachable from the root hierarchy.
        active_path: Node IDs already present in the current recursion path.

    Returns:
        Hierarchical child nodes in document order.
    """

    children: list[TocNode] = []
    for child_id in children_by_parent.get(parent_id, []):
        if child_id in active_path:
            continue

        record = nodes_by_id[child_id]
        attached_node_ids.add(child_id)
        descendants = _build_hierarchy_children(
            child_id,
            children_by_parent,
            nodes_by_id,
            attached_node_ids,
            active_path + (child_id,),
        )
        children.append(TocNode(metadata=record.metadata, children=descendants))

    return children


def parse_toc_xml(xml_path: Path, include_xrefs: bool = True) -> TocNode:
    """Parse a hierarchy XML file into a simple tree structure.

    Args:
        xml_path: Path to input XML file.
        include_xrefs: Whether to parse node ``XRefs`` values.

    Returns:
        Root ``TocNode`` representing the XML ``Hierarchy`` element.

    Raises:
        FileNotFoundError: If ``xml_path`` does not exist.
        ValueError: If the XML does not contain a ``Hierarchy`` root.
        ET.ParseError: If XML is malformed.
    """

    if not xml_path.exists():
        raise FileNotFoundError(f"XML file not found: {xml_path}")

    tree = ET.parse(xml_path)
    root = tree.getroot()

    root_name = _local_name(root.tag)
    if root_name == "Hierarchies":
        hierarchy_element = _find_first_child(root, "Hierarchy")
    elif root_name == "Hierarchy":
        hierarchy_element = root
    else:
        hierarchy_element = None

    if hierarchy_element is None:
        raise ValueError("The XML must contain a Hierarchy element under the Hierarchies root.")

    root_id = _first_child_text(hierarchy_element, "RootID") or "root"
    information = _find_first_child(hierarchy_element, "Information")
    parsed_root = _parse_information(information)
    root_metadata = NodeMetadata(
        label=parsed_root["label"] or _first_child_text(hierarchy_element, "SourceName") or "Hierarchy",
        description=parsed_root["description"],
        comments=parsed_root["comments"],
        url=parsed_root["url"],
        source_name=_first_child_text(hierarchy_element, "SourceName"),
        source_id=_first_child_text(hierarchy_element, "SourceID"),
        license_note=_first_child_text(hierarchy_element, "LicenseNote"),
        license_url=_first_child_text(hierarchy_element, "LicenseURL"),
        xrefs={},
    )

    flat_nodes: list[FlatHierarchyNode] = []
    for child in hierarchy_element:
        if _local_name(child.tag) == "Node":
            flat_nodes.append(_parse_flat_node(child, include_xrefs=include_xrefs))

    nodes_by_id = {node.node_id: node for node in flat_nodes}
    children_by_parent: dict[str, list[str]] = {}
    for node in flat_nodes:
        parent_ids = node.parent_ids or [root_id]
        for parent_id in parent_ids:
            children_by_parent.setdefault(parent_id, []).append(node.node_id)

    attached_node_ids: set[str] = set()
    root_children = _build_hierarchy_children(root_id, children_by_parent, nodes_by_id, attached_node_ids)

    for node in flat_nodes:
        if node.node_id in attached_node_ids:
            continue
        root_children.append(
            TocNode(
                metadata=node.metadata,
                children=_build_hierarchy_children(
                    node.node_id,
                    children_by_parent,
                    nodes_by_id,
                    attached_node_ids,
                    (node.node_id,),
                ),
            )
        )

    return TocNode(metadata=root_metadata, children=root_children)


def _add_nodes(parent: Tree[NodeMetadata | None].Node, nodes: list[TocNode]) -> None:
    """Populate a Textual tree node recursively.

    Args:
        parent: Parent Textual tree node.
        nodes: Child nodes to insert under the parent.
    """

    for node in nodes:
        if not node.children:
            parent.add_leaf(node.metadata.label, data=node.metadata)
            continue

        child = parent.add(node.metadata.label, data=node.metadata)
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


def _iter_tree_nodes(node: Tree[NodeMetadata | None].Node) -> list[Tree[NodeMetadata | None].Node]:
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


def _node_label_text(node: Tree[NodeMetadata | None].Node) -> str:
    """Return the plain label text for a tree node.

    Args:
        node: Tree node to read.

    Returns:
        The label text for the node.
    """

    if node.data is not None:
        return node.data.label
    return str(node.label)


def _expand_ancestors(node: Tree[NodeMetadata | None].Node) -> None:
    """Expand all ancestors of a node to ensure it is visible.

    Args:
        node: Tree node whose ancestors should be expanded.
    """

    current = node.parent
    while current is not None:
        current.expand()
        current = current.parent


class ClassificationViewer(App):
    """Textual application that renders hierarchy nodes in a tree widget."""

    TITLE = "Classification Viewer"
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

        #metadata-text {
            margin-bottom: 1;
        }

        #xref-select {
            height: 3;
            margin-bottom: 1;
        }

        #xref-values-container {
            height: 1fr;
            border: round $surface;
            padding: 0 1;
        }
    """

    def __init__(self, toc_root: TocNode, include_xrefs: bool = True) -> None:
        """Initialize the tree viewer application.

        Args:
            toc_root: Parsed root node to visualize.
            include_xrefs: Whether xref widgets should be shown.
        """

        super().__init__()
        self._toc_root = toc_root
        self._include_xrefs = include_xrefs
        self._search_query: str | None = None
        self._search_results: list[Tree[NodeMetadata | None].Node] = []
        self._search_index = -1
        self._current_metadata: NodeMetadata | None = toc_root.metadata

    def compose(self) -> ComposeResult:
        """Compose top-level UI widgets."""
        # Header
        yield Header(show_clock=True)

        # Tree
        tree = Tree[NodeMetadata | None](self._toc_root.metadata.label, id="toc-tree")
        tree.root.data = self._toc_root.metadata
        _add_nodes(tree.root, self._toc_root.children)
        tree.root.expand()

        # Search
        search_input = Input(
            placeholder="Search...",
            id="search-input",
        )

        # Description and URL
        metadata_text = Static("", id="metadata-text", markup=True)
        metadata_text.update(self._format_metadata(self._toc_root.metadata))

        # Layout
        with Horizontal():
            yield tree
            with Vertical(id="metadata-panel"):
                yield search_input
                yield metadata_text
                if self._include_xrefs:
                    xref_select = Select[str](
                        options=[],
                        prompt="XRef Type",
                        allow_blank=True,
                        id="xref-select",
                    )
                    xref_values = Static("", id="xref-values", markup=True)
                    xref_values.update(self._format_xref_values(self._toc_root.metadata, None))
                    yield xref_select
                    with VerticalScroll(id="xref-values-container"):
                        yield xref_values

        # Footer
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

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[NodeMetadata | None]) -> None:
        """Update the metadata panel when a tree node is highlighted.

        Args:
            event: Highlight event containing the current node.
        """

        metadata_text = self.query_one("#metadata-text", Static)
        metadata = event.node.data
        metadata_text.update(self._format_metadata(metadata))
        self._current_metadata = metadata
        if self._include_xrefs:
            self._update_xref_controls(metadata)

    def on_select_changed(self, event: Select.Changed) -> None:
        """Update visible xref values when the selected xref type changes.

        Args:
            event: Select change event.
        """

        if not self._include_xrefs:
            return

        if event.control.id != "xref-select":
            return

        xref_values = self.query_one("#xref-values", Static)
        selected_type = None if event.value == Select.BLANK else str(event.value)
        xref_values.update(self._format_xref_values(self._current_metadata, selected_type))

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
    def _format_metadata(metadata: NodeMetadata | None) -> str:
        """Format metadata for display in the side panel.

        Args:
            metadata: Metadata from the highlighted node.

        Returns:
            Formatted text for the metadata panel.
        """

        if metadata is None:
            return ""

        label_style = "bold #4ea1ff"
        details: list[tuple[str, str | None]] = [
            ("NodeID", metadata.node_id),
            ("ParentID", "\n".join(metadata.parent_ids) if metadata.parent_ids else None),
            ("Name", metadata.label),
            ("Description", metadata.description),
            ("Comments", metadata.comments),
            ("URL", metadata.url),
        ]

        if any(
            value is not None
            for value in (
                metadata.source_name,
                metadata.source_id,
                metadata.license_note,
                metadata.license_url,
            )
        ):
            details.extend(
                [
                    ("SourceName", metadata.source_name),
                    ("SourceID", metadata.source_id),
                    ("LicenseNote", metadata.license_note),
                    ("LicenseURL", metadata.license_url),
                ]
            )

        visible_details = [(field_name, value) for field_name, value in details if value]
        if not visible_details:
            return ""

        return "\n\n".join(f"[{label_style}]{field_name}[/]\n{escape(value)}" for field_name, value in visible_details)

    def _update_xref_controls(self, metadata: NodeMetadata | None) -> None:
        """Refresh the xref selector and values panel for the highlighted node.

        Args:
            metadata: Metadata for the highlighted tree node.
        """

        xref_select = self.query_one("#xref-select", Select)
        xref_values = self.query_one("#xref-values", Static)
        options = [(xref_type, xref_type) for xref_type in sorted(metadata.xrefs)] if metadata else []
        xref_select.set_options(options)
        xref_select.prompt = "XRef Type" if options else "No XRefs"

        if options:
            first_type = options[0][1]
            xref_select.value = first_type
            xref_values.update(self._format_xref_values(metadata, first_type))
            return

        xref_select.clear()
        xref_values.update("")

    @staticmethod
    def _format_xref_values(metadata: NodeMetadata | None, xref_type: str | None) -> str:
        """Format the selected xref values for the side panel.

        Args:
            metadata: Metadata from the highlighted node.
            xref_type: Selected xref type.

        Returns:
            Formatted xref values text.
        """

        if metadata is None or not xref_type:
            return ""

        values = metadata.xrefs.get(xref_type, [])
        if not values:
            return ""

        label_style = "bold #4ea1ff"
        escaped_values = "\n".join(escape(value) for value in values)
        return f"[{label_style}]{escape(xref_type)}[/]\n{escaped_values}"


def _build_argument_parser() -> argparse.ArgumentParser:
    """Create and configure the command-line argument parser."""

    parser = argparse.ArgumentParser(description="Display classification XML as an interactive tree.")
    parser.add_argument("xml_file", help="Path to the classification XML file")
    parser.add_argument(
        "-X",
        "--exclude-xrefs",
        action="store_true",
        help="Skip reading XRefs to improve XML parsing performance",
    )
    return parser


def main() -> None:
    """Run the classification viewer TUI."""

    args = _build_argument_parser().parse_args()
    xml_path = Path(args.xml_file)

    try:
        toc_root = parse_toc_xml(xml_path, include_xrefs=not args.exclude_xrefs)
    except (FileNotFoundError, ET.ParseError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    app = ClassificationViewer(toc_root, include_xrefs=not args.exclude_xrefs)
    app.run()
