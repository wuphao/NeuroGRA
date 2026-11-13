"""Explicit patient comparisons and bounded, read-only cohort calculations."""

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from neurogra.clinical.storage import BudgetExceeded
from neurogra.clinical.utils import dumps, fingerprint

from .patient_comparison import read_scale
from .patient_workspace import PRIVATE_FIELD, WorkspaceError, read_rows


def _affirmative_operations(text):
    """Exclude explicitly declined operations, retaining independently requested clauses."""
    operation = r'比较|对比|统计|计算|汇总'
    clauses = re.split(r'[，,；;。！？?!\n]|但是|而是|但(?=\s*(?:' + operation + r'))|'
                       r'只(?=\s*(?:读取|分析|解释|' + operation + r'))', text)
    declined = r'(?:不要|不用|无需|不需要|暂不|别)\s*(?:再|继续)?\s*(?:' + operation + r')'
    return '；'.join(clause for clause in clauses if not re.search(declined, clause))


def is_cohort_question(text):
    text = _affirmative_operations(text)
    scope = r'所有患者|全部患者|全体患者|项目中|本项目|项目内|人群|群体|队列|患者总体'
    operation = r'统计|人数|多少|占比|比例|百分比|平均|均值'
    return bool(re.search(scope, text) and re.search(operation, text))


def is_cross_question(text, selected):
    text = _affirmative_operations(text)
    explicit = re.search(r'两位患者|两个患者|两名患者|这些患者|两位病人|两个病人|'
                         r'另一位患者|另一个患者|其他患者|这两位|这两人|他们|她们|'
                         r'患者.{0,10}(?:和|与).{0,10}患者', text)
    compare = re.search(r'比较|对比|差异|异同|相比|差别', text)
    longitudinal = re.search(r'最近两次|上次|这次|历次|趋势|前后', text)
    return bool(compare and (explicit or selected and not longitudinal))


def question_clauses(text):
    """Split conjunctions only when they introduce a new requested action."""
    action = (r'(?:解释|解读|分析|评估|比较|对比|统计|计算|汇总|生成|出具|撰写|'
              r'提供|查看|查询|检索|给出|给我|做|写)')
    prefix = r'(?:(?:请|再|进一步|分别|帮我|帮忙)\s*)*'
    scope = r'(?:在(?:本项目|项目中|项目内|队列|群体).{0,8})?'
    separators = (r'[，,；;。！？?!\n]|(?:并且|同时|另外|然后|以及|(?<!合)并)'
                  r'(?=\s*' + prefix + scope + action + ')')
    return [part.strip() for part in re.split(separators, text) if part.strip()]


def split_comparison_and_cohort(workspace, text, selected):
    """Find independently executable comparison and cohort clauses."""
    if not (is_cross_question(text, selected) and is_cohort_question(text)):
        return None
    clauses = question_clauses(text)
    comparisons = [part for part in clauses if is_cross_question(part, selected)]
    cohorts = [part for part in clauses if is_cohort_question(part)]
    if not comparisons or not cohorts:
        return ('', '')
    comparison, cohort = '；'.join(comparisons), '；'.join(cohorts)
    global_forms = matching_forms(workspace, text)
    if len(global_forms) == 1:
        if not matching_forms(workspace, comparison):
            comparison += ' ' + global_forms[0].key
        if not matching_forms(workspace, cohort):
            cohort += ' ' + global_forms[0].key
    return comparison, cohort


def roster_fingerprint(workspace, roster):
    return fingerprint([workspace.source_id, str(workspace.schema.project_id),
                        sorted(patient['patientKey'] for patient in roster['patients'])])


def matching_forms(workspace, text):
    matched = []
    lowered = text.lower()
    for form in workspace.schema.forms:
        aliases = {'csf': '脑脊液', 'plasma': '血浆', 'apoe': '基因'}.get(form.key)
        if form.key.lower() in lowered or form.name.lower() in lowered or aliases and aliases in text:
            matched.append(form)
    return matched


def select_form(workspace, text):
    matched = matching_forms(workspace, text)
    if len(matched) != 1:
        raise WorkspaceError('请明确指定一个要比较或统计的表单，例如 MoCA。', 400)
    return matched[0]


