"""Kuzu graph store — T-078.

Embedded Kuzu graph database. No Docker container, no server, no port.
Database stored at KUZU_DB_PATH (default: ./data/knowledge_graph).

Kuzu uses Cypher syntax identical to Neo4j — all query_service.py queries
are compatible without modification.

Usage:
    store = KuzuGraphStore()
    store.initialize()
    store.upsert_node(GraphNode(...))
    store.upsert_edge(GraphEdge(...))
    store.health_check()  # Returns bool — never raises
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from knowledge_graph.graph_schema import EdgeType, GraphEdge, GraphNode, NodeType

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = "./data/knowledge_graph"


class KuzuGraphStore:
    """Embedded Kuzu graph database store.

    Kuzu is a file-based embedded graph DB. No server, no Docker container.
    Creates the database directory on first use.
    Fails gracefully — all methods return safe defaults on error.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or os.environ.get("KUZU_DB_PATH", _DEFAULT_DB_PATH)
        self._db = None
        self._conn = None
        self._initialized = False

    def initialize(self) -> bool:
        """Initialize Kuzu database and create schema tables.

        Returns:
            True if initialization succeeded. False on error (never raises).
        """
        try:
            import kuzu  # type: ignore
        except ImportError:
            logger.warning(
                "Kuzu is not installed. Run: pip install kuzu. "
                "Knowledge graph will be unavailable."
            )
            return False

        try:
            parent = os.path.dirname(os.path.abspath(self._db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            self._db = kuzu.Database(self._db_path)
            self._conn = kuzu.Connection(self._db)
            self._create_schema()
            self._initialized = True
            logger.info("KuzuGraphStore: initialized at %s", self._db_path)
            return True
        except Exception as e:
            logger.error("KuzuGraphStore: initialization failed: %s", e)
            self._initialized = False
            return False

    def _create_schema(self) -> None:
        """Create node and edge tables if they don't exist."""
        # Node table — one table for all node types
        # Kuzu uses typed node tables; we use a single polymorphic table
        # with a 'node_type' string property.
        try:
            self._conn.execute("""
                CREATE NODE TABLE IF NOT EXISTS GraphNode (
                    node_id STRING,
                    node_type STRING,
                    properties STRING,
                    PRIMARY KEY (node_id)
                )
            """)
        except Exception:
            pass  # table may already exist

        # Edge table
        try:
            self._conn.execute("""
                CREATE REL TABLE IF NOT EXISTS GraphEdge (
                    FROM GraphNode TO GraphNode,
                    edge_type STRING,
                    properties STRING
                )
            """)
        except Exception:
            pass

    def upsert_node(self, node: GraphNode) -> bool:
        """Insert or update a graph node.

        Args:
            node: GraphNode dataclass instance.

        Returns:
            True on success. False on error.
        """
        if not self._initialized or not self._conn:
            return False
        try:
            props_json = json.dumps(node.properties or {})
            # Kuzu MERGE equivalent: try insert, update if exists
            try:
                self._conn.execute(
                    "CREATE (:GraphNode {node_id: $id, node_type: $type, properties: $props})",
                    {"id": node.node_id, "type": node.node_type.value, "props": props_json},
                )
            except Exception:
                # Node already exists — update properties
                self._conn.execute(
                    "MATCH (n:GraphNode {node_id: $id}) SET n.properties = $props",
                    {"id": node.node_id, "props": props_json},
                )
            return True
        except Exception as e:
            logger.warning("KuzuGraphStore.upsert_node failed: %s", e)
            return False

    def upsert_edge(self, edge: GraphEdge) -> bool:
        """Insert or update a graph edge.

        Args:
            edge: GraphEdge dataclass instance.

        Returns:
            True on success. False on error.
        """
        if not self._initialized or not self._conn:
            return False
        try:
            props_json = json.dumps(edge.properties or {})
            self._conn.execute(
                """
                MATCH (a:GraphNode {node_id: $from_id}), (b:GraphNode {node_id: $to_id})
                CREATE (a)-[:GraphEdge {edge_type: $etype, properties: $props}]->(b)
                """,
                {
                    "from_id": edge.source_node_id,
                    "to_id": edge.target_node_id,
                    "etype": edge.edge_type.value,
                    "props": props_json,
                },
            )
            return True
        except Exception as e:
            logger.warning("KuzuGraphStore.upsert_edge failed: %s", e)
            return False

    def query(self, cypher: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Execute a Cypher query and return results as a list of dicts.

        Args:
            cypher: Cypher query string.
            params: Optional query parameters.

        Returns:
            List of row dicts. Empty list on error or no results.
        """
        if not self._initialized or not self._conn:
            return []
        try:
            result = self._conn.execute(cypher, params or {})
            rows = []
            while result.has_next():
                row = result.get_next()
                # Convert list row to dict using column names
                col_names = result.get_column_names()
                rows.append(dict(zip(col_names, row)))
            return rows
        except Exception as e:
            logger.warning("KuzuGraphStore.query failed: %s", e)
            return []

    def health_check(self) -> bool:
        """Test graph store connectivity.

        Returns:
            True if the database is reachable. False on any error.
            Never raises.
        """
        if not self._initialized:
            return False
        try:
            import kuzu  # type: ignore
            test_conn = kuzu.Connection(self._db)
            result = test_conn.execute("RETURN 1")
            _ = result.get_next()
            return True
        except Exception as e:
            logger.warning("KuzuGraphStore.health_check failed: %s", e)
            return False

    def close(self) -> None:
        """Close database connection."""
        self._conn = None
        self._db = None
        self._initialized = False
