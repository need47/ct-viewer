"""CLI application for viewing hierarchy XML files as a tree."""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

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
class Node:
    """Represents a hierarchy node used to populate the UI tree.

    Args:
        metadata: Metadata displayed for this tree node.
        children: Child hierarchy nodes.
    """

    metadata: NodeMetadata
    children: list["Node"] = field(default_factory=list)

    def write_flat(self, output_path: Path | None = None) -> None:
        """Write the hierarchy as flat tab-delimited rows.

        Args:
            output_path: Destination path. When ``None``, write to standard
                output.
        """

        text = "\n".join(self._iter_flat_lines()) + "\n"
        if output_path is None:
            sys.stdout.write(text)
            return

        output_path.write_text(text, encoding="utf-8")

    def _iter_flat_lines(self) -> list[str]:
        """Render the node subtree in pre-order flat rows.

        Returns:
            list[str]: One tab-delimited row per node.
        """

        node_id = self.metadata.node_id or ""
        parent_ids = "|".join(self.metadata.parent_ids)
        xrefs = _serialize_flat_xrefs(self.metadata.xrefs)
        fields = [node_id, parent_ids, self.metadata.label]
        if xrefs:
            fields.append(xrefs)

        lines = ["\t".join(fields)]
        for child in self.children:
            lines.extend(child._iter_flat_lines())

        return lines


@dataclass(slots=True)
class FlatNode:
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


def _parse_flat_node(node_element: ET.Element, include_xrefs: bool = True) -> FlatNode:
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
    return FlatNode(node_id=node_id, parent_ids=parent_ids, metadata=metadata)


