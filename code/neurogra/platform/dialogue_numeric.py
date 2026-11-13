"""Conservative numeric checks for patient-scoped dialogue claims.

These checks only establish limited literal consistency.  They do not infer
clinical meaning, derive differences, or convert between measurement units.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation


NUMBER = r'(?<![\d.])(?:[-+−负]\s*)?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?(?![\d.])'
NUMBER_RE = re.compile(NUMBER)
UNIT_RE = re.compile(
    r'(?<![A-Za-zµμ])(?:mmol|µmol|μmol|umol|mol|mg|ng|pg|µg|μg|ug|g|IU|U)/'
    r'(?:dL|mL|L)(?![A-Za-z])|(?<![A-Za-z])(?:mmHg|kPa|kg|cm|mL|dL)(?![A-Za-z])|[%％]',
    re.I)

DATE_PATTERNS = (
    re.compile(r'(?<!\d)(\d{4})\s*(?:年|[-/.])\s*(\d{1,2})\s*(?:月|[-/.])\s*'
               r'(\d{1,2})\s*(?:日|号)?(?!\d)'),
    re.compile(r'(?<!\d)(\d{4})\s*年\s*(\d{1,2})\s*月'),
    re.compile(r'(?<!\d)(\d{4})[-/.](\d{1,2})(?![-/.]\d|\d)'),
    re.compile(r'(?<!\d)(\d{4})\s*年'),
    re.compile(r'(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*(?:日|号)'),
)
AMBIGUOUS_DATE = re.compile(r'(?<!\d)\d{1,2}[-/.]\d{1,2}[-/.]\d{4}(?!\d)')
PATIENT_AGE = re.compile(
    r'(?:患者|病人|病患|其|他|她)(?:今年|现年|年龄|年纪)?\s*(?:为|是|约|大约)?\s*('
    + NUMBER + r')\s*岁|现年\s*(' + NUMBER + r')\s*岁', re.I)


def number_tokens(value):
    """Return signed decimal values; date hyphens are not negative signs."""
    if value is None or isinstance(value, bool):
        return set()
    result = set()
    for match in NUMBER_RE.finditer(str(value)):
        text = re.sub(r'\s+', '', match.group()).replace('−', '-').replace('负', '-')
        try:
            number = Decimal(text)
        except InvalidOperation:
            continue
        if number.is_finite():
            result.add(number)
    return result


def date_anchors(value):
    """Recognize explicit year/month/day without mixing their components."""
    text, result, occupied = str(value or ''), [], []
    for index, pattern in enumerate(DATE_PATTERNS):
        for match in pattern.finditer(text):
            if any(first < match.end() and match.start() < last for first, last in occupied):
                continue
            parts = tuple(int(item) for item in match.groups())
            try:
                if index == len(DATE_PATTERNS) - 1:
                    date(2000, parts[0], parts[1])
                    parts = ('month_day', *parts)
                else:
                    date(parts[0], parts[1] if len(parts) > 1 else 1,
                         parts[2] if len(parts) > 2 else 1)
            except ValueError:
                parts = (-1,)
            result.append(parts)
            occupied.append((match.start(), match.end()))
    for match in AMBIGUOUS_DATE.finditer(text):
        if not any(first < match.end() and match.start() < last for first, last in occupied):
            result.append((-1,))
    return result


def date_supported(anchor, row):
    sources = [row.time.value, row.time.raw]
    if re.search(r'日期|时间|(?:^|[_\s])(?:date|time)$', row.name, re.I):
        sources.append(row.value)
    actual = [item for value in sources for item in date_anchors(value)]
    if anchor and anchor[0] == 'month_day':
        return any(item == anchor or len(item) == 3 and item[1:] == anchor[1:] for item in actual)
    return anchor != (-1,) and any(len(item) >= len(anchor) and item[:len(anchor)] == anchor for item in actual)


def date_only_context(text):
    """Identify a standalone date prefix without treating arbitrary prose as a date."""
    if not date_anchors(text):
        return False
    remaining = text
    for pattern in (*DATE_PATTERNS, AMBIGUOUS_DATE):
        remaining = pattern.sub(' ', remaining)
    remaining = re.sub(r'[\s:：()（）]', '', remaining)
    return re.fullmatch(r'(?:(?:患者|病人|本次|上次|当次|当日|当天|的|在|于|访视|检查|评估|就诊|记录|时))*',
                        remaining) is not None


def claim_segments(text, observations, known_forms=None):
    """Split numeric conjunctions only if both sides name recorded fields."""
    names = {row.name.casefold() for row in observations if len(row.name.strip()) >= 2}
    names.update(alias.casefold() for key, title in (known_forms or {}).items()
                 if key.casefold() in {'moca', 'mmse'} for alias in (key, title)
                 if isinstance(alias, str) and len(alias.strip()) >= 3)
    raw = re.split(r'[，,；;。\n]', text)
    clauses, index = [], 0
    while index < len(raw):
        clause = raw[index]
        if (index + 1 < len(raw) and date_only_context(clause) and
                any(name in raw[index + 1].casefold() for name in names)):
            clause += ' ' + raw[index + 1]
            index += 1
        clauses.append(clause)
        index += 1
    output = []
    for clause in clauses:
        lowered = clause.casefold()
        occupied = []
        for name in names:
            occupied.extend((match.start(), match.end()) for match in re.finditer(re.escape(name), lowered))
        start = 0
        for match in re.finditer(r'(?<=\d)\s*(?:以及|并且|同时|和|与|及|而)\s*', clause):
            if any(first < match.start() < last for first, last in occupied):
                continue
            before, after = lowered[start:match.start()], lowered[match.end():]
            if any(name in before for name in names) and any(name in after for name in names):
                output.append(clause[start:match.start()])
                start = match.end()
        output.append(clause[start:])
    return output


def field_mentions(segment, observations):
    """Prefer an explicitly named longer field over a contained generic name."""
    matches = []
    for name in {row.name for row in observations if len(row.name.strip()) >= 2}:
        for match in re.finditer(re.escape(name), segment, re.I):
            matches.append((match.start(), match.end(), name))
    return [item for item in matches if not any(
        other[0] <= item[0] and item[1] <= other[1] and
        (other[0] < item[0] or item[1] < other[1]) for other in matches)]


def form_aliases(row):
    context = row.context if isinstance(row.context, dict) else {}
    source, form = context.get('rwe_source'), context.get('rwe_form')
    aliases = []
    for value in (source.get('form_key') if isinstance(source, dict) else None,
                  form.get('form_name') if isinstance(form, dict) else None):
        if isinstance(value, str) and len(value.strip()) >= 3:
            aliases.append(value.strip())
    return aliases


def mentions_alias(text, alias):
    # ASCII names must not match inside unrelated English words; Chinese field
    # names may immediately follow a form abbreviation, e.g. "MoCA总分".
    if alias.isascii() and re.fullmatch(r'[A-Za-z0-9_]+', alias):
        pattern = r'(?<![A-Za-z0-9_])' + re.escape(alias) + r'(?![A-Za-z0-9_])'
    else:
        pattern = re.escape(alias)
    return re.search(pattern, text, re.I) is not None


def scale_mentions(text, known_forms):
    """Locate known scale labels when a user-facing claim omits 'total score'."""
    matches = []
    for key, title in (known_forms or {}).items():
        if key.casefold() not in {'moca', 'mmse'}:
            continue
        for alias in {key, title}:
            if not isinstance(alias, str) or len(alias.strip()) < 3:
                continue
            alias = alias.strip()
            pattern = (r'(?<![A-Za-z0-9_])' + re.escape(alias) + r'(?![A-Za-z0-9_])'
                       if alias.isascii() and re.fullmatch(r'[A-Za-z0-9_]+', alias) else re.escape(alias))
            matches.extend((match.start(), match.end(), key) for match in re.finditer(pattern, text, re.I))
    return [item for item in matches if not any(
        other[2] == item[2] and other[0] <= item[0] and item[1] <= other[1] and
        (other[0] < item[0] or item[1] < other[1]) for other in matches)]


def is_scale_score(row, key):
    name = re.sub(r'\s+', '', row.name).casefold()
    return (name in {'总分', '评分', '得分', '总得分', '总评分', 'score', 'total', 'total_score'} or
            name.startswith(key.casefold()) and name.endswith(('总分', '得分', '评分', 'score')))


def mentioned_forms(segment, known_forms):
    return {key for key, title in (known_forms or {}).items() if any(
        isinstance(alias, str) and len(alias.strip()) >= 3 and mentions_alias(segment, alias.strip())
        for alias in (key, title))}


def belongs_to_form(row, key, known_forms):
    context = row.context if isinstance(row.context, dict) else {}
    source, form = context.get('rwe_source'), context.get('rwe_form')
    source_key = source.get('form_key') if isinstance(source, dict) else None
    form_name = form.get('form_name') if isinstance(form, dict) else None
    if source_key is not None:
        return source_key == key
    if form_name is not None:
        return form_name == known_forms[key]
    return any(isinstance(alias, str) and len(alias.strip()) >= 3 and
               mentions_alias(row.name, alias.strip()) for alias in (key, known_forms[key]))


def scoped_field_rows(segment, name, observations, known_forms=None):
    rows = [row for row in observations if row.name.casefold() == name.casefold()]
    mentioned = mentioned_forms(segment, known_forms)
    if mentioned:
        return [row for row in rows if any(belongs_to_form(row, key, known_forms) for key in mentioned)]
    scoped = [row for row in rows if any(mentions_alias(segment, alias) for alias in form_aliases(row))]
    return scoped or rows


def direct_value(segment, end):
    """Recognize a field immediately followed by an explicitly stated value."""
    tail = segment[end:]
    pattern = (r'^\s*(?:(?:检测|测量|检查)?(?:结果|数值|水平|浓度|分数|得分|总分|评分|值))?\s*'
               r'(?:(?:约为|达到|测得|等于|为|是|约|达|[:：=])\s*)?'
               r'(' + NUMBER + r')(?!\s*[年月日号])')
    match = re.match(pattern, tail, re.I)
    return number_tokens(match.group(1)) if match else set()


def measurement_numbers(row):
    """Locate an explicit field value, not every number in a narrative quote."""
    value = row.value
    if value is None or isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)):
        return number_tokens(value)
    if not isinstance(value, str):
        return set()
    text = value.strip()
    number = NUMBER_RE.match(text)
    if number:
        suffix = text[number.end():].strip()
        if not suffix or suffix in {'分', '岁'} or UNIT_RE.fullmatch(suffix):
            return number_tokens(number.group())
    values = set()
    if len(row.name.strip()) >= 2:
        for match in re.finditer(re.escape(row.name), text, re.I):
            values.update(direct_value(text, match.end()))
    return values


def direct_values_supported(text, observations, known_forms=None):
    for segment in claim_segments(text, observations, known_forms):
        dates, units = date_anchors(segment), units_in(segment)

        def supported(row, stated):
            material = ' '.join(str(value) for value in (row.value, row.quote, row.unit or ''))
            return (stated <= measurement_numbers(row) and units <= units_in(material) and
                    all(date_supported(anchor, row) for anchor in dates))

        for _, end, key in scale_mentions(segment, known_forms):
            stated = direct_value(segment, end)
            if stated and not any(belongs_to_form(row, key, known_forms) and is_scale_score(row, key) and
                                  supported(row, stated) for row in observations):
                return False
        for _, end, name in field_mentions(segment, observations):
            stated = direct_value(segment, end)
            rows = scoped_field_rows(segment, name, observations, known_forms)
            if stated and not any(supported(row, stated) for row in rows):
                return False
    return True


def explicit_age_supported(text, observations):
    ages = [row for row in observations if '年龄' in row.name or
            row.name.strip().casefold() in {'age', 'patient_age'}]
    for match in PATIENT_AGE.finditer(text):
        stated = number_tokens(match.group(1) or match.group(2))
        if stated and not any(stated <= measurement_numbers(row) for row in ages):
            return False
    return True


def units_in(value):
    return {item.casefold().replace('μ', 'µ').replace('％', '%') for item in UNIT_RE.findall(str(value))}