def compare(workspace, session, primary, other_keys, text, check, *, read_patient=None):
    form = select_form(workspace, text)
    patients = [primary] + [workspace.patient(session, key, refresh=True) for key in other_keys]
    sources, lines, scale_results, material = [], [], [], []
    incomplete = False
    for patient in patients:
        check()
        read = lambda: workspace.form(session, patient['patientKey'], form.key, with_sources=True)
        data = read_patient('comparison:' + form.key, read) if read_patient is not None else read()
        rows = data['records']
        if any(not isinstance(row.get('source_fields'), list) for row in rows):
            raise WorkspaceError('比较来源缺少可核实的原始字段。', 503)
        canonical = [{'record_id': row.get('record_id'), 'date': row.get('date'),
                      'source_fields': sorted(row.get('source_fields', []), key=dumps)} for row in rows]
        material.append({'patient': patient['patientKey'], 'records': sorted(canonical, key=dumps)})
        if not rows:
            incomplete = True
            lines.append(f"{patient['name']}（{patient['id']}）：{form.name}无记录。")
            continue
        dates = [row.get('date') or '' for row in rows]
        try:
            for day in dates:
                if day:
                    date.fromisoformat(day)
        except ValueError:
            incomplete = True
            lines.append(f"{patient['name']}（{patient['id']}）：访视日期无法识别，请指定访视。")
            continue
        if len(rows) > 1 and any(not day for day in dates):
            incomplete = True
            lines.append(f"{patient['name']}（{patient['id']}）：部分访视日期缺失，请指定访视。")
            continue
        newest = rows[0].get('date') or ''
        same_day = [row for row in rows if (row.get('date') or '') == newest]
        if not newest and len(rows) > 1 or len(same_day) > 1:
            incomplete = True
            lines.append(f"{patient['name']}（{patient['id']}）：最近记录日期不明确或同日多条，请指定访视。")
            continue
        row = rows[0]
        fields = [[str(name), str(value)] for name, value in row['fields'] if not PRIVATE_FIELD.search(str(name))]
        displayed = fields[:30]
        detail = '；'.join(f'{name}：{value}' for name, value in displayed)
        if len(fields) > len(displayed):
            incomplete = True
            detail += f'；仅显示 {len(displayed)}/{len(fields)} 个字段'
        if not fields or not newest:
            incomplete = True
        label = f"{patient['name']}（{patient['id']}）"
        lines.append(f'{label} · {newest or "日期未记录"}：{detail}')
        sources.append({'title': f'{label} · {form.name}', 'text': detail, 'dates': [newest] if newest else [],
                        'refs': [str(row['record_id'])] if row.get('record_id') else []})
        if form.key in {'moca', 'mmse'}:
            records = read_scale({'records': [row]}, form.comparison,
                                 {'start': None, 'end': None, 'last': None})
            if len(records) == 1 and not records[0].get('excluded'):
                scale_results.append(records[0])
    comparable = False
    if len(patients) == 2 and len(scale_results) == 2:
        first, second = scale_results
        if (first.get('version'), first.get('scoring')) == (second.get('version'), second.get('scoring')):
            delta = Decimal(second['value']) - Decimal(first['value'])
            lines.append(f'相同量表版本与评分口径下，后一位患者减前一位为 {delta:+} 分。')
            comparable = True
    if form.key in {'moca', 'mmse'} and not comparable:
        incomplete = True
        lines.append('量表记录、版本或评分口径不足以计算可核实的两人差值。' if len(patients) == 2 else
                     '量表差值目前仅支持两位患者，请分别选择两位并核对评分口径。')
    lines.append('请结合各次访视、量表版本、单位和临床背景判断可比性；差异不代表病因或诊断。')
    check()
    for patient in patients:
        workspace.patient(session, patient['patientKey'], refresh=True)
    source_fingerprint = fingerprint({'form': form.model_dump(mode='json'),
                                      'patients': material})
    return {'text': '\n'.join(lines), 'sources': sources, 'patients': [patient['patientKey'] for patient in patients],
            'source_fingerprint': source_fingerprint, 'result_status': 'partial' if incomplete else 'completed'}


