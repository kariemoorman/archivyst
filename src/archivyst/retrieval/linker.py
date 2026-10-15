"""
Wikilink graph traversal for Archivyst retrieval.

Follows [[wikilinks]] to discover related content.
"""

from collections import deque
from pathlib import Path

from archivyst.core.index import VaultIndex


class WikilinkGraph:
    """
    Graph representation of wikilink connections between notes.

    Supports traversal operations for related content discovery.
    """

    def __init__(self, vault_index: VaultIndex):
        self.vault_index = vault_index
        self._graph: dict[str, list[str]] | None = None
        self._reverse_graph: dict[str, list[str]] | None = None

    def _ensure_graph(self) -> None:
        """Build graph lazily on first access."""
        if self._graph is None:
            self._graph = self.vault_index.build_wikilink_graph()
            self._reverse_graph = self._build_reverse_graph(self._graph)

    @staticmethod
    def _build_reverse_graph(graph: dict[str, list[str]]) -> dict[str, list[str]]:
        """Build reverse graph (backlinks)."""
        reverse: dict[str, list[str]] = {}

        for source, targets in graph.items():
            for target in targets:
                if target not in reverse:
                    reverse[target] = []
                reverse[target].append(source)

        return reverse

    @property
    def graph(self) -> dict[str, list[str]]:
        """Forward graph: note -> notes it links to."""
        self._ensure_graph()
        return self._graph  # type: ignore

    @property
    def reverse_graph(self) -> dict[str, list[str]]:
        """Reverse graph: note -> notes that link to it (backlinks)."""
        self._ensure_graph()
        return self._reverse_graph  # type: ignore

    def get_outlinks(self, note_path: str) -> list[str]:
        """Get notes that this note links to."""
        return self.graph.get(note_path, [])

    def get_backlinks(self, note_path: str) -> list[str]:
        """Get notes that link to this note."""
        return self.reverse_graph.get(note_path, [])

    def get_neighbors(self, note_path: str) -> set[str]:
        """Get all directly connected notes (outlinks + backlinks)."""
        outlinks = set(self.get_outlinks(note_path))
        backlinks = set(self.get_backlinks(note_path))
        return outlinks | backlinks

    def bfs_traverse(
        self,
        start_paths: list[str],
        max_depth: int = 2,
        max_nodes: int = 20,
    ) -> list[tuple[str, int]]:
        """
        Breadth-first traversal from starting nodes.

        Args:
            start_paths: Starting note paths
            max_depth: Maximum traversal depth
            max_nodes: Maximum nodes to return

        Returns:
            List of (note_path, depth) tuples
        """
        visited: set[str] = set()
        result: list[tuple[str, int]] = []
        queue: deque[tuple[str, int]] = deque()

        # Initialize queue with starting nodes
        for path in start_paths:
            if path in self.graph or path in self.reverse_graph:
                queue.append((path, 0))
                visited.add(path)

        while queue and len(result) < max_nodes:
            current, depth = queue.popleft()
            result.append((current, depth))

            if depth >= max_depth:
                continue

            # Add unvisited neighbors
            for neighbor in self.get_neighbors(current):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, depth + 1))

        return result

    def find_path(
        self,
        source: str,
        target: str,
        max_depth: int = 5,
    ) -> list[str] | None:
        """
        Find shortest path between two notes.

        Returns:
            List of note paths forming the path, or None if not found
        """
        if source == target:
            return [source]

        visited: set[str] = {source}
        queue: deque[tuple[str, list[str]]] = deque([(source, [source])])

        while queue:
            current, path = queue.popleft()

            if len(path) > max_depth:
                continue

            for neighbor in self.get_neighbors(current):
                if neighbor == target:
                    return path + [neighbor]

                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))

        return None

    def find_common_ancestors(
        self,
        paths: list[str],
        max_depth: int = 3,
    ) -> list[str]:
        """
        Find notes that link to multiple of the given notes.

        Useful for finding hub/index notes.
        """
        if not paths:
            return []

        # Get backlinks for each path up to max_depth
        backlink_sets: list[set[str]] = []

        for path in paths:
            backlinks: set[str] = set()
            current_level = {path}

            for _ in range(max_depth):
                next_level: set[str] = set()
                for note in current_level:
                    for backlink in self.get_backlinks(note):
                        if backlink not in backlinks:
                            backlinks.add(backlink)
                            next_level.add(backlink)
                current_level = next_level

            backlink_sets.append(backlinks)

        # Find intersection
        if not backlink_sets:
            return []

        common = backlink_sets[0]
        for bs in backlink_sets[1:]:
            common &= bs

        return list(common)

    def get_cluster(
        self,
        note_path: str,
        max_size: int = 10,
    ) -> list[str]:
        """
        Get a cluster of closely related notes.

        Uses link density to identify tightly connected subgraph.
        """
        result = self.bfs_traverse([note_path], max_depth=2, max_nodes=max_size)
        return [path for path, _ in result]

    def invalidate(self) -> None:
        """Invalidate cached graph (call after index changes)."""
        self._graph = None
        self._reverse_graph = None


def expand_with_links(
    vault_index: VaultIndex,
    initial_paths: list[Path],
    max_expansion: int = 5,
    max_depth: int = 1,
) -> list[Path]:
    """
    Expand a set of notes by following wikilinks.

    Args:
        vault_index: Vault index instance
        initial_paths: Initial set of note paths
        max_expansion: Maximum additional notes to add
        max_depth: Maximum link depth to follow

    Returns:
        Expanded list of note paths
    """
    graph = WikilinkGraph(vault_index)

    initial_strs = [str(p.relative_to(vault_index.vault_path)) for p in initial_paths]

    expanded = graph.bfs_traverse(
        initial_strs,
        max_depth=max_depth,
        max_nodes=len(initial_paths) + max_expansion,
    )

    # Convert back to absolute paths
    result_paths: list[Path] = []
    for rel_path, _ in expanded:
        abs_path = vault_index.vault_path / rel_path
        if abs_path.exists():
            result_paths.append(abs_path)

    return result_paths


def score_by_centrality(
    vault_index: VaultIndex,
    note_paths: list[Path],
) -> list[tuple[Path, float]]:
    """
    Score notes by their centrality in the wikilink graph.

    Higher scores indicate more connected notes.

    Returns:
        List of (path, score) tuples sorted by score descending
    """
    graph = WikilinkGraph(vault_index)

    scores: list[tuple[Path, float]] = []

    for path in note_paths:
        rel_path = str(path.relative_to(vault_index.vault_path))

        # Simple degree centrality: outlinks + backlinks
        outlinks = len(graph.get_outlinks(rel_path))
        backlinks = len(graph.get_backlinks(rel_path))

        # Weight backlinks higher (being referenced is more significant)
        score = outlinks + (backlinks * 1.5)
        scores.append((path, score))

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores
