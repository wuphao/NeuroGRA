"""Deterministic visible JSON and text. No inference or model calls."""
from copy import deepcopy
from datetime import date
import hashlib
import json

from neurogra.clinical.schemas import PatientInput, is_rwe_patient_json
from neurogra.clinical.rwe_preprocessing import trim_patient


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False).encode('utf-8')).hexdigest()


def pointer_key(key):
    return str(key).replace('~', '~0').replace('/', '~1')


def leaves(value, pointer=''):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, pointer + '/' + pointer_key(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from leaves(item, pointer + '/' + str(index))
    else:
        yield pointer, value


def prepare_patient(raw, *, task='clinical_review', policy=None):
    PatientInput.model_validate(raw)
    digest(raw)  # Reject NaN and non-JSON data.
    policy = deepcopy(policy or {})
    evaluation = task != 'clinical_review'
    if evaluation and not (policy.get('reviewed') is True and policy.get('cutoff')
                           and isinstance(policy.get('exclude_pointers'), dict)):
        raise ValueError('evaluation_requires_reviewed_cutoff_and_exclusion_policy')
    cutoff = date.fromisoformat(policy['cutoff']) if policy.get('cutoff') else None
    if evaluation and not is_rwe_patient_json(raw):
        raise ValueError('legacy_evaluation_requires_explicit_dated_rwe_conversion')
    excluded = policy.get('exclude_pointers', {})
    if any(not p.startswith('/') or not reason for p, reason in excluded.items()):
        raise ValueError('exclusions_require_JSON_pointers_and_reasons')
    actions, matched = [], set()

    def filter_value(value, path=''):
        if path in excluded:
            matched.add(path)
            actions.append({'pointer': path, 'reason': excluded[path]})
            return None, False
        if isinstance(value, dict):
            output = {}
            for key, item in value.items():
                kept, include = filter_value(item, path + '/' + pointer_key(key))
                if include:
                    output[key] = kept
            return output, True
        if isinstance(value, list):
            # Filtering exclusions must not shift JSON array indices before date filtering.
            output = []
            for index, item in enumerate(value):
                kept, include = filter_value(item, path + '/' + str(index))
                if not include:
                    raise ValueError('exclude_fields_or_forms_not_individual_array_elements')
                output.append(kept)
            return output, True
        return value, True

    visible, _ = filter_value(deepcopy(raw))
    if set(excluded) != matched:
        raise ValueError('unmatched_exclusion_pointers: ' + str(sorted(set(excluded) - matched)))
    # Original row positions survive in a sidecar even after cutoff and trimming.
    row_origins = {}
    if is_rwe_patient_json(visible):
        for key, form in visible['forms'].items():
            path = '/forms/' + pointer_key(key) + '/records/'
            rows, origins = [], []
            for index, row in enumerate(form['records']):
                try:
                    when = date.fromisoformat(row.get('visit_date', ''))
                except (TypeError, ValueError):
                    when = None
                reason = None
                if cutoff and when and when > cutoff:
                    reason = 'after_cutoff'
                elif cutoff and when is None and evaluation and key not in policy.get('undated_forms_allowed', []):
                    reason = 'date_unknown_not_confirmed_available'
                if reason:
                    actions.append({'pointer': path + str(index), 'reason': reason})
                else:
                    rows.append(row)
                    origins.append(path + str(index))
            form['records'] = rows
            form['record_count'] = len(rows)
            row_origins[key] = origins
        # Duplicated/export-derived views can carry post-cutoff facts; forms are authoritative.
        for key in ('timeline',):
            if key in visible:
                visible.pop(key)
                actions.append({'pointer': '/' + key, 'reason': 'derived_view_use_authoritative_forms'})
        demographics = visible.get('demographics')
        if isinstance(demographics, dict):
            dicom = visible['forms'].get('dicom_basic_info', {}).get('records', [])
            aliases = {'身高_cm': '身高', '体重_kg': '体重'}
            for key in list(demographics):
                reason = None
                if cutoff and evaluation:
                    reason = 'undated_derived_view_not_confirmed_at_cutoff'
                elif dicom and aliases.get(key, key) in dicom[0] and dicom[0][aliases.get(key, key)] == demographics[key]:
                    reason = 'duplicate_authoritative_form_field'
                if reason:
                    demographics.pop(key)
                    actions.append({'pointer': '/demographics/' + pointer_key(key), 'reason': reason})
        if cutoff and evaluation:
            for name, asset in list((visible.get('imaging') or {}).items()):
                try:
                    when = date.fromisoformat(asset.get('visit_date', asset.get('date', '')))
                except (TypeError, ValueError):
                    when = None
                if (when and when > cutoff) or (when is None and name not in policy.get('undated_images_allowed', [])):
                    visible['imaging'].pop(name)
                    actions.append({'pointer': '/imaging/' + pointer_key(name),
                                    'reason': 'image_after_cutoff_or_date_unconfirmed'})
    elif policy.get('trim_mode', 'none') != 'none':
        raise ValueError('trimming_requires_RWE_v2')
    trim_manifest = None
    mode = policy.get('trim_mode', 'none')
    if is_rwe_patient_json(visible) and mode != 'none':
        visible, trim_manifest = trim_patient(visible, mode=mode)
        for key, form in trim_manifest['forms'].items():
            old = row_origins[key]
            for row in form['rows']:
                if row['output_row_index'] is None:
                    actions.append({'pointer': old[row['source_row_index']], 'reason': 'trim_omitted_visit'})
                else:
                    for field in row['omitted_fields']:
                        actions.append({'pointer': old[row['source_row_index']] + '/' + pointer_key(field),
                                        'reason': 'trim_omitted_field'})
            row_origins[key] = [old[r['source_row_index']] for r in form['rows']
                                if r['output_row_index'] is not None]
    PatientInput.model_validate(visible)
    facts, metadata, images = [], [], []
    source_hash, visible_hash = digest(raw), digest(visible)

    def contextual_leaves(value, path, context):
        if isinstance(value, dict):
            enriched = {**context, **{k: v for k, v in value.items() if k in {
                '名称', '时间', '日期', '检查日期', '单位', '参考范围', '方法', '样本', '版本',
                '教育校正', '语言', '检测平台', '模态'}}}
            for key, item in value.items():
                yield from contextual_leaves(item, path + '/' + pointer_key(key), enriched)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from contextual_leaves(item, path + '/' + str(index), context)
        else:
            yield path, value, context

    def append_fields(value, path, context):
        for pointer, item, field_context in contextual_leaves(value, path, context):
            key = pointer.rsplit('/', 1)[-1]
            if key in {'record_id', 'patient_id', 'patientId', 'visit_date', '患者ID'}:
                metadata.append(pointer)
                continue
            if key in {'路径', '报告路径', '文件路径', '附件路径', 'path'}:
                images.append({'path': item, 'pointer': pointer, 'context': field_context})
                metadata.append(pointer)
                continue
            ref = 'P' + str(len(facts) + 1).zfill(5)
            original = pointer
            if pointer.startswith('/forms/'):
                for form_key, origins in row_origins.items():
                    prefix = '/forms/' + pointer_key(form_key) + '/records/'
                    if pointer.startswith(prefix):
                        index, _, suffix = pointer[len(prefix):].partition('/')
                        original = origins[int(index)] + ('/' + suffix if suffix else '')
                        break
            facts.append({'id': ref, 'pointer': pointer, 'original_pointer': original,
                          'value': item, 'context': field_context, 'input_hash': source_hash})

    if is_rwe_patient_json(visible):
        for key, form in visible['forms'].items():
            for index, row in enumerate(form['records']):
                append_fields(row, '/forms/' + pointer_key(key) + '/records/' + str(index),
                              {'form': form.get('form_name', key), 'date': row.get('visit_date')})
        for name, asset in (visible.get('imaging') or {}).items():
            images.append({'modality': name, **asset})
            for field in ('报告原文', 'report_text', 'shared_tool_result'):
                if field in asset:
                    append_fields(asset[field], '/imaging/' + pointer_key(name) + '/' + field,
                                  {'form': name, 'date': asset.get('visit_date')})
        # Unknown root clinical extensions must not silently vanish.
        known = {'schema_version', 'patient', 'project', 'exported_at', 'forms', 'imaging',
                 'data_quality', 'timeline', 'demographics', '患者ID'}
        for key, value in visible.items():
            if key not in known:
                if evaluation:
                    raise ValueError('evaluation_unknown_root_requires_explicit_mapping_or_exclusion: ' + key)
                append_fields(value, '/' + pointer_key(key), {'form': key})
        if visible.get('demographics'):
            append_fields(visible['demographics'], '/demographics', {'form': '基本信息'})
    else:
        append_fields(visible, '', {'form': 'legacy'})
    text = '\n'.join('[' + f['id'] + '] ' + json.dumps(
        {'来源': f['context'], '字段': f['pointer'].rsplit('/', 1)[-1], '值': f['value']},
        ensure_ascii=False, allow_nan=False) for f in facts)
    restrictions = visible.get('data_quality', {})
    text += '\n【输入限制】' + json.dumps(restrictions, ensure_ascii=False)
    if images:
        text += '\n【影像】仅路径不构成影像结论；未提供报告/共享工具结果的影像不可解读。'
    rendered = {f['original_pointer'] for f in facts}
    coverage = []
    for pointer, value in leaves(raw):
        if pointer in rendered:
            reason = 'rendered_fact'
        else:
            action = next((a for a in actions if pointer == a['pointer'] or
                           pointer.startswith(a['pointer'] + '/')), None)
            if action:
                reason = action['reason']
            elif pointer.startswith('/imaging/') or pointer.endswith(('/路径', '/报告路径', '/文件路径', '/附件路径', '/path')):
                reason = 'attachment_metadata_not_imaging_interpretation'
            elif pointer.startswith('/data_quality/'):
                reason = 'input_restriction'
            elif pointer.startswith('/forms/') and '/records/' not in pointer:
                reason = 'form_metadata'
            elif pointer.startswith(('/patient/', '/project/')) or pointer in {'/患者ID', '/schema_version', '/exported_at'}:
                reason = 'identity_or_export_metadata'
            elif pointer.rsplit('/', 1)[-1] in {'record_id', 'patient_id', 'patientId', 'visit_date'}:
                reason = 'record_metadata_date_in_context'
            else:
                raise ValueError('unaccounted_input_field: ' + pointer)
        coverage.append({'pointer': pointer, 'disposition': reason})
    return {'visible': visible, 'text': text, 'sources': facts, 'images': images,
            'manifest': {'input_hash': source_hash, 'visible_hash': visible_hash,
                         'text_hash': digest(text), 'policy': policy, 'task': task,
                         'exclusions': actions, 'metadata_pointers': metadata,
                         'trim_manifest': trim_manifest, 'row_origins': row_origins,
                         'field_coverage': coverage, 'accounted_leaf_count': len(coverage),
                         'fact_count': len(facts), 'schema': 'medgraphrag-input-v1',
                         'limitations': ['Input conversion coverage is distinct from LLM graph extraction recall.']}}
