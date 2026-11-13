"""Read-only longitudinal comparison and dependent evidence lookup in patient chat."""

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, timedelta
from decimal import Decimal

from neurogra.clinical.utils import fingerprint, parse_time

from .patient_workspace import PRIVATE_FIELD, WorkspaceError

SCALES = {'moca': 'MoCA', 'mmse': 'MMSE'}
LAB_FORMS = {'plasma': '血浆', 'csf': '脑脊液'}
ISO_DATE = r'\d{4}-\d{2}-\d{2}'
TREND = r'趋势|走势|变化|波动|差多少|相差|差值|比.{0,12}(多|少|高|低)|对比|比较|下降|上升|历次'
WINDOW = r'去年|今年|前年|近[一二两三\d]+[年月天]|最近[一二两三\d]+[次年月天]|上一次(复诊|访视)|(?:19|20)\d{2}年'
SCORE_FIELDS = ('总分', '量表总分', '原始总分', '总得分', 'moca总分', 'mmse总分', 'total_score', 'totalscore')
VERSION_FIELDS = ('版本', '量表版本', '问卷版本', 'scale_version')
SCORING_FIELDS = ('评分口径', '计分口径', '计分方式', '教育校正', '教育校正方式', 'scoring_method')
MISSING = {'', '—', '-', '未知', '未记录', '不详', 'null', 'none', 'n/a'}
NUMBERS = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
EVIDENCE_SECONDS = 45


class ComparisonWorkflow:
    """Record the plan and the evidence actually consumed by each task."""

    def __init__(self, request, lab_keys, previous, route, model_calls):
        self.route, self.model_calls = route, model_calls
        self.started = time.monotonic()
        self.tasks = {}
        self.previous = previous.get('tasks', {}) if previous.get('schema_version') == 1 else {}
        self.add('scale', '读取量表', 'cognition', {'scale': request['scale'], 'window': request['window']}, {}, 1)
        self.add('compare', '计算量表变化', 'cognition', {'scale': request['scale']}, {'scale': ['source_version']})
        dependencies = {'compare': ['result_digest']}
        if request['cross']:
            self.add('visit', '确认访视', 'cognition',
                     {key: request[key] for key in ('anchor', 'direction', 'choose_latest')}, {'compare': ['candidates']})
            dependencies['visit'] = ['date', 'evidence_refs']
            for key in lab_keys:
                self.add(key, LAB_FORMS[key], 'laboratory', {'form': key, 'radius': request['radius']},
                         {'visit': ['date']}, 1)
                dependencies[key] = ['source_version', 'status']
        self.add('verify', '核验与汇总', 'review', {}, dependencies)

    def add(self, key, title, role, inputs, dependencies, cost=0):
        self.tasks[key] = {'title': title, 'role': role, 'inputs': inputs, 'depends_on': dependencies,
                           'estimated_reads': cost, 'status': 'waiting', 'action': 'blocked', 'reads': 0}

    def finish(self, key, status, *, action='executed', reads=0, output=None, detail=None):
        task = self.tasks[key]
        dependencies = {parent: {field: self.tasks[parent].get('output', {}).get(field) for field in fields}
                        for parent, fields in task['depends_on'].items()}
        task.update(status=status, action=action, reads=reads, output=output or {},
                    input_digest=fingerprint([task['inputs'], dependencies]))
        if detail:
            task['detail'] = detail

    def record(self):
        return {'schema_version': 1, 'source': 'rwe', 'route': self.route, 'model_calls': self.model_calls,
                'tasks': self.tasks, 'elapsed_ms': round((time.monotonic() - self.started) * 1000),
                'removed': [task['title'] for key, task in self.previous.items()
                            if key in LAB_FORMS and key not in self.tasks]}


def read_with_deadline(calls, deadline):
    """Leave late read-only results outside the current reply and cache."""
    if not calls or time.monotonic() >= deadline:
        return {}, 0
    pool = ThreadPoolExecutor(max_workers=2)
    futures = {}
    try:
        futures = {key: pool.submit(call) for key, call in calls.items()}
        done, _ = wait(futures.values(), timeout=max(0, deadline - time.monotonic()))
        return {key: future.result() for key, future in futures.items() if future in done}, len(futures)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def shift_months(day, months):
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    first = date(year, month + 1, 1)
    next_month = date(year + (month == 11), (month + 1) % 12 + 1, 1)
    return first.replace(day=min(day.day, (next_month - first).days))


def window_from_question(text, previous, today):
    window = dict(previous)
    dates = re.findall(ISO_DATE, text)
    if dates:
        try:
            if any(not 1900 <= date.fromisoformat(day).year <= 2099 for day in dates):
                raise ValueError
        except ValueError:
            raise ValueError('请使用有效的访视日期，例如 2025-06-01。') from None
    if len(dates) == 2 and re.search(r'到|至|~|～', text):
        first, last = map(date.fromisoformat, dates)
        if first > last:
            raise ValueError('时间范围的开始日期晚于结束日期。')
        window.update(start=first.isoformat(), end=last.isoformat(), last=None)
        return window, None
    count = re.search(r'(?:最近|近|过去)\s*([一二两三四五六七八九十\d]+)\s*(次|年|个月|月|天)', text)
    if count and count[2] == '次' and re.match(r'\s*(?:评分)?(?:下降|上升|降低|升高)', text[count.end():]):
        count = None
    if count:
        raw, unit = count.groups()
        n = int(raw) if raw.isdecimal() and len(raw) <= 4 else NUMBERS.get(raw, 0)
        if not 1 <= n <= {'次': 100, '年': 30, '个月': 360, '月': 360, '天': 10950}[unit]:
            raise ValueError('请指定有效的时间范围或访视次数。')
        if unit == '次':
            window['last'] = n
        else:
            start = today - timedelta(days=n - 1) if unit == '天' else shift_months(today, n * (12 if unit == '年' else 1))
            window.update(start=start.isoformat(), end=today.isoformat(), last=None)
    year_span = re.search(r'((?:19|20)\d{2})年?\s*(?:到|至|~|～)\s*((?:19|20)\d{2})年', text)
    if year_span and not count:
        if year_span[1] > year_span[2]:
            raise ValueError('时间范围的开始年份晚于结束年份。')
        window.update(start=year_span[1] + '-01-01', end=year_span[2] + '-12-31', last=None)
        return window, None
    year_match = re.search(r'((?:19|20)\d{2})年(?:\s*(上半年|下半年))?|((?:前|去|今)年)(上半年|下半年)?', text)
    if year_match and not count:
        year = int(year_match[1]) if year_match[1] else today.year - {'前年': 2, '去年': 1, '今年': 0}[year_match[3]]
        half = year_match[2] or year_match[4]
        window.update(start=date(year, 7 if half == '下半年' else 1, 1).isoformat(),
                      end=date(year, 6 if half == '上半年' else 12, 30 if half == '上半年' else 31).isoformat(), last=None)
    if re.search(r'和上一次|与上一次|上一次(?:复诊|访视)|前后两次', text):
        window['last'] = 2
    if re.search(r'全部|所有访视|所有记录', text):
        window = {'start': None, 'end': None, 'last': None}
    return window, dates[0] if len(dates) == 1 else None


def comparison_request(text, previous=None, *, today=None):
    if re.match(r'^(?:记住|忘记)[:：]|^(?:查看记忆|/memory)$', text):
        return None
    previous = previous or {}
    scales = [key for key in SCALES if re.search(rf'(?<![A-Za-z]){key}(?![A-Za-z])', text, re.I)]
    mentions_lab = re.search(r'检验|化验|生物标志物|血浆|脑脊液|CSF', text, re.I)
    time_phrase = re.search(WINDOW + '|' + ISO_DATE, text)
    trend = re.search(TREND, text)
    cross = bool(mentions_lab and (trend or re.search(r'同期|那次|同一天|同时|对应', text)))
    radius_match = re.search(r'前后\s*([一二两三四五六七八九十]|\d{1,2})\s*天', text)
    lab_revision = previous.get('cross') and (radius_match or mentions_lab and re.search(r'只看|只查|改查|改成', text))
    continuing = bool(previous and not scales and (cross or time_phrase or lab_revision or
                      re.fullmatch(r'(那|再看|看看)?(趋势|变化|差值)(呢|怎么样|[？?])?', text) or
                      re.search(r'全部|所有访视|所有记录', text)) and
                      (len(text) <= 36 or re.search(r'改成|只看|改看|同期|那次', text)))
    if cross and not scales and not previous:
        raise ValueError('请先说明要按哪张量表确定访视，例如 MoCA 或 MMSE。')
    if not (scales and (trend or cross or previous and time_phrase and re.search(r'只看|改成|改看', text)) or continuing):
        return None
    if len(scales) > 1:
        raise ValueError('请指定要比较的一个量表，例如只比较 MoCA 总分。')
    if re.search(r'分项|子项|分领域', text):
        raise ValueError('当前纵向比较支持量表已记录总分。请指定要比较的量表总分。')
    scale = scales[0] if scales else previous['scale']
    retain = previous if scale == previous.get('scale') else {}
    window, anchor = window_from_question(text, retain.get('window', {'start': None, 'end': None, 'last': None}),
                                          today or date.today())
    cross |= bool(retain.get('cross') and continuing and not re.search(r'只看(?:总分|量表)|不看检验', text))
    choose_latest = bool(re.search(r'最近一次|最新一次|最后一次', text))
    if not anchor and window == retain.get('window') and cross and not choose_latest:
        anchor = retain.get('anchor')
    lab_keys = [key for key, label in LAB_FORMS.items() if label in text or key in text.lower()]
    direction = ('up' if re.search(r'上升|升高', text) else 'down' if re.search(r'下降|降低', text)
                 else retain.get('direction', 'down'))
    raw_radius = radius_match[1] if radius_match else None
    radius = NUMBERS.get(raw_radius, raw_radius) if raw_radius is not None else retain.get('radius', 0)
    return {'scale': scale, 'window': window, 'cross': cross, 'anchor': anchor,
            'lab_keys': lab_keys or retain.get('lab_keys') or list(LAB_FORMS), 'direction': direction,
            'radius': int(radius), 'choose_latest': choose_latest}


def read_field(fields, aliases):
    for alias in aliases:
        matches = [(key, value) for key, label, value in fields if alias.casefold() in {key.casefold(), label.casefold()}
                   and str(value).strip().casefold() not in MISSING]
        if matches:
            return matches[0] if len({str(value) for _, value in matches}) == 1 else (None, None)
    return None, None


def read_scale(form, policy, window):
    rows = []
    for record in form['records']:
        fields = record.get('source_fields', [[key, key, value] for key, value in record['fields']])
        score_key, score = read_field(fields, (policy.score_field,) if policy else SCORE_FIELDS)
        _, version = read_field(fields, (policy.version_field,) if policy and policy.version_field else VERSION_FIELDS)
        _, scoring = read_field(fields, (policy.scoring_field,) if policy and policy.scoring_field else SCORING_FIELDS)
        version = version or (policy.fixed_version if policy else None)
        scoring = scoring or (policy.fixed_scoring if policy else None)
        stamp = parse_time(record.get('date'))
        day = stamp.value if stamp.precision == 'day' else None
        if day and ((window['start'] and day < window['start']) or (window['end'] and day > window['end'])):
            continue
        value = Decimal(str(score).strip()) if re.fullmatch(r'[0-9]{1,2}(?:\.[0-9]{1,3})?', str(score).strip()) else None
        problem = ('缺少有效访视日期' if not day else '缺少有效总分' if value is None or value > 30
                   else '量表版本或评分口径待核实' if not version or not scoring else None)
        rows.append({'date': day, 'record_id': record.get('record_id'), 'field': score_key,
                     'value': number(value) if value is not None else str(score if score is not None else '—')[:100],
                     'version': str(version)[:100] if version else None,
                     'scoring': str(scoring)[:100] if scoring else None, 'excluded': problem})
    rows.sort(key=lambda row: (row['date'] or '', row['record_id'] or ''))
    if window['last']:
        dates = set(sorted({row['date'] for row in rows if row['date']})[-window['last']:])
        rows = [row for row in rows if not row['date'] or row['date'] in dates]
    return rows


def number(value):
    return format(value.normalize(), 'f')


def signed(value):
    return ('+' if value > 0 else '') + number(value)


def compute(rows):
    by_date = {}
    for row in rows:
        if row['date']:
            by_date.setdefault(row['date'], []).append(row)
    valid = []
    for group in by_date.values():
        signature = {(row['value'], row['version'], row['scoring']) for row in group}
        if len(signature) > 1 or len(group) > 1 and any(row['excluded'] for row in group):
            for row in group:
                row['excluded'] = '同一访视日期存在冲突或待核实记录'
        elif not group[0]['excluded']:
            valid.append(group[0])
            for row in group[1:]:
                row['excluded'] = '同日同值的重复记录'
    signature = (valid[-1]['version'], valid[-1]['scoring']) if valid else None
    kept = []
    for row in valid:
        if (row['version'], row['scoring']) == signature:
            kept.append(row)
        else:
            row['excluded'] = '与最新可比记录的量表版本或评分口径不同'
    changes = []
    for first, last in zip(kept, kept[1:]):
        delta = Decimal(last['value']) - Decimal(first['value'])
        changes.append({'from': first['date'], 'date': last['date'], 'delta': signed(delta)})
    deltas = [Decimal(step['delta']) for step in changes]
    direction = None
    if len(kept) >= 3:
        direction = ('持平' if not any(deltas) else '总体上升' if all(d >= 0 for d in deltas)
                     else '总体下降' if all(d <= 0 for d in deltas) else '有波动')
    return {'total': len(rows), 'included': len(kept), 'excluded': len(rows) - len(kept),
            'first': kept[0] if kept else None, 'last': kept[-1] if kept else None, 'direction': direction,
            'records': rows[-40:], 'changes': changes[-40:],
            'anchors': {step['date']: [kept[i]['record_id'], kept[i + 1]['record_id']]
                        for i, step in enumerate(changes) if Decimal(step['delta'])},
            'anchor_versions': {step['date']: fingerprint([kept[i], kept[i + 1]])
                                for i, step in enumerate(changes) if Decimal(step['delta'])},
            'conflicts': [{'date': day, 'evidence_refs': [row['record_id'] for row in group if row['record_id']]}
                          for day, group in by_date.items()
                          if group[0]['excluded'] == '同一访视日期存在冲突或待核实记录'],
            'declines': [step['date'] for step in changes if Decimal(step['delta']) < 0],
            'rises': [step['date'] for step in changes if Decimal(step['delta']) > 0]}


def read_patient_form(workspace, session, patient_id, key, read_patient):
    read = lambda: workspace.form(session, patient_id, key, with_sources=True)
    return read_patient('longitudinal:' + key, read) if read_patient is not None else read()


def laboratory_task(workspace, session, patient_id, key, bounds, previous, *, read_patient=None):
    label = LAB_FORMS[key]
    try:
        form = read_patient_form(workspace, session, patient_id, key, read_patient)
    except WorkspaceError as error:
        if error.status in {401, 403, 404, 409}:
            raise
        return {'key': key, 'title': label, 'status': 'failed', 'records': [], 'gaps': ['表单暂无法读取。'],
                'reused': False}
    hits, undated = [], 0
    for row in form['records']:
        stamp = parse_time(row.get('date'))
        if stamp.precision != 'day':
            undated += 1
        elif bounds[0] <= stamp.value <= bounds[1]:
            hits.append({'date': stamp.value, 'record_id': row.get('record_id'),
                         'fields': [[name, str(value)] for name, value in row['fields']
                                    if not PRIVATE_FIELD.search(name) and str(value).strip() not in MISSING]})
    binding = fingerprint([bounds, hits, undated, form['title']])
    if previous.get('binding') == binding:
        return {**previous, 'reused': True}
    identified = {}
    conflicts = set()
    unique = []
    for row in hits:
        ref = row['record_id']
        if ref and ref in identified:
            if row != identified[ref]:
                conflicts.add(ref)
        else:
            if ref:
                identified[ref] = row
            unique.append(row)
    hits = [row for row in unique if row['record_id'] not in conflicts]
    gaps = [f'{undated} 条记录没有可用日期，无法归入本次时间窗。'] if undated else []
    if conflicts:
        gaps.append(f'{len(conflicts)} 条来源记录存在内容冲突，已排除。')
    if len(hits) > 8:
        gaps.append(f'命中 {len(hits)} 条记录，展开可查看最近 8 条。')
    status = 'conflict' if conflicts else 'date_unknown' if undated else 'completed' if hits else 'skipped_no_data'
    records = [{**row, 'fields': [[name[:100], value[:220]] for name, value in row['fields'][:12]]}
               for row in hits[:8]]
    return {'key': key, 'title': form['title'], 'status': status, 'records': records, 'count': len(hits),
            'binding': binding, 'gaps': gaps, 'reused': False, 'conflicts': sorted(conflicts)}


