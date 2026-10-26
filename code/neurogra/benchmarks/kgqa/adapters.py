"""Query existing artifacts only; never call build/publish/train APIs."""
from datetime import datetime
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
from pydantic import BaseModel
from neurogra_kg.contracts.domain import Purpose

from .core import canonical, digest, read_json, write_json, bounded_context


class QueryPurpose(BaseModel):
    purpose: Purpose


class GatewayEmbedding:
    """Keep the published embedding contract; redirect cache/logs into the run."""
    def __init__(self, config, gateway):
        self.config, self.gateway = config, gateway

    def model_digest(self, timeout=10):
        return self.gateway.identity[self.config.model]

    def embed(self, texts, budget=None):
        import numpy as np
        result = []
        for text in texts:
            if budget:
                budget.reserve('embedding')
            v = np.asarray(self.gateway.embed(text), dtype=float)
            norm = float(np.linalg.norm(v))
            if norm <= 0:
                raise ValueError('zero_embedding')
            result.append((v / norm).tolist())
        return result


def condition_view(node):
    if node is None:
        return None
    if isinstance(node, dict):
        return {k: condition_view(v) for k, v in node.items()
                if k not in {'source_refs', 'zh_annotation', 'condition_id'}}
    if isinstance(node, list):
        return [condition_view(v) for v in node]
    return node


def interleave(*lists):
    for index in range(max(map(len, lists), default=0)):
        for items in lists:
            if index < len(items):
                yield items[index]


