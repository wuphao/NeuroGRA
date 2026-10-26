"""Namespaced graph implementations; local source data remain authoritative."""

import os
import json

from ..utils import canonical
from ..construction.sources import condition_leaves
from ..terminology import entity_names


class MemoryGraphStore:
    def __init__(self):
        self.releases = {}

    def publish(self, release_id, statements, blocks=None):
        self.releases[release_id] = list(statements)

    def neighbors(self, release_id, terms, max_hops, limit):
        statements = self.releases.get(release_id)
        if statements is None:
            raise ValueError("memory_graph_release_not_loaded")
        frontier = {entity.entity_id for statement in statements
                    for entity in [statement.subject, statement.object]
                    if any(term.casefold() in name.casefold() for term in terms
                           for name in entity_names(entity))}
        result = set()
        for _ in range(max_hops):
            next_frontier = set()
            for statement in statements:
                if {statement.subject.entity_id, statement.object.entity_id} & frontier:
                    result.add(statement.statement_id)
                    next_frontier.update([statement.subject.entity_id, statement.object.entity_id])
            frontier = next_frontier
        return sorted(result)[:limit]


class Neo4jGraphStore:
    def __init__(self, config):
        self.config = config

    def _driver(self):
        from neo4j import GraphDatabase
        password = os.environ.get(self.config.password_env)
        if not password and self.config.password_file and self.config.password_file.is_file():
            value = json.loads(self.config.password_file.read_text(encoding="utf-8"))
            if value.get("username") != self.config.username:
                raise ValueError("neo4j_local_credentials_username_mismatch")
            password = value.get("password")
        if not password:
            raise ValueError("neo4j_password_env_missing:" + self.config.password_env)
        return GraphDatabase.driver(self.config.uri, auth=(self.config.username, password),
            connection_timeout=10, connection_acquisition_timeout=10)

    def probe(self):
        with self._driver() as driver:
            driver.verify_connectivity()
            with driver.session(database=self.config.database) as session:
                return session.run("RETURN 1 AS ok").single()["ok"] == 1

    def publish(self, release_id, statements, blocks=None):
        blocks = blocks or {}
        rows = []
        for statement in statements:
            refs = [*statement.source_refs, *[ref for leaf in condition_leaves(statement.condition) for ref in leaf.source_refs]]
            rows.append({"key": release_id + ":" + statement.statement_id,
                "id": statement.statement_id, "h": statement.subject.entity_id,
                "h_name": statement.subject.name, "h_aliases": entity_names(statement.subject),
                "h_label_zh": statement.subject.label_zh, "h_label_en": statement.subject.label_en,
                "h_type": statement.subject.entity_type, "t": statement.object.entity_id,
                "t_name": statement.object.name, "t_aliases": entity_names(statement.object),
                "t_label_zh": statement.object.label_zh, "t_label_en": statement.object.label_en,
                "t_type": statement.object.entity_type, "predicate": statement.predicate,
                "payload": canonical(statement.model_dump(mode="json")),
                "condition": canonical(statement.condition.model_dump(mode="json")) if statement.condition else "null",
                "purposes": list(statement.purposes),
                "assertion_language": statement.assertion_language,
                "assertion_text": statement.assertion_text,
                "assertion_text_zh": statement.zh_annotation.text if statement.zh_annotation and statement.zh_annotation.status == "verified" else None,
                "sources": [{"key": release_id + ":" + ref.block_id + ":" + str(ref.start) + ":" + str(ref.end),
                    "block_id": ref.block_id, "quote": ref.quote,
                    "page": blocks[ref.block_id].page if ref.block_id in blocks and blocks[ref.block_id].page_kind == "physical" else None,
                    "page_kind": blocks[ref.block_id].page_kind if ref.block_id in blocks else "unavailable",
                    "source_locator": blocks[ref.block_id].source_locator if ref.block_id in blocks else None} for ref in refs]})
        # All graph nodes are explicitly release-bound; never touch old application labels.
        with self._driver() as driver:
            with driver.session(database=self.config.database) as session:
                for label in ["NGKGStatement", "NGKGEntity", "NGKGCondition", "NGKGPurpose", "NGKGSource"]:
                    session.run(f"CREATE CONSTRAINT {label.lower()}_key IF NOT EXISTS FOR (n:{label}) REQUIRE n.key IS UNIQUE").consume()
                def write(tx):
                    tx.run("MERGE (r:NGKGRelease {id:$release}) SET r.status='ready'", release=release_id).consume()
                    for row in rows:
                        tx.run("""
                            MERGE (s:NGKGStatement {key:$key})
                            SET s.id=$id,s.release_id=$release,s.predicate=$predicate,s.payload=$payload,
                                s.assertion_language=$assertion_language,s.assertion_text=$assertion_text,s.assertion_text_zh=$assertion_text_zh
                            MERGE (h:NGKGEntity {key:$hkey}) SET h.id=$h,h.name=$h_name,h.aliases=$h_aliases,h.type=$h_type,h.release_id=$release,h.label_zh=$h_label_zh,h.label_en=$h_label_en
                            MERGE (t:NGKGEntity {key:$tkey}) SET t.id=$t,t.name=$t_name,t.aliases=$t_aliases,t.type=$t_type,t.release_id=$release,t.label_zh=$t_label_zh,t.label_en=$t_label_en
                            MERGE (s)-[:SUBJECT]->(h) MERGE (s)-[:OBJECT]->(t)
                            MERGE (c:NGKGCondition {key:$key}) SET c.payload=$condition,c.release_id=$release
                            MERGE (s)-[:HAS_CONDITION]->(c)
                            WITH s UNWIND $purposes AS purpose
                            MERGE (u:NGKGPurpose {key:$release+':'+purpose}) SET u.name=purpose,u.release_id=$release
                            MERGE (s)-[:HAS_PURPOSE]->(u)
                        """, **{key: value for key, value in row.items() if key != "sources"},
                            release=release_id, hkey=release_id + ":" + row["h"], tkey=release_id + ":" + row["t"]).consume()
                        tx.run("""
                            MATCH (s:NGKGStatement {key:$key}) UNWIND $sources AS source
                            MERGE (e:NGKGSource {key:source.key})
                            SET e.block_id=source.block_id,e.quote=source.quote,e.page=source.page,e.release_id=$release,
                                e.page_kind=source.page_kind,e.source_locator=source.source_locator
                            MERGE (s)-[:SUPPORTED_BY]->(e)
                        """, key=row["key"], sources=row["sources"], release=release_id).consume()
                    for statement in statements:
                        def save_condition(condition, path="root", parent=None, order=0):
                            key = release_id + ":" + statement.statement_id + ":" + path
                            kind = condition.kind if condition else statement.condition_status
                            payload = condition.model_dump(mode="json") if condition else None
                            tx.run("MERGE (c:NGKGCondition {key:$key}) SET c.release_id=$release,c.kind=$kind,c.payload=$payload",
                                   key=key, release=release_id, kind=kind, payload=canonical(payload)).consume()
                            if parent:
                                tx.run("MATCH (p:NGKGCondition {key:$parent}),(c:NGKGCondition {key:$key}) "
                                       "MERGE (p)-[o:OPERAND {position:$position}]->(c)",
                                       parent=parent, key=key, position=order).consume()
                            else:
                                tx.run("MATCH (s:NGKGStatement {key:$statement}),(c:NGKGCondition {key:$key}) "
                                       "MERGE (s)-[:CONDITION_ROOT]->(c)",
                                       statement=release_id + ":" + statement.statement_id, key=key).consume()
                            if condition and condition.kind in {"ATOM", "TEXT"}:
                                refs = [{"key": release_id + ":" + ref.block_id + ":" + str(ref.start) + ":" + str(ref.end)}
                                        for ref in condition.source_refs]
                                tx.run("MATCH (c:NGKGCondition {key:$key}) UNWIND $refs AS ref "
                                       "MATCH (e:NGKGSource {key:ref.key}) MERGE (c)-[:SUPPORTED_BY]->(e)",
                                       key=key, refs=refs).consume()
                            elif condition:
                                children = [condition.child] if condition.kind == "NOT" else condition.children
                                for index, child in enumerate(children):
                                    save_condition(child, path + "/" + str(index), key, index)
                        save_condition(statement.condition)
                session.execute_write(write)

    def neighbors(self, release_id, terms, max_hops, limit):
        if not 1 <= max_hops <= 4:
            raise ValueError("graph_hop_limit_invalid")
        from neo4j import Query
        query = """
            MATCH (e:NGKGEntity {release_id:$release})
            WHERE any(term IN $terms WHERE toLower(e.name) CONTAINS toLower(term)
                OR any(alias IN e.aliases WHERE toLower(alias) CONTAINS toLower(term)))
            MATCH p=(e)-[:SUBJECT|OBJECT*1..HOPS]-(s:NGKGStatement {release_id:$release})
            WHERE all(n IN nodes(p) WHERE n.release_id=$release)
            RETURN DISTINCT s.id AS id ORDER BY id LIMIT $limit
        """.replace("HOPS", str(max_hops * 2 - 1))
        with self._driver() as driver:
            with driver.session(database=self.config.database, default_access_mode="READ") as session:
                marker = list(session.run(Query("MATCH (r:NGKGRelease {id:$release, status:'ready'}) RETURN r.id AS id LIMIT 1",
                                                 timeout=15), release=release_id))
                if not marker:
                    raise ValueError("graph_release_missing_in_current_instance")
                return [row["id"] for row in session.run(Query(query, timeout=15), release=release_id,
                                                         terms=terms, limit=limit)]