def compare(workspace, session, patient, request, previous, check, *, route='rules', model_calls=0,
            workflow_enabled=True, reuse_results=True, evidence_seconds=EVIDENCE_SECONDS, read_patient=None):
    scale = request['scale']
    title = SCALES[scale]
    forms = {form.key: form for form in workspace.schema.forms}
    if scale not in forms:
        raise WorkspaceError('当前项目没有配置这张量表。', 400)
    keys = [key for key in request['lab_keys'] if key in forms]
    workflow = ComparisonWorkflow(request, keys, previous.get('workflow', {}), route, model_calls) if workflow_enabled else None
    deadline = time.monotonic() + evidence_seconds
    if not reuse_results:
        previous = {}
    check()
    scale_status = 'completed'
    reads = 0
    try:
        if workflow:
            forms_read, reads = read_with_deadline(
                {'scale': lambda: read_patient_form(workspace, session, patient['patientKey'], scale, read_patient)},
                deadline)
            form = forms_read.get('scale')
        else:
            form = read_patient_form(workspace, session, patient['patientKey'], scale, read_patient)
        rows = read_scale(form, getattr(forms[scale], 'comparison', None), request['window']) if form else []
        if form is None:
            scale_status = 'timed_out'
    except WorkspaceError as error:
        if not workflow or error.status in {401, 403, 404, 409}:
            raise
        rows, reads, scale_status = [], 1, 'failed'
    version = fingerprint([scale, request['window'], rows]) if scale_status == 'completed' else None
    reused = (version is not None and previous.get('version') == version
              and 'anchor_versions' in previous.get('result', {}))
    result = previous['result'] if reused else compute(rows)
    if workflow:
        read_status = scale_status if scale_status != 'completed' else (
            'date_unknown' if any(not row['date'] for row in rows) else 'completed' if rows else 'skipped_no_data')
        workflow.finish('scale', read_status, reads=reads, output={'source_version': version})
        if scale_status == 'completed':
            workflow.finish('compare', 'completed' if result['included'] >= 2 else 'partial',
                            action='reused' if reused else 'executed',
                            output={'result_digest': fingerprint(result), 'candidates': fingerprint(result['anchor_versions'])})
    count, first, last = result['included'], result['first'], result['last']
    if scale_status != 'completed':
        message = f'{title} 量表读取' + ('超时。' if scale_status == 'timed_out' else '失败。') + '本轮无法核验量表记录。'
    elif count >= 2:
        delta = Decimal(last['value']) - Decimal(first['value'])
        message = f'{title} 已记录总分：{first["date"]} 为 {first["value"]}，{last["date"]} 为 {last["value"]}，末次减首次为 {signed(delta)} 分。'
        if result['direction']:
            message += f'按逐次变化看，{result["direction"]}。'
    elif count:
        message = f'{title} 仅有一条可比记录：{first["date"]} 总分 {first["value"]}，尚无法比较变化。'
    else:
        message = f'{title} 在该范围内没有可比记录。可展开查看原始记录和排除原因。'
    if scale_status == 'completed':
        message += f'本次纳入 {count} 条，排除 {result["excluded"]} 条。'
    collaboration = []
    lab_cache = {}
    if request['cross']:
        candidates = result['rises' if request['direction'] == 'up' else 'declines']
        if workflow:
            candidates = [day for day in candidates if all(result['anchors'][day]) and '1900' <= day[:4] <= '2099']
        anchor = request['anchor']
        cognition = {'role': 'cognition', 'status': 'completed' if count >= 2 else 'partial', 'node': '量表访视',
                     'question': f'确定 {title} 评分变化对应的访视', 'gaps': [], 'reused': reused}
        collaboration.append(cognition)
        if not anchor and (len(candidates) == 1 or request['choose_latest'] and candidates):
            anchor = candidates[-1]
        if not anchor or anchor not in candidates:
            if candidates:
                options = '、'.join(candidates[-8:])
                message += f'\n请指定要核查哪次评分变化的访视：{options}。'
                cognition['status'] = 'waiting'
                cognition['output'] = '等待确认访视日期'
            else:
                message += '\n当前资料无法确认对应的评分变化访视，同期检验查询等待访视证据。'
                cognition['output'] = '缺少可确定的访视日期'
            if workflow:
                workflow.finish('visit', 'waiting', action='blocked', detail=cognition['output'])
        else:
            request = {**request, 'anchor': anchor}
            day, radius = date.fromisoformat(anchor), timedelta(days=request['radius'])
            bounds = [(day - radius).isoformat(), (day + radius).isoformat()]
            cognition['output'] = '确认访视 ' + anchor
            if workflow:
                handoff = {'date': anchor, 'evidence_refs': result['anchors'][anchor],
                           'evidence_version': result['anchor_versions'][anchor]}
                prior_visit = workflow.previous.get('visit', {}) if reuse_results else {}
                workflow.finish('visit', 'completed', action='reused' if prior_visit.get('output') == handoff else 'executed',
                                output=handoff, detail=cognition['output'])
            message += f'\n同期检验的查询范围为 {bounds[0]} 至 {bounds[1]}。'
            if not keys:
                message += '\n当前项目未配置所请求的检验表单。'
            check()
            def read_lab(key):
                return laboratory_task(workspace, session, patient['patientKey'], key, bounds,
                                       previous.get('lab', {}).get(key, {}), read_patient=read_patient)

            if workflow:
                labs, lab_reads = read_with_deadline({key: lambda key=key: read_lab(key) for key in keys}, deadline)
            else:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    labs = dict(zip(keys, pool.map(read_lab, keys)))
                lab_reads = len(keys)
            for key in keys:
                item = labs.get(key)
                if item is None:
                    item = {'title': LAB_FORMS[key], 'status': 'timed_out', 'records': [],
                            'gaps': ['本轮查询超时。'], 'reused': False}
                if 'binding' in item:
                    lab_cache[key] = item
                count_text = f'找到 {item["count"]} 条' if 'count' in item else '读取未完成'
                message += f'\n{item["title"]}：{count_text}。'
                evidence = [{'title': item['title'], 'text': '；'.join(f'{k} {v}' for k, v in row['fields']),
                             'records': [{'date': row['date'], 'text': f'记录 {row["record_id"]}' if row['record_id'] else ''}]}
                            for row in item['records']]
                collaboration.append({'role': 'laboratory', 'status': item['status'], 'node': item['title'],
                                      'question': '查阅同期检验记录', 'depends_on': '量表访视',
                                      'output': f'{bounds[0]} 至 {bounds[1]} · {count_text}',
                                      'gaps': item['gaps'], 'reused': item['reused'], 'sources': evidence})
                if workflow:
                    workflow.finish(key, item['status'], action='reused' if item['reused'] else 'executed',
                                    reads=int(bool(lab_reads)),
                                    output={'source_version': item.get('binding'), 'status': item['status'],
                                            'evidence_refs': [row['record_id'] for row in item['records'] if row['record_id']]},
                                    detail=f'{bounds[0]} 至 {bounds[1]} · {count_text}')
    state = {'request': request, 'version': version, 'result': result, 'lab': lab_cache}
    incomplete = (scale_status != 'completed' or count < 2 or
                  any(item.get('status') not in {'completed', 'skipped_no_data'} for item in collaboration))
    if workflow:
        conflicts = result['conflicts'] + [
            {'form': key, 'evidence_refs': item['conflicts']} for key, item in lab_cache.items() if item.get('conflicts')]
        if conflicts:
            verify = workflow.tasks.pop('verify')
            review_dependencies = {'compare': ['result_digest']}
            review_dependencies.update({key: ['source_version'] for key, item in lab_cache.items()
                                        if item.get('conflicts')})
            workflow.add('review', '复核冲突记录', 'review', {}, review_dependencies)
            workflow.finish('review', 'conflict', output={'conflicts': conflicts},
                            detail=f'排除 {len(conflicts)} 组冲突或待核实记录。')
            verify['depends_on']['review'] = ['conflicts']
            workflow.tasks['verify'] = verify
        incomplete = any(task['status'] in {'waiting', 'failed', 'timed_out', 'date_unknown', 'conflict', 'partial'}
                         for key, task in workflow.tasks.items() if key != 'verify')
        workflow.finish('verify', 'partial' if incomplete else 'completed', detail='采用本轮已核验的数据和来源')
        state['workflow'] = workflow.record()
    state['result_status'] = 'partial' if incomplete else 'completed'
    return message, collaboration, state


def previous_comparison(history):
    return next((json.loads(row['content']) for row in reversed(history)
                 if row.get('tool_name') == 'neurogra_comparison'), {})
