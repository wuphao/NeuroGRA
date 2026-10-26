import unittest
from unittest.mock import patch

from neurogra_kg.adapters.graph import Neo4jGraphStore
from neurogra_kg.config import GraphConfig


class GraphVersionTests(unittest.TestCase):
    def driver(self, exists):
        class Session:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def run(self, query, **kwargs):
                if 'NGKGRelease' in str(query):
                    return [{'id': kwargs['release']}] if exists else []
                return [{'id': 'code_statement'}]
        class Driver:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def session(self, **kwargs): return Session()
        return Driver()

    def test_connected_instance_without_version_is_not_a_successful_empty_graph(self):
        graph = Neo4jGraphStore(GraphConfig())
        with patch.object(graph, '_driver', return_value=self.driver(False)):
            with self.assertRaisesRegex(ValueError, 'graph_release_missing'):
                graph.neighbors('code_release', ['term'], 2, 5)

    def test_graph_query_requires_ready_version_in_the_selected_instance(self):
        graph = Neo4jGraphStore(GraphConfig())
        with patch.object(graph, '_driver', return_value=self.driver(True)):
            self.assertEqual(graph.neighbors('code_release', ['term'], 2, 5), ['code_statement'])
