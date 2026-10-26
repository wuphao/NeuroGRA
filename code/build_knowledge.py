"""批量 PDF → 原文知识库 + 知识图谱。运行 python code/build_knowledge.py。"""
import argparse
from pathlib import Path
from datetime import datetime, timedelta, timezone

from neurogra_kb import TextKnowledgeService
from neurogra_kg.api import KnowledgeGraphService
from neurogra_kg.contracts.domain import DocumentInput, BuildRequest
from neurogra_kg.diagnostics import probe
from neurogra_kg.runtime import Budget
from neurogra_kg.utils import atomic_json, checksum
from neurogra_common import new_id
from neurogra_common.llm_log import configure_llm_log, record_llm_event

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Compatibility defaults for direct SDK callers; the CLI uses explicit arguments.
BUILD_KB = True
BUILD_KG = True
MAX_SECONDS_PER_PDF = 7200
MAX_LLM_CALLS_PER_PDF = 2000
MAX_EMBEDDING_CALLS = 10000
PUBLICATION_SECONDS = 7200
OUTPUT_DIRECTORY = PROJECT_ROOT / 'output/knowledge_build'


def find_pdfs(folder, recursive=True):
    folder = Path(folder)
    folder = (folder if folder.is_absolute() else PROJECT_ROOT / folder).resolve()
    if not folder.is_dir():
        raise ValueError('PDF 文件夹不存在：' + str(folder))
    files = sorted((p.resolve() for p in (folder.rglob('*') if recursive else folder.iterdir())
                    if p.is_file() and p.suffix.lower() == '.pdf'), key=lambda p: str(p).casefold())
    if not files:
        raise ValueError('文件夹中没有 PDF：' + str(folder))
    return files


def build_folder(files, directory, *, kb=None, kg=None, graph_error=None, options=None):
    """Process in order, persist progress, and activate one combined release per backend."""
    directory = Path(directory)
    options = options or {}
    build_kg = options.get('build_kg', BUILD_KG)
    seconds = options.get('seconds_per_pdf', MAX_SECONDS_PER_PDF)
    llm_calls = options.get('llm_calls_per_pdf', MAX_LLM_CALLS_PER_PDF)
    embedding_calls = options.get('embedding_calls', MAX_EMBEDDING_CALLS)
    publication_seconds = options.get('publication_seconds', PUBLICATION_SECONDS)
    vector = options.get('vector', True)
    directory.mkdir(parents=True, exist_ok=True)
    summary = {'status': 'running', 'files': [], 'kb_release_id': None, 'kg_release_id': None, 'errors': []}
    kb_files, graph_builds, seen = [], [], {}
    def save():
        atomic_json(directory / 'summary.json', summary)
    save()
    try:
        for index, path in enumerate(files, 1):
            path = Path(path)
            row = {'path': str(path), 'kb': 'disabled' if kb is None else 'pending',
                   'kg': 'disabled' if not build_kg else 'pending'}
            summary['files'].append(row)
            print(f'[{index}/{len(files)}] {path.name}', flush=True)
            try:
                digest = checksum(path)
                row['checksum'] = digest
                if digest in seen:
                    row.update(kb='duplicate', kg='duplicate', duplicate_of=seen[digest])
                    continue
                seen[digest] = str(path)
                if kb is not None:
                    try:
                        # Stage a raw-text release for this PDF, without switching the active corpus.
                        staged = kb.publish([path], vector=False, activate=False)
                        if staged['resource_count'] == 0:
                            raise ValueError('no_parseable_text:needs_OCR_or_review')
                        row.update(kb='parsed', kb_staging_release_id=staged['release_id'])
                        kb_files.append(path)
                    except Exception as exc:
                        row.update(kb='failed', kb_error=str(exc), kb_error_type=type(exc).__name__)
                if kg is not None:
                    try:
                        result = kg.build(BuildRequest(documents=[DocumentInput(path=path, title=path.stem)],
                            corpus_version='pdf_folder_v1', max_seconds=seconds,
                            max_llm_calls=llm_calls, max_embedding_calls=embedding_calls))
                        row.update(kg=result.status, kg_build_id=result.build_id,
                            completed_units=result.completed_units, failed_units=result.failed_units,
                            statement_count=len(result.statement_ids), kg_issues=result.issues)
                        if result.status == 'completed':
                            graph_builds.append(result.build_id)
                    except Exception as exc:
                        row.update(kg='failed', kg_error=str(exc), kg_error_type=type(exc).__name__)
                elif build_kg:
                    row.update(kg='failed', kg_error=graph_error or 'graph_unavailable')
                print(f"  KB={row['kb']}，KG={row['kg']}", flush=True)
            except Exception as exc:
                row.update(error=str(exc), error_type=type(exc).__name__)
            finally:
                save()
        if kb_files:
            print('发布包含全部已解析 PDF 的知识库（生成嵌入）…', flush=True)
            try:
                release = kb.publish(kb_files, vector=vector,
                    budget=Budget(publication_seconds, max_embedding_calls=embedding_calls))
                summary['kb_release_id'] = release['release_id']
                for row in summary['files']:
                    if row['kb'] == 'parsed':
                        row['kb'] = 'published'
            except Exception as exc:
                summary['errors'].append({'stage': 'kb_publication', 'error': str(exc)})
            save()
        if graph_builds:
            print('合并发布全部完成验证的 PDF 图谱…', flush=True)
            try:
                release = kg.publish_builds(graph_builds, max_seconds=publication_seconds,
                    max_embedding_calls=embedding_calls)
                summary['kg_release_id'] = release['release_id']
                for row in summary['files']:
                    if row['kg'] == 'completed':
                        row['kg'] = 'published'
            except Exception as exc:
                summary['errors'].append({'stage': 'kg_publication', 'error': str(exc)})
        issues = bool(summary['errors']) or any(row.get('error') or
            row['kb'] not in {'published', 'disabled', 'duplicate'} or
            row['kg'] not in {'published', 'disabled', 'duplicate'} for row in summary['files'])
        published = bool(summary['kb_release_id'] or summary['kg_release_id'])
        summary['status'] = ('partial' if published else 'failed') if issues else 'completed'
        return summary
    except BaseException:
        summary['status'] = 'interrupted'
        raise
    finally:
        save()


