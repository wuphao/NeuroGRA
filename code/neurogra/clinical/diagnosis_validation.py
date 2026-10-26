"""Structural label checks; no patient diagnosis or invented clinical cutoffs."""
import re


def validate_primary_label(label, status, research_class=None, level=None):
    value = label.strip()
    positive = r'(?:MCI|AD|CN|轻度认知障碍|(?:轻度|中度|重度)?痴呆(?:综合征)?|认知正常|阿尔茨海默病)'
    if status != 'indeterminate' and re.match(r'^' + positive + r'\s*(?:或|/)\s*' + positive, value, re.I):
        raise ValueError('primary_label_requires_one_working_judgment_put_alternatives_separately')
    assertions = re.sub(r'(?:尚不能|尚未|未能|不能|无法|并未|不是|尚非|非|未|不)\s*(?:予以|作出)?(?:确诊|已证实|已排除|100%)', '', value)
    if any(word in assertions for word in ('确诊', '已证实', '已排除', '100%')):
        raise ValueError('overstated_diagnosis_label')
    if status == 'indeterminate' or not research_class:
        return
    names = {'CN': r'^(?:CN\b|认知正常|认知功能(?:基本)?正常)',
             'MCI': r'^(?:MCI\b|轻度认知障碍)',
             'DEMENTIA': r'^(?:(?:轻度|中度|重度|轻中度)?痴呆|痴呆综合征)'}
    for code, pattern in names.items():
        if re.search(pattern, value, re.I) and level == 'syndrome' and code != research_class:
            raise ValueError('primary_label_and_research_class_disagree')