def _field(workspace, reader, form, text):
    metadata = reader.query('/form/getFormDetailsBatch', {'formIds': [form.form_id]})
    definition = metadata.get(str(form.form_id), {}) if isinstance(metadata, dict) else {}
    fields = definition.get('fields') if isinstance(definition, dict) else None
    if not isinstance(fields, list):
        raise WorkspaceError('表单字段定义不可用，无法统计。', 503)
    matches = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        key = field.get('enName', field.get('en_name'))
        label = field.get('name') or field.get('cnName') or key
        if (not isinstance(key, str) or not key or not isinstance(label, str) or
                PRIVATE_FIELD.search(label) or PRIVATE_FIELD.search(key)):
            continue
        explicit = label in text or (len(key) >= 3 and key.lower() in text.lower())
        score = form.key in {'moca', 'mmse'} and label in {'总分', '评分', '得分', '总得分'}
        if explicit or score and re.search(r'分|评分|得分|低于|高于|小于|大于|[<>]', text):
            unit = field.get('unit') or field.get('unitName') or field.get('measurementUnit')
            matches.append((key, label, unit.strip() if isinstance(unit, str) and unit.strip() else None))
    if len(matches) != 1:
        raise WorkspaceError('请明确指定表单中的一个统计字段；字段不明确或重名时不能计算。', 400)
    return matches[0]


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    if not re.fullmatch(r'-?\d{1,18}(?:\.\d{1,8})?', text):
        return None
    try:
        number = Decimal(text)
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def _operator(text):
    patterns = [('le', r'不高于|不大于|小于等于|≤|<='), ('ge', r'不低于|不小于|大于等于|≥|>='),
                ('lt', r'低于|小于|少于|<'), ('gt', r'高于|大于|超过|>')]
    for kind, pattern in patterns:
        match = re.search('(?:' + pattern + r')\s*(-?\d{1,18}(?:\.\d{1,8})?)(?![\d.eE])', text)
        if match:
            return kind, Decimal(match.group(1))
    return None, None


def _read_batches(workspace, session, patient_keys, form, *, read_patient=None):
    reader = workspace.reader(session)
    grouped, failed, exhausted = defaultdict(list), set(), set()
    for offset in range(0, len(patient_keys), 100):
        keys = patient_keys[offset:offset + 100]
        if exhausted:
            failed.update(keys)
            exhausted.update(keys)
            continue
        try:
            read = lambda: reader.query('/form/queryDataBatch', {'forms': [{'formId': form.form_id}],
                'patientIds': [int(key) for key in keys], 'page': 1, 'pageSize': 0})
            data = read_patient('cohort:' + form.key, read) if read_patient is not None else read()
            rows = read_rows(data.get(str(form.form_id)) if isinstance(data, dict) else None)
            for row in rows:
                key = str(row.get('patient_id', row.get('patientId')))
                if key not in keys:
                    raise WorkspaceError('统计来源包含范围外患者，已停止计算。', 403)
                grouped[key].append(row)
        except BudgetExceeded:
            failed.update(keys)
            exhausted.update(keys)
        except WorkspaceError as error:
            if error.status in {401, 403, 404, 409}:
                raise
            failed.update(keys)
    return grouped, failed, exhausted