def _parse_root_metadata(hierarchy_element: ET.Element) -> tuple[str, NodeMetadata]:
    """Parse metadata for the XML ``Hierarchy`` root element.

    Args:
        hierarchy_element: XML element with local name ``Hierarchy``.

    Returns:
        A tuple containing the root identifier and display metadata.
    """

    root_id = _first_child_text(hierarchy_element, "RootID") or "root"
    information = _find_first_child(hierarchy_element, "Information")
    parsed_root = _parse_information(information)
    root_metadata = NodeMetadata(
        node_id=root_id,
        parent_ids=[],
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
    return root_id, root_metadata


def _stream_hierarchy(xml_path: Path, include_xrefs: bool) -> tuple[str, NodeMetadata, list[FlatNode]]:
    """Stream the first hierarchy from XML while releasing parsed node elements.

    Args:
        xml_path: Path to input XML file.
        include_xrefs: Whether to parse node ``XRefs`` values.

    Returns:
        The root identifier, root metadata, and parsed flat nodes.

    Raises:
        ValueError: If the XML does not contain a ``Hierarchy`` element.
        ET.ParseError: If the XML is malformed.
    """

    flat_nodes: list[FlatNode] = []
    element_stack: list[ET.Element] = []
    target_hierarchy: ET.Element | None = None
    root_id: str | None = None
    root_metadata: NodeMetadata | None = None

    for event, element in ET.iterparse(xml_path, events=("start", "end")):
        if event == "start":
            element_stack.append(element)
            if target_hierarchy is None and _local_name(element.tag) == "Hierarchy":
                target_hierarchy = element
            continue

        element_name = _local_name(element.tag)

        if target_hierarchy is not None and element is target_hierarchy and element_name == "Hierarchy":
            root_id, root_metadata = _parse_root_metadata(element)
            element_stack.pop()
            element.clear()
            break

        if target_hierarchy is not None and element_name == "Node":
            flat_nodes.append(_parse_flat_node(element, include_xrefs=include_xrefs))
            element.clear()
            if len(element_stack) >= 2:
                element_stack[-2].remove(element)

        element_stack.pop()

    if root_id is None or root_metadata is None:
        raise ValueError("The XML must contain a Hierarchy element under the Hierarchies root.")

    return root_id, root_metadata, flat_nodes


def _parse_flat_parent_ids(parent_text: str) -> list[str]:
    """Parse serialized parent IDs from one flat hierarchy row.

    Args:
        parent_text: Raw parent-ID field from the flat file.

    Returns:
        list[str]: Parent IDs in file order.
    """

    if not parent_text:
        return []

    return [parent_id for value in parent_text.split("|") if (parent_id := value.strip())]


def _parse_flat_xrefs(xrefs_text: str) -> dict[str, list[str]]:
    """Parse serialized xrefs from one flat hierarchy row.

    Args:
        xrefs_text: Raw xref field from the flat file.

    Returns:
        dict[str, list[str]]: Xrefs grouped by type in input order.

    Raises:
        ValueError: If an xref value is missing the ``Type:Value`` structure.
    """

    grouped_values: dict[str, list[str]] = defaultdict(list)
    if not xrefs_text:
        return {}

    for raw_value in xrefs_text.split("|"):
        value = raw_value.strip()
        if not value:
            continue

        xref_type, separator, xref_value = value.partition(":")
        xref_type = xref_type.strip()
        xref_value = xref_value.strip()
        if not separator or not xref_type or not xref_value:
            raise ValueError(f"Invalid XRef value: {value}")

        grouped_values[xref_type].append(xref_value)

    return dict(grouped_values)


def _serialize_flat_xrefs(xrefs: dict[str, list[str]]) -> str:
    """Serialize grouped xrefs into the flat-file fourth column.

    Args:
        xrefs: Xrefs grouped by type.

    Returns:
        str: Serialized ``Type:Value|Type:Value`` text.
    """

    values: list[str] = []
    for xref_type, xref_values in xrefs.items():
        for xref_value in xref_values:
            values.append(f"{xref_type}:{xref_value}")

    return "|".join(values)


def _build_flat_subtree(
    node_id: str,
    nodes_by_id: dict[str, FlatNode],
    children_by_parent: dict[str, list[str]],
    active_path: set[str] | None = None,
) -> Node:
    """Construct one hierarchy subtree from flat node records.

    Args:
        node_id: Root node identifier for the subtree.
        nodes_by_id: Parsed flat nodes indexed by ``NodeID``.
        children_by_parent: Mapping of parent IDs to child node IDs.
        active_path: Node IDs already visited on the current recursion path.

    Returns:
        Node: Rendered subtree rooted at ``node_id``.

    Raises:
        ValueError: If the flat hierarchy contains a cycle.
    """

    active_path = set() if active_path is None else active_path
    if node_id in active_path:
        cycle_path = " -> ".join([*active_path, node_id])
        raise ValueError(f"Cycle detected in flat hierarchy: {cycle_path}")

    record = nodes_by_id[node_id]
    next_path = set(active_path)
    next_path.add(node_id)
    children = [
        _build_flat_subtree(child_id, nodes_by_id, children_by_parent, next_path)
        for child_id in children_by_parent.get(node_id, [])
    ]
    return Node(metadata=record.metadata, children=children)


def read_flat(flat_path: Path, include_xrefs: bool = True) -> Node:
    """Parse a flat tab-delimited hierarchy file into a tree structure.

    Args:
        flat_path: Path to input flat hierarchy file.
        include_xrefs: Whether to parse optional ``XRefs`` values.

    Returns:
        Node: Root hierarchy node for the viewer.

    Raises:
        FileNotFoundError: If ``flat_path`` does not exist.
        ValueError: If the file contains malformed rows, duplicate node IDs,
            unknown parents, or cycles.
    """

    if not flat_path.exists():
        raise FileNotFoundError(f"Flat hierarchy file not found: {flat_path}")

    flat_nodes: list[FlatNode] = []
    nodes_by_id: dict[str, FlatNode] = {}

    for line_number, raw_line in enumerate(flat_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue

        parts = raw_line.split("\t")
        if len(parts) not in {3, 4}:
            raise ValueError(f"Line {line_number} must contain three or four tab-delimited fields")

        node_id, parent_text, label = parts[:3]
        if not node_id:
            raise ValueError(f"Line {line_number} is missing NodeID")
        if node_id in nodes_by_id:
            raise ValueError(f"Duplicate NodeID on line {line_number}: {node_id}")

        parent_ids = _parse_flat_parent_ids(parent_text)
        xrefs = _parse_flat_xrefs(parts[3]) if include_xrefs and len(parts) == 4 else {}
        metadata = NodeMetadata(
            node_id=node_id,
            parent_ids=parent_ids,
            label=label or node_id,
            xrefs=xrefs,
        )
        flat_node = FlatNode(node_id=node_id, parent_ids=parent_ids, metadata=metadata)
        flat_nodes.append(flat_node)
        nodes_by_id[node_id] = flat_node

    if not flat_nodes:
        raise ValueError("The flat hierarchy file does not contain any nodes")

    children_by_parent: dict[str, list[str]] = defaultdict(list)
    root_ids: list[str] = []
    for node in flat_nodes:
        if not node.parent_ids:
            root_ids.append(node.node_id)
            continue

        for parent_id in node.parent_ids:
            if parent_id not in nodes_by_id:
                raise ValueError(f"Unknown parent ID for node {node.node_id}: {parent_id}")
            children_by_parent[parent_id].append(node.node_id)

    if not root_ids:
        raise ValueError("The flat hierarchy file must contain at least one root node")

    if len(root_ids) == 1:
        return _build_flat_subtree(root_ids[0], nodes_by_id, children_by_parent)

    synthetic_root = NodeMetadata(node_id="root", label="Hierarchy")
    root_children = [_build_flat_subtree(root_id, nodes_by_id, children_by_parent) for root_id in root_ids]
    return Node(metadata=synthetic_root, children=root_children)


def _build_hierarchy_children(
    parent_id: str,
    children_by_parent: dict[str, list[str]],
    nodes_by_id: dict[str, FlatNode],
    attached_node_ids: set[str],
    active_path: set[str] | None = None,
) -> list[Node]:
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

    active_path = set() if active_path is None else active_path
    children: list[Node] = []
    for child_id in children_by_parent.get(parent_id, []):
        if child_id in active_path:
            continue

        record = nodes_by_id[child_id]
        attached_node_ids.add(child_id)
        active_path.add(child_id)
        descendants = _build_hierarchy_children(
            child_id,
            children_by_parent,
            nodes_by_id,
            attached_node_ids,
            active_path,
        )
        active_path.remove(child_id)
        children.append(Node(metadata=record.metadata, children=descendants))

    return children


def read_xml(xml_path: Path, include_xrefs: bool = True) -> Node:
    """Parse a hierarchy XML file into a simple tree structure.

    Args:
        xml_path: Path to input XML file.
        include_xrefs: Whether to parse node ``XRefs`` values.

    Returns:
        Root ``Node`` representing the XML ``Hierarchy`` element.

    Raises:
        FileNotFoundError: If ``xml_path`` does not exist.
        ValueError: If the XML does not contain a ``Hierarchy`` root.
        ET.ParseError: If XML is malformed.
    """

    if not xml_path.exists():
        raise FileNotFoundError(f"XML file not found: {xml_path}")

    root_id, root_metadata, flat_nodes = _stream_hierarchy(xml_path, include_xrefs=include_xrefs)

    nodes_by_id = {node.node_id: node for node in flat_nodes}
    children_by_parent: dict[str, list[str]] = defaultdict(list)
    for node in flat_nodes:
        parent_ids = node.parent_ids or [root_id]
        for parent_id in parent_ids:
            children_by_parent[parent_id].append(node.node_id)

    attached_node_ids: set[str] = set()
    root_children = _build_hierarchy_children(root_id, children_by_parent, nodes_by_id, attached_node_ids)

    for node in flat_nodes:
        if node.node_id in attached_node_ids:
            continue
        root_children.append(
            Node(
                metadata=node.metadata,
                children=_build_hierarchy_children(
                    node.node_id,
                    children_by_parent,
                    nodes_by_id,
                    attached_node_ids,
                    {node.node_id},
                ),
            )
        )

    return Node(metadata=root_metadata, children=root_children)


def _walk_nodes(root: Node) -> Iterator[tuple[Node, Node | None]]:
    """Yield tree nodes with their parent in depth-first order.

    Args:
        root: Root hierarchy node.

    Yields:
        Tuples of ``(node, parent)`` for the full hierarchy.
    """

    stack: list[tuple[Node, Node | None]] = [(root, None)]
    while stack:
        node, parent = stack.pop()
        yield node, parent
        for child in reversed(node.children):
            stack.append((child, node))


def _label_text(node: Node) -> str:
    """Return the display label for a parsed hierarchy node.

    Args:
        node: Parsed hierarchy node.

    Returns:
        The label shown in the tree.
    """

    return node.metadata.label


def _expand_node_recursively(node: Tree[Node].Node) -> None:
    """Expand a rendered tree node and all currently materialized descendants.

    Args:
        node: The tree node from which recursive expansion starts.
    """

    node.expand()
    for child in node.children:
        _expand_node_recursively(child)


def _collapse_node_recursively(node: Tree[Node].Node) -> None:
    """Collapse all descendants of a tree node.

    Args:
        node: The tree node from which recursive collapsing starts.
    """

    for child in node.children:
        _collapse_node_recursively(child)
        child.collapse()

    node.collapse()


def _has_expanded_descendant(node: Tree[Node].Node) -> bool:
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


def _expand_ancestors(node: Tree[Node].Node) -> None:
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

    TITLE = "PubChem Classification Viewer"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("z", "expand_all", "Fold/Unfold"),
        ("/", "search", "Search"),
        ("t", "toggle_metadata", "Toggle Metadata"),
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

    def __init__(self, root: Node, include_xrefs: bool = True) -> None:
        """Initialize the tree viewer application.

        Args:
            root: Parsed root node to visualize.
            include_xrefs: Whether xref widgets should be shown.
        """

        super().__init__()
        self._root = root
        self._include_xrefs = include_xrefs
        self._search_query: str | None = None
        self._search_results: list[Node] = []
        self._search_index = -1
        self._current_metadata: NodeMetadata | None = root.metadata
        self._metadata_visible = True
        self._rendered_nodes: dict[int, Tree[Node].Node] = {}
        self._populated_toc_nodes: set[int] = set()
        self._toc_parents: dict[int, Node | None] = {}
        self._searchable_nodes: list[Node] = []

        for node, parent in _walk_nodes(root):
            self._searchable_nodes.append(node)
            self._toc_parents[id(node)] = parent

    def compose(self) -> ComposeResult:
        """Compose top-level UI widgets."""
        # Header
        yield Header(show_clock=True)

        # Tree
        tree = Tree[Node](self._root.metadata.label, id="toc-tree")
        tree.root.data = self._root
        self._rendered_nodes[id(self._root)] = tree.root
        self._populate_tree_node(tree.root, self._root)
        tree.root.expand()

        # Search
        search_input = Input(
            placeholder="Search...",
            id="search-input",
        )

        # Description and URL
        metadata_text = Static("", id="metadata-text", markup=True)
        metadata_text.update(self._format_metadata(self._root.metadata))

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
                    xref_values.update(self._format_xref_values(self._root.metadata, None))
                    yield xref_select
                    with VerticalScroll(id="xref-values-container"):
                        yield xref_values

        # Footer
        yield Footer()

    def on_mount(self) -> None:
        """Apply the initial layout state after widgets are mounted."""

        self._set_metadata_visibility(self._metadata_visible)

    def action_expand_all(self) -> None:
        """Toggle expanding or folding all descendants of the highlighted node."""

        tree = self.query_one(Tree)
        node = tree.cursor_node or tree.root

        if _has_expanded_descendant(node):
            _collapse_node_recursively(node)
        else:
            self._populate_descendants(node)
            _expand_node_recursively(node)

    def action_search(self) -> None:
        """Focus the search input for querying tree nodes."""

        search_input = self.query_one("#search-input", Input)
        search_input.focus()
        search_input.cursor_position = len(search_input.value)

    def action_toggle_metadata(self) -> None:
        """Hide or show the metadata panel."""

        self._metadata_visible = not self._metadata_visible
        self._set_metadata_visibility(self._metadata_visible)

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

    def on_tree_node_expanded(self, event: Tree.NodeExpanded[Node]) -> None:
        """Populate child widgets lazily when a tree node expands.

        Args:
            event: Expansion event for the current node.
        """

        toc_node = event.node.data
        if toc_node is None:
            return

        self._populate_tree_node(event.node, toc_node)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[Node]) -> None:
        """Update the metadata panel when a tree node is highlighted.

        Args:
            event: Highlight event containing the current node.
        """

        metadata_text = self.query_one("#metadata-text", Static)
        metadata = event.node.data.metadata if event.node.data is not None else None
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

    def _set_metadata_visibility(self, visible: bool) -> None:
        """Update the layout to reflect whether the metadata panel is shown.

        Args:
            visible: Whether the metadata panel should be visible.
        """

        tree = self.query_one("#toc-tree", Tree)
        metadata_panel = self.query_one("#metadata-panel", Vertical)
        metadata_panel.display = visible
        tree.styles.width = "70%" if visible else "100%"

    def _run_search(self, query: str) -> None:
        """Search the tree for nodes containing the query and select the next match.

        Args:
            query: Search query string.
        """

        tree = self.query_one("#toc-tree", Tree)
        lowered = query.casefold()
        matches = [node for node in self._searchable_nodes if lowered in _label_text(node).casefold()]

        if not matches:
            return

        if query == self._search_query:
            self._search_index = (self._search_index + 1) % len(matches)
        else:
            self._search_query = query
            self._search_index = 0

        self._search_results = matches
        target = self._ensure_tree_node(matches[self._search_index])
        if target is None:
            return

        _expand_ancestors(target)

        if hasattr(tree, "select_node"):
            tree.select_node(target)
        else:
            tree.cursor_node = target

        if hasattr(tree, "scroll_to_node"):
            tree.scroll_to_node(target)
        tree.refresh()

    def _populate_tree_node(self, parent: Tree[Node].Node, toc_node: Node) -> None:
        """Render one level of tree children for a parsed hierarchy node.

        Args:
            parent: Rendered Textual tree node.
            toc_node: Parsed hierarchy node backing ``parent``.
        """

        toc_key = id(toc_node)
        if toc_key in self._populated_toc_nodes:
            return

        parent.remove_children()
        for child in toc_node.children:
            if child.children:
                rendered_child = parent.add(child.metadata.label, data=child, allow_expand=True)
            else:
                rendered_child = parent.add_leaf(child.metadata.label, data=child)
            self._rendered_nodes[id(child)] = rendered_child

        self._populated_toc_nodes.add(toc_key)

    def _populate_descendants(self, parent: Tree[Node].Node) -> None:
        """Materialize the full rendered subtree below a UI node.

        Args:
            parent: Rendered tree node to expand fully.
        """

        toc_node = parent.data
        if toc_node is None:
            return

        self._populate_tree_node(parent, toc_node)
        for child in parent.children:
            self._populate_descendants(child)

    def _ensure_tree_node(self, toc_node: Node) -> Tree[Node].Node | None:
        """Ensure a parsed node has a corresponding rendered Textual node.

        Args:
            toc_node: Parsed hierarchy node to locate.

        Returns:
            The rendered tree node when available.
        """

        existing = self._rendered_nodes.get(id(toc_node))
        if existing is not None:
            return existing

        parent_toc = self._toc_parents.get(id(toc_node))
        if parent_toc is None:
            return self._rendered_nodes.get(id(self._root))

        parent_node = self._ensure_tree_node(parent_toc)
        if parent_node is None:
            return None

        self._populate_tree_node(parent_node, parent_toc)
        return self._rendered_nodes.get(id(toc_node))

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

    parser = argparse.ArgumentParser(description="Display classification XML or flat hierarchy as an interactive tree.")
    parser.add_argument("file", help="Path to the classification XML or flat hierarchy file")
    parser.add_argument(
        "-X",
        "--exclude-xrefs",
        action="store_true",
        help="Skip reading XRefs to improve XML parsing performance",
    )
    parser.add_argument(
        "-o",
        "--output",
        const="-",
        nargs="?",
        help="Write a flat tab-delimited hierarchy to a file, or to stdout when omitted or set to '-'",
    )
    return parser


def main() -> None:
    """Run the classification viewer TUI."""

    args = _build_argument_parser().parse_args()
    file_path = Path(args.file)

    try:
        if file_path.suffix.lower() == ".xml":
            root = read_xml(file_path, include_xrefs=not args.exclude_xrefs)
        else:
            root = read_flat(file_path, include_xrefs=not args.exclude_xrefs)
    except (FileNotFoundError, ET.ParseError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    if args.output:
        output_path = None if args.output == "-" else Path(args.output)
        return root.write_flat(output_path)

    app = ClassificationViewer(root, include_xrefs=not args.exclude_xrefs)
    app.run()