class RetrievalAdapters:
    def __init__(self, c, audited, gateway):
        self.c, self.a, self.gateway = c, audited, gateway
        self.med_documents = self.med_refs = self.med_graphs = self.med_config = None

    def _kb(self, question):
        from neurogra_kb.config import load_config
        from neurogra_kb.api import TextKnowledgeService
        cfg = load_config(self.c['kb_config'])
        service = TextKnowledgeService(cfg, GatewayEmbedding(cfg.embedding, self.gateway))
        raw = service.search_text(question, release_id=self.c['kb_release'],
                                  document_ids=self.a['kb_document_ids'], top_k=self.c['top_k'])
        if any(status not in {'ok', 'empty'} for status in raw['backend_status'].values()):
            raise ValueError('kb_backend_unavailable:' + canonical(raw['backend_status']))
        rows = [{'id': row['id'], 'type': 'original', 'text': row['text'],
                 'document': row['title'], 'page': row['page']} for row in raw['items']]
        return raw, rows

    def _kg(self, question, directory):
        from neurogra_kg import KnowledgeGraphService
        from neurogra_kg.config import load_config
        from neurogra_kg.adapters.ollama_llm import OllamaLLMAdapter
        from neurogra_kg.contracts.domain import Block, RetrievalRequest
        cfg = load_config(self.c['kg_config'])
        cfg.paths.output_root = directory / 'kg_internal'
        cfg.retrieval.allow_explicit_degradation = False
        cfg.llm.keep_alive = '5m'
        # The API opens its registry read-only; all model records stay in this case.
        def transport(url, data, timeout):
            endpoint = urlsplit(url).path
            if endpoint == '/api/chat':
                self.gateway.calls += 1
            return self.gateway.request(endpoint, data, 'kg_internal', timeout=timeout)
        llm = OllamaLLMAdapter(cfg.llm, directory / 'kg_internal', transport=transport)
        purpose = self.gateway.generate('仅判断问题所询问的用途，不回答问题；'
            '普通诊断标准选择diagnostic_assessment，鉴别诊断选择differential_diagnosis，'
            '影像解释选择imaging_interpretation，生物标志物解释选择biomarker_interpretation，'
            '认知测评选择cognitive_assessment，随访选择follow_up。',
            {'question': question}, 'kg_query_purpose', QueryPurpose)
        with KnowledgeGraphService(cfg, llm=llm,
                embedding=GatewayEmbedding(cfg.embedding, self.gateway), read_only=True) as kg:
            reference_time = datetime.fromisoformat(self.c['reference_time'])
            block = Block(block_id='question_record', document_id='question', page=1,
                          order=0, kind='paragraph', text=question)
            facts, fact_issues = kg.extract_patient_facts(
                [block], reference_time, max_seconds=self.c['retrieval_seconds'])
            request = RetrievalRequest(question=question, facts=facts,
                purpose=purpose.purpose, reference_time=reference_time,
                release_id=self.c['kg_release'], top_k=self.c['top_k'],
                max_supplement_rounds=0, max_seconds=self.c['retrieval_seconds'],
                max_llm_calls=self.c['kg_max_llm_calls'])
            result = kg.retrieve(request).model_dump(mode='json')
        corpus = read_json(Path(self.a['kg_directory']) / 'corpus.json')
        blocks = {b['block_id']: b for b in corpus['blocks']}
        docs = {d['document_id']: d for d in corpus['documents']}
        groups = []
        for group in ['applicable', 'unknown', 'excluded']:
            rows = []
            for item in result[group]:
                statement = item['statement']
                refs = []
                def collect(value):
                    if isinstance(value, dict):
                        if {'block_id', 'quote', 'start', 'end'} <= value.keys():
                            if value not in refs:
                                refs.append(value)
                        for v in value.values():
                            collect(v)
                    elif isinstance(value, list):
                        for v in value:
                            collect(v)
                collect(statement['source_refs'])
                collect(statement['condition'])
                provenance = []
                for ref in refs:
                    b = blocks[ref['block_id']]
                    if b['text'][ref['start']:ref['end']] != ref['quote']:
                        raise ValueError('kg_quote_changed')
                    provenance.append({'document': docs[b['document_id']]['title'],
                                       'page': b['page'], 'quote': ref['quote']})
                rows.append({'id': item['evidence_id'], 'type': 'conditional_statement',
                             'text': statement['assertion_text'], 'state': item['state'],
                             'conditions': condition_view(statement['condition']),
                             'condition_status': statement['condition_status'],
                             'sources': provenance,
                             'condition_results': [{k: x[k] for k in ('state', 'reason', 'missing_fields')}
                                                   for x in item['condition_results']]})
            groups.append(rows)
        return {'request': request.model_dump(mode='json'), 'result': result,
                'fact_extraction_issues': fact_issues}, list(interleave(*groups))

    def _load_med(self):
        if self.med_graphs is not None:
            return
        import yaml
        from neurogra.benchmarks.medgraphrag.runtime import Config
        from neurogra.benchmarks.medgraphrag.pipeline import load_knowledge
        raw = yaml.safe_load(Path(self.c['med_config']).read_text(encoding='utf-8'))
        self.med_config = Config.model_validate(raw)
        docs, refs, _ = load_knowledge(self.c['med_manifest'], self.med_config.chunk_chars)
        graphs = []
        for d in docs:
            artifact = read_json(d['prebuilt_graph'])
            g = artifact['graph']
            if digest(g) != artifact['graph_hash'] or not set(g['source_refs']) <= refs.keys():
                raise ValueError('medgraph_provenance_mismatch')
            for entity in g['entities']:
                if not set(entity['refs']) <= refs.keys():
                    raise ValueError('medgraph_unknown_reference')
            graphs.append(g)
        self.med_documents, self.med_refs, self.med_graphs = docs, refs, graphs

    def _med(self, question):
        self._load_med()
        from neurogra.benchmarks.medgraphrag.pipeline import Rating
        from neurogra.benchmarks.medgraphrag.retrieval import (
            summary_preview, retrieve, prompt_graph)
        # Question-only adaptation: no invented patient graph, no graph construction.
        summary = self.gateway.generate('将问题概括为检索请求；保留年份、数值、否定、'
            '未知与询问目标，不回答问题，不补充医学事实。', {'question': question}, 'med_query_summary')
        ratings = []
        for graph in self.med_graphs:
            rating = self.gateway.generate('评价检索请求与文献摘要的相关性。0不相关至4高度相关。'
                '仅依据给定内容判断，不回答原问题。',
                {'query': summary, 'summary': summary_preview(graph['summary'],
                    self.med_config.document_summary_chars)}, 'med_summary_rating', Rating)
            ratings.append({'gid': graph['gid'], 'rating': rating.rating})
        chosen = {r['gid'] for r in sorted(ratings, key=lambda r: (-r['rating'], r['gid']))[
                  :self.med_config.selected_graphs]}
        def evidence(context, refs):
            graph = prompt_graph(context)
            return [{'id': 'med:' + ref, 'type': 'graph_linked_original', 'text': row['text'],
                     'document': self.med_refs[ref]['source'], 'page': row['page'],
                     'offset': row['page_text_offset'],
                     'graph_entities': [n for n in graph['entities'] if ref in n['refs']],
                     'graph_relationships': [edge for edge in graph['relationships'] if ref in edge['refs']]}
                    for ref, row in refs.items()]
        def fits(context, refs):
            rows = evidence(context, refs)
            packed, omitted = bounded_context(rows, self.c['evidence_token_bound'])
            return not omitted and len(packed) == len(rows)
        context, refs, trace = retrieve(self.med_graphs, chosen, {'gid': 'question-only'}, [],
            self.gateway.embed(question), self.med_refs, question, self.med_config,
            fits, embed=self.gateway.embed)
        return {'summary': summary, 'ratings': ratings, 'chosen': sorted(chosen),
                'context': context, 'references': refs, 'trace': trace}, evidence(context, refs)

    def retrieve(self, group, question, directory):
        start = time.monotonic()
        if group == 'llm':
            raw, items = {}, []
        elif group == 'kb':
            raw, items = self._kb(question)
            write_json(Path(directory) / 'kb_retrieval.json', raw)
        elif group == 'kg_kb':
            # KB evidence is independent of graph coverage and is never silently suppressed.
            kb_raw, kb_items = self._kb(question)
            write_json(Path(directory) / 'kb_retrieval.json', kb_raw)
            kg_raw, kg_items = self._kg(question, directory)
            write_json(Path(directory) / 'kg_retrieval.json', kg_raw)
            raw = {'kb': kb_raw, 'kg': kg_raw}
            items = list(interleave(kg_items, kb_items))
        elif group == 'medgraphrag':
            raw, items = self._med(question)
            write_json(Path(directory) / 'med_retrieval.json', raw)
        else:
            raise ValueError('unknown_group')
        selected, omitted = bounded_context(items, self.c['evidence_token_bound'])
        if group != 'llm' and not selected:
            raise ValueError('no_complete_evidence_fits_budget')
        return {'raw': raw, 'candidates': items, 'evidence': selected,
                'omitted_ids': omitted, 'seconds': time.monotonic() - start,
                'budget_policy': 'UTF-8 byte upper bound on serialized evidence; whole units only',
                'evidence_bytes': len(canonical(selected).encode('utf-8')),
                'kg_candidates': sum(x['type'] == 'conditional_statement' for x in items),
                'kg_used': sum(x['type'] == 'conditional_statement' for x in selected)}
