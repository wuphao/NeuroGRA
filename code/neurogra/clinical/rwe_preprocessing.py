"""Reusable, source-preserving RWE export preprocessing; no network or model calls."""
from copy import deepcopy
from datetime import date

from .schemas import PatientInput


SCORES = {
    'moca': {'MoCA 汇总得分'},
    'mmse': {'MMSE总分'},
    'faq': {'总分'},
    'cdr': {'总体评分', 'CDRSB总分'},
    'adas': {'ADAS-Cog 11 项总分', 'ADAS-Cog 13 项总分'},
}
META = {'record_id', 'visit_date', 'patient_id', 'patientId'}
NOTE_FIELDS = frozenset({'备注', 'comment', 'remark', 'remarks'})


def is_note_field(key):
    """Only explicit remark columns; do not remove clinical fields containing 备注."""
    return isinstance(key, str) and key.strip().casefold() in NOTE_FIELDS


def visit_date(row):
    try:
        return date.fromisoformat(row['visit_date'])
    except (KeyError, TypeError, ValueError):
        return None


def _without_notes(value, path='', removed=None):
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            pointer = path + '/' + str(key).replace('~', '~0').replace('/', '~1')
            if is_note_field(key):
                removed.append(pointer)
            else:
                output[key] = _without_notes(item, pointer, removed)
        return output
    if isinstance(value, list):
        return [_without_notes(item, path + '/' + str(i), removed) for i, item in enumerate(value)]
    return deepcopy(value)


def trim_patient(raw, mode='short', *, complete_detail_years=()):
    """Create a new patient view and audit manifest, independent of patient/year.

    short: all original totals, latest full visit and optional full-detail years.
    smoke: latest full visit only. Both preserve undated rows and other forms.
    none: remove remark columns only, preserving the full export and timeline.
    No values, totals or missing dates are inferred or recalculated.
    """
    if mode not in {'none', 'smoke', 'short'}:
        raise ValueError('unknown_trim_mode')
    years = tuple(sorted(set(complete_detail_years)))
    if any(type(year) is not int or not 1 <= year <= 9999 for year in years):
        raise ValueError('invalid_complete_detail_year')
    if years and mode != 'short':
        raise ValueError('detail_years_require_short_mode')
    PatientInput.model_validate(raw)
    if raw.get('schema_version') != '2.0':
        raise ValueError('requires_rwe_export_v2')
    removed_notes = []
    result = _without_notes(raw, removed=removed_notes)
    manifest = {'mode': mode, 'removed_root_views': [], 'removed_note_fields': removed_notes,
                'policy': {'complete_detail_years': list(years),
                           'latest_complete_per_form': mode != 'none',
                           'preserve_undated': True, 'preserve_other_forms': True,
                           'recalculate_scores': False, 'remove_note_columns': True,
                           'score_fields': {key: sorted(value) for key, value in SCORES.items()}},
                'forms': {}}
    if mode != 'none' and 'timeline' in result:
        result.pop('timeline')  # Redundant view; patient intake reads forms directly.
        manifest['removed_root_views'].append('timeline')
    for name, form in result['forms'].items():
        source_rows = raw['forms'][name]['records']
        latest = max((d for row in source_rows if (d := visit_date(row))), default=None)
        output, actions = [], []
        for index, row in enumerate(form['records']):
            d = visit_date(row)
            if (mode == 'none' or name not in SCORES or d is None or d == latest
                    or (mode == 'short' and d.year in years)):
                kept, action = row, 'full_record'
            elif mode == 'short':
                kept = {key: value for key, value in row.items() if key in META or key in SCORES[name]}
                action = 'original_scores_only'
            else:
                kept, action = None, 'omitted_visit'
            output_index = len(output) if kept is not None else None
            if kept is not None:
                output.append(kept)
            source = source_rows[index]
            actions.append({'source_row_index': index, 'output_row_index': output_index,
                'record_id': source.get('record_id'), 'visit_date': source.get('visit_date'),
                'action': action, 'kept_fields': list(kept) if kept is not None else [],
                'omitted_fields': [key for key in source if kept is None or key not in kept],
                'undated_record_preserved': d is None})
        form['records'], form['record_count'] = output, len(output)
        manifest['forms'][name] = {'source_records': len(source_rows), 'output_records': len(output),
                                 'latest_date': latest.isoformat() if latest else None, 'rows': actions}
    quality = result.setdefault('data_quality', {})
    quality['record_counts'] = {form.get('form_name', key): len(form['records'])
                              for key, form in result['forms'].items()}
    if mode != 'none':
        rule = ('各量表仅保留最近明确日期的完整记录' if mode == 'smoke' else
                '各量表保留全部访视原始总分、最近明确日期的完整分项'
                + (f'，另外保留年份 {list(years)} 的完整分项' if years else ''))
        quality.setdefault('warnings', []).append(
            '本文件为裁剪后的患者资料子集，不能视为患者完整病史；' + rule +
            '。未注明日期的记录和其他表单保留（备注列除外）；删减字段不代表未检查或结果阴性。'
            '详见同名 manifest.json 裁剪清单。')
    PatientInput.model_validate(result)
    return result, manifest