def main(argv=None):
    from neurogra.platform.configuration import resolve_config
    parser = argparse.ArgumentParser(description='批量 PDF 建库与建图')
    parser.add_argument('--pdf-folder', required=True, type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--root', type=Path, default=PROJECT_ROOT)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--no-recursive', action='store_true')
    parser.add_argument('--lexical-only', action='store_true')
    backend = parser.add_mutually_exclusive_group()
    backend.add_argument('--kb-only', action='store_true')
    backend.add_argument('--kg-only', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--seconds-per-pdf', type=int, default=7200)
    parser.add_argument('--llm-calls-per-pdf', type=int, default=2000)
    args = parser.parse_args(argv)
    if args.seconds_per_pdf <= 0 or args.llm_calls_per_pdf <= 0:
        parser.error('构建预算必须为正数')
    if args.lexical_only and not args.kb_only:
        parser.error('--lexical-only 必须与 --kb-only 一起使用')
    config = resolve_config(args.config, root=args.root)
    from neurogra.clinical.knowledge_adapter import settings as kg_settings
    from neurogra_kb import load_config as load_kb
    kg_config = kg_settings(config) if not args.kb_only else None
    kb_config = (config.kb or load_kb(config.resolve(config.text_knowledge_config))) if not args.kg_only else None
    if kb_config:
        kb_config = kb_config.model_copy(update={'root': config.project_root})
    folder = config.resolve(args.pdf_folder)
    files = find_pdfs(folder, not args.no_recursive)
    if args.check:
        print(f'找到 {len(files)} 份 PDF（未构建或连接模型）：')
        for path in files:
            print(path)
        return
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S')
    directory = config.resolve(args.output) if args.output else config.project_root / 'output/knowledge_build' / (stamp + '-' + new_id('batch'))
    configure_llm_log(directory / 'llm_calls.jsonl')
    record_llm_event('batch_start', files=[str(p) for p in files], build_kb=not args.kg_only, build_kg=not args.kb_only)
    kg, graph_error = None, None
    if not args.kb_only:
        print('检查本地生成模型和嵌入模型…', flush=True)
        try:
            if not probe(kg_config, execute=True)['ready']:
                raise ValueError('模型检查未通过，详情见 output/kg/runtime_profile.json')
            kg = KnowledgeGraphService(kg_config)
        except Exception as exc:
            graph_error = str(exc)
            print('图谱构建不可用：' + graph_error, flush=True)
    try:
        summary = build_folder(files, directory, kb=TextKnowledgeService(kb_config) if not args.kg_only else None,
            kg=kg, graph_error=graph_error, options={'build_kg': not args.kb_only,
                'vector': not args.lexical_only, 'seconds_per_pdf': args.seconds_per_pdf,
                'llm_calls_per_pdf': args.llm_calls_per_pdf})
    finally:
        if kg is not None:
            kg.close()
    record_llm_event('batch_complete', **summary)
    print('处理状态：' + summary['status'])
    print('KB 发布版本：' + str(summary['kb_release_id']))
    print('KG 发布版本：' + str(summary['kg_release_id']))
    print('结果清单：' + str(directory / 'summary.json'))
    print('LLM 日志：' + str(directory / 'llm_calls.jsonl'))
    return summary


if __name__ == '__main__':
    result = main()
    if result is not None and result['status'] != 'completed':
        raise SystemExit(1)