def aggregate(workspace, session, text, check, *, read_patient=None):
    roster = workspace.snapshot(session, refresh=True)
    keys = sorted(patient['patientKey'] for patient in roster['patients'])
    binding = roster_fingerprint(workspace, roster)
    if not keys:
        return {'text': '当前账号在该项目的可见患者列表为空。', 'roster_hash': binding, 'form_key': None,
                'source_fingerprint': fingerprint([binding, 'roster'])}
    form_mentioned = bool(matching_forms(workspace, text))
    if not form_mentioned:
        if re.search(r'平均|均值|占比|比例|百分比|低于|高于|[<>]', text):
            raise WorkspaceError('请指定统计表单、字段和筛选条件。', 400)
        return {'text': f'当前账号在项目 {workspace.schema.project_id} 可见的患者列表共 {len(keys)} 人。'
                        '列表总数按 RWE 返回值核对；跨接口一致性需由数据源保证。',
                'roster_hash': binding, 'form_key': None,
                'source_fingerprint': fingerprint([binding, 'roster'])}
    form = select_form(workspace, text)
    field, label, unit = _field(workspace, workspace.reader(session), form, text)
    operator, threshold = _operator(text)
    average = bool(re.search(r'平均|均值', text))
    count_present = bool(re.search(r'有记录|有结果|已记录|已填写', text))
    if average and operator is not None:
        raise WorkspaceError('请明确分别查询筛选人数或平均值。', 400)
    if not average and operator is None and not count_present:
        raise WorkspaceError('请提供明确的比较条件，例如“总分低于 26”，或选择计算平均值。', 400)
    if (average or operator is not None) and form.key not in {'moca', 'mmse'} and not unit:
        raise WorkspaceError('该字段未提供可核实的统一单位，无法进行数值统计。', 400)
    grouped, failed, exhausted = _read_batches(workspace, session, keys, form, read_patient=read_patient)
    values, missing, ambiguous = [], 0, 0
    for key in keys:
        check()
        if key in failed:
            continue
        rows = grouped[key]
        if not rows:
            missing += 1
            continue
        if form.date_field:
            dates = [str(row.get(form.date_field) or '')[:10] for row in rows]
            try:
                for day in dates:
                    date.fromisoformat(day)
            except ValueError:
                ambiguous += 1
                continue
            latest = max(dates)
            rows = [row for row, day in zip(rows, dates, strict=True) if day == latest]
        if len(rows) != 1:
            ambiguous += 1
            continue
        raw_value = rows[0].get(field)
        if raw_value is None or raw_value == '':
            missing += 1
        elif count_present and not average and operator is None:
            values.append(Decimal(1))
        else:
            value = _number(raw_value)
            row_unit = next((str(rows[0][key]).strip() for key in ('unit', '单位', '单位名称')
                             if rows[0].get(key) not in (None, '')), None)
            if value is None:
                missing += 1
            elif row_unit and unit and row_unit != unit:
                ambiguous += 1
            else:
                values.append(value)
    if roster_fingerprint(workspace, workspace.snapshot(session, refresh=True)) != binding:
        raise WorkspaceError('统计期间患者授权范围发生变化，请重新统计。', 409)
    details = (f'授权列表 {len(keys)} 人；可用 {len(values)} 人；无记录或字段缺失 {missing} 人；'
               f'日期不明或同日多条 {ambiguous} 人；读取失败 {len(failed - exhausted)} 人。')
    if exhausted:
        details += f' 另有 {len(exhausted)} 人因读取预算耗尽尚未查询。'
    if not values:
        result = '没有足够的可计算数值。'
    elif count_present and not average and operator is None:
        result = f'{form.name} {label}有记录的患者为 {len(values)} 人。'
    elif average:
        result = f'{form.name} {label}的平均值为 {sum(values) / len(values):.2f}{unit or ""}，分母为 {len(values)} 人。'
    else:
        operations = {'lt': lambda value: value < threshold, 'le': lambda value: value <= threshold,
                      'gt': lambda value: value > threshold, 'ge': lambda value: value >= threshold}
        matched = sum(operations[operator](value) for value in values)
        result = f'{form.name} {label}符合条件的患者 {matched} 人，占有效数值 {len(values)} 人的 {matched / len(values) * 100:.2f}%。'
    caveat = ('未统一量表版本或教育校正' if form.key in {'moca', 'mmse'} else '未自动换算单位或统一检测方法')
    source_fingerprint = fingerprint({'roster': binding, 'form': form.model_dump(mode='json'),
        'field': [field, label, unit], 'records': {key: sorted(grouped[key], key=dumps) for key in keys},
        'failed': sorted(failed), 'budget_skipped': sorted(exhausted)})
    return {'text': result + '\n' + details + f'\n每位患者取最近一次可确定的记录；{caveat}，'
            '结果仅用于资料统计。跨接口一致性需由 RWE 保证。',
            'roster_hash': binding, 'form_key': form.key, 'source_fingerprint': source_fingerprint,
            'result_status': 'partial' if failed else 'completed', 'budget_exhausted': bool(exhausted)}


def reauthorize_cohort(workspace, session, binding):
    roster = workspace.snapshot(session, refresh=True)
    if (binding['source_id'] != workspace.source_id or str(binding['project_id']) != str(workspace.schema.project_id)
            or roster_fingerprint(workspace, roster) != binding['roster_hash']):
        raise WorkspaceError('群体统计的授权范围已变化，无法展示旧结果。', 403)
    if binding.get('form_key'):
        form = next((item for item in workspace.schema.forms if item.key == binding['form_key']), None)
        if form is None:
            raise WorkspaceError('统计表单已不可用。', 403)
        _, failed, _ = _read_batches(workspace, session, [patient['patientKey'] for patient in roster['patients']], form)
        if failed:
            raise WorkspaceError('统计资料授权或读取状态不可验证。', 403)
