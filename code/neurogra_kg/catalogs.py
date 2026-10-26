"""Field and terminology catalogs contain no invented clinical thresholds."""

FIELDS = {
    "education_years": {"type": "number", "unit": "year", "description": "明确记录的受教育年限"},
    "function.independent_daily_living": {"type": "boolean", "description": "明确记录的日常生活独立性"},
    "symptom.visual_hallucinations": {"type": "boolean", "description": "明确记录的视幻觉"},
    "symptom.cognitive_fluctuation": {"type": "boolean", "description": "明确记录的认知波动"},
    "symptom.parkinsonism": {"type": "boolean", "description": "明确记录的帕金森综合征"},
    "symptom.rem_sleep_behavior_disorder": {"type": "boolean", "description": "明确记录的 REM 睡眠行为障碍"},
    "symptom.behavioral_disinhibition": {"type": "boolean", "description": "明确记录的行为脱抑制"},
    "age": {"type": "number", "unit": "year", "description": "患者年龄"},
    "sex": {"type": "string", "description": "记录中的性别"},
    "mmse": {"type": "number", "unit": "score", "description": "MMSE 量表分数"},
    "moca": {"type": "number", "unit": "score", "description": "MoCA 量表分数"},
    "cdr": {"type": "number", "unit": "score", "description": "CDR 分数"},
    "cdr_sb": {"type": "number", "unit": "score", "description": "CDR分项总和，不能等同CDR总体评分"},
    "faq": {"type": "number", "unit": "score", "description": "FAQ总分，不能反推各功能项目独立性"},
    "adas_cog_11": {"type": "number", "unit": "score", "description": "ADAS-Cog 11项总分"},
    "adas_cog_13": {"type": "number", "unit": "score", "description": "ADAS-Cog 13项总分"},
    "symptom.memory_impairment": {"type": "boolean", "description": "明确记载的记忆损害"},
    "imaging.hippocampal_atrophy": {"type": "boolean", "description": "影像报告记载海马萎缩"},
    "biomarker.abeta": {"type": "number", "description": "带方法和单位的 Aβ 结果"},
    "biomarker.tau": {"type": "number", "description": "带方法和单位的 tau 结果"},
    "diagnosis": {"type": "string", "description": "已记录的诊断标签，不可用于反推事实"},
    "population": {"type": "string", "description": "记录明确说明的适用人群归属"},
    "indicator_x": {"type": "number", "description": "纯合成样例指标 X"},
    "exclusion_e": {"type": "boolean", "true_means": "present", "description": "纯合成样例排除因素 E 是否存在；true=存在，false=明确不存在"},
}

PURPOSES = ["diagnostic_assessment", "cognitive_assessment", "imaging_interpretation",
            "biomarker_interpretation", "differential_diagnosis", "follow_up"]
RELATIONS = ["supports", "weakens", "associated_with", "distinguishes", "limits"]
ENTITY_TYPES = ["disease", "syndrome", "phenotype", "examination", "biomarker", "other"]
OPERATORS = ["eq", "ne", "gt", "gte", "lt", "lte", "in", "exists"]
TERMINOLOGY = [
    {"name": "阿尔茨海默病", "label_zh": "阿尔茨海默病", "label_en": "Alzheimer's disease", "entity_type": "disease", "aliases": ["AD", "Alzheimer's disease", "Alzheimer disease"]},
    {"name": "轻度认知障碍", "label_zh": "轻度认知障碍", "label_en": "mild cognitive impairment", "entity_type": "syndrome", "aliases": ["MCI", "mild cognitive impairment"]},
    {"name": "磁共振成像", "label_zh": "磁共振成像", "label_en": "magnetic resonance imaging", "entity_type": "examination", "aliases": ["MRI", "magnetic resonance imaging"]},
    {"name": "路易体痴呆", "label_zh": "路易体痴呆", "label_en": "dementia with Lewy bodies", "entity_type": "disease", "aliases": ["DLB", "dementia with Lewy bodies"]},
    {"name": "行为变异型额颞叶痴呆", "label_zh": "行为变异型额颞叶痴呆", "label_en": "behavioral variant frontotemporal dementia", "entity_type": "disease", "aliases": ["bvFTD", "behavioural variant frontotemporal dementia", "behavioral variant frontotemporal dementia"]},
    {"name": "痴呆综合征", "label_zh": "痴呆综合征", "label_en": "dementia syndrome", "entity_type": "syndrome", "aliases": ["dementia syndrome"]},
    {"name": "简易精神状态检查", "label_zh": "简易精神状态检查", "label_en": "Mini-Mental State Examination", "entity_type": "examination", "aliases": ["MMSE", "Mini-Mental State Examination"]},
    {"name": "蒙特利尔认知评估", "label_zh": "蒙特利尔认知评估", "label_en": "Montreal Cognitive Assessment", "entity_type": "examination", "aliases": ["MoCA", "Montreal Cognitive Assessment"]},
]

# Explicit equivalences only: no inferred threshold, population membership or platform.
FIELD_ALIASES = {
    "age": ["年龄", "Age"], "sex": ["性别", "Sex"],
    "mmse": ["MMSE", "简易精神状态检查"], "moca": ["MoCA", "蒙特利尔认知评估"],
    "cdr": ["CDR", "临床痴呆评定"], "education_years": ["受教育年限", "教育年限", "years of education"],
    "function.independent_daily_living": ["日常生活独立性", "independent daily living"],
    "symptom.memory_impairment": ["记忆损害", "memory impairment"],
    "imaging.hippocampal_atrophy": ["海马萎缩", "hippocampal atrophy"],
    "symptom.visual_hallucinations": ["视幻觉", "visual hallucinations"],
    "symptom.cognitive_fluctuation": ["认知波动", "cognitive fluctuation"],
    "symptom.parkinsonism": ["帕金森综合征", "parkinsonism"],
    "symptom.rem_sleep_behavior_disorder": ["REM睡眠行为障碍", "REM sleep behavior disorder", "REM sleep behaviour disorder"],
    "symptom.behavioral_disinhibition": ["行为脱抑制", "behavioral disinhibition", "behavioural disinhibition"],
}
METHOD_ALIASES = {
    "MRI": ["MRI", "磁共振成像", "magnetic resonance imaging"],
    "MMSE": ["MMSE", "简易精神状态检查", "Mini-Mental State Examination"],
    "MoCA": ["MoCA", "蒙特利尔认知评估", "Montreal Cognitive Assessment"],
}
POPULATION_ALIASES = {"adults": ["adults", "成人"]}
VALUE_ALIASES = {"sex": {"male": ["male", "男", "男性"], "female": ["female", "女", "女性"]},
                 "population": POPULATION_ALIASES}
PURPOSE_LABELS = dict(zip(PURPOSES, ["诊断评估", "认知评估", "影像解释", "生物标志物解释", "鉴别诊断", "随访"]))
RELATION_LABELS = dict(zip(RELATIONS, ["支持", "削弱", "相关", "区分", "限制"]))
