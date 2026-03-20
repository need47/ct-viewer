# ct-viewer

`ct-viewer` is a Textual-based terminal UI for browsing PubChem classification XML files as an interactive tree.

It is intended for PubChem-style hierarchy documents and works with XML rooted at `Hierarchies`. The left pane shows the hierarchy tree, and the right pane shows metadata for the currently highlighted node, including descriptions, comments, URLs, and optional cross-references.

## Features

- Interactive tree viewer for hierarchy XML
- Streaming XML parsing for faster, lower-memory startup on large files
- Lazy tree rendering so only expanded branches are materialized in the UI
- Search by node label from inside the TUI
- Expand or collapse the current subtree with a single key
- Metadata panel for hierarchy and node details
- Optional XRef browser for nodes that contain `XRefs`
- Graceful handling of unattached nodes by placing them under the root view

## Requirements

- Python 3.13+
- A terminal that can run Textual applications

## Installation

### Using `uv`
To install directly from the Git repository:

```bash
uv tool install git+https://github.com/need47/ct-viewer.git
```

### Using `pip`

```bash
pip install git+https://github.com/need47/ct-viewer.git
```

## Usage

```bash
cv path/to/hierarchy.xml
```

To skip parsing `XRefs` for faster startup on large files:

```bash
cv path/to/hierarchy.xml --exclude-xrefs
```

## Keyboard shortcuts

- `q`: quit
- `z`: expand or collapse the highlighted subtree
- `/`: focus the search box
- `Enter` in the search box: find the first match, or jump to the next match when the same query is submitted again
