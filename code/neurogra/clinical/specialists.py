"""Specialist policies and source-preserving domain output; no implicit scoring/conversion."""
from .history import HistoryAgent, SYSTEM
from .schemas import SpecialistItem, Claim, AgentResult, Issue
from .llm import ModelFailure
from .storage import BudgetExceeded
from .utils import identity


def items(snapshot, role, required=()):
    output = []
    for o in snapshot.observations:
        if role not in o.domains:
            continue
        context = dict(o.context)
        for r in snapshot.records:
            if r.record_id in o.source_refs:
                context = {**r.context, **context}
        missing = [key for key in required if not context.get(key)]
        if role == 'laboratory' and not o.unit and not context.get('单位'):
            missing.append('单位')
        output.append(SpecialistItem(observation_id=o.observation_id, name=o.name, value=o.value,
            quote=o.quote, unit=o.unit, time=o.time, status=o.status, source_refs=o.source_refs,
            context=context, missing_context=missing))
    return output


class SourceSpecialist(HistoryAgent):
    def enrich(self, result, snapshot):
        return result

    def constrain(self, result, snapshot):
        # Clinical interpretation is retained only when explicit source metadata permits it.
        # Unknown instruments/methods get source facts, never learned default thresholds.
        result = self.enrich(result, snapshot)
        return result

    async def analyze(self, task, context):
        try:
            result = await super().analyze(task, context)
        except (ModelFailure, BudgetExceeded, TimeoutError, ValueError) as exc:
            result = AgentResult(result_id=identity('result', task.task_id), task_id=task.task_id,
                case_version=task.case_version, status='partial', used_refs=[o.observation_id for o in context.snapshot.observations],
                issues=[Issue(code='specialist_facts_fallback', stage=self.role, message=type(exc).__name__)],
                uncertainties=['模型或检索未完成，专业结构仅整理原始资料，不代表模型分析已成功。'])
        return self.constrain(result, context.snapshot)

    async def review(self, request, previous, snapshot, evidence, gateway):
        response = await super().review(request, previous, snapshot, evidence, gateway)
        if response.revised_result:
            response.revised_result = self.constrain(response.revised_result, snapshot)
        return response

    def restrict(self, result, rows):
        restricted = {r.observation_id for r in rows if r.missing_context or r.status in {'not_recorded', 'not_performed'}}
        removed = [c for c in result.claims if c.kind != 'description' and restricted.intersection(c.observation_ids)]
        result.claims = [c for c in result.claims if c not in removed]
        if removed:
            result.uncertainties.append('缺少解释条件的专业推断已撤回；保留原始观察，不套用默认阈值。')
        # All specialist facts survive even when LLM returns no valid claims.
        covered = {ref for c in result.claims for ref in c.observation_ids}
        for row in rows:
            if row.observation_id not in covered:
                result.claims.append(Claim(claim_id=identity('fact', [self.role, row.observation_id]),
                    text=row.quote, kind='description', level=self.role,
                    observation_ids=[row.observation_id], strength='descriptive'))
        if restricted:
            result.uncertainties.append('部分项目缺少解释条件，详见专业结构字段；未执行自动校正、计分或单位换算。')
        return result


class CognitionAgent(SourceSpecialist):
    role = 'cognition'
    system = SYSTEM.replace('病史', '认知与生活功能').replace('level写history', 'level写cognition') + '''
未知量表不命名为MoCA/MMSE，不补版本或教育校正；保留每次日期。
仅功能描述也要分析但不能替代量表；不把认知状态直接转为具体病因确诊。
年龄、教育和语言未明确提供时标记缺失，禁止默认加分、默认阈值、自动计分。
'''

    def enrich(self, result, snapshot):
        rows = items(snapshot, self.role, ('名称', '版本', '教育校正', '语言'))
        # Names supplied under equivalent external Chinese keys retain their original provenance.
        for row in rows:
            if any(row.context.get(k) for k in ('量表名称', '表格名称')):
                row.missing_context = [k for k in row.missing_context if k != '名称']
            name = str(row.context.get('名称') or row.context.get('量表名称') or row.context.get('表格名称') or '')
            if name.upper() not in {'MOCA', 'MMSE'}:
                row.missing_context.append('量表解释规则未登记')
        result.scale_summaries = [r for r in rows if any(r.context.get(k) for k in ('名称', '量表名称', '表格名称'))
                                  or any(w in r.quote for w in ('量表', '总分', '评分'))]
        result.cognitive_findings = [r.observation_id for r in rows if not any(w in r.quote for w in ('穿衣', '购物', '吃饭', '生活', '理财'))]
        result.functional_findings = [r.observation_id for r in rows if any(w in r.quote for w in ('穿衣', '购物', '吃饭', '生活', '理财'))]
        result.interpretable_scope = ['原文描述与明确提供的量表记录；未启用自动计分及教育校正；无阈值条件时不判定分数异常']
        return self.restrict(result, rows)


class LaboratoryAgent(SourceSpecialist):
    role = 'laboratory'
    system = SYSTEM.replace('病史', '检验与生物标志物').replace('level写history', 'level写laboratory') + '''
保留数值、原单位、参考范围、方法、样本和日期。禁止自动单位换算或补参考值。
仅将原报告明确标注的异常记录为报告异常；知识解释必须区分。
未做不等于阴性。跨方法、样本或单位禁止直接作病程趋势比较。
'''

    def enrich(self, result, snapshot):
        rows = items(snapshot, self.role, ('参考范围', '方法', '样本'))
        result.test_findings = rows
        result.report_flags = [r for r in rows if ('↑' in r.quote or '↓' in r.quote or
            (any(word in r.quote for word in ('标记异常', '标注异常')) and not any(word in r.quote for word in ('未标', '无异常', '没有标'))))
            and r.status not in {'not_recorded', 'not_performed'}]
        result.method_constraints = [f'{r.observation_id}：缺少' + '、'.join(r.missing_context) for r in rows if r.missing_context]
        result.comparable_series = []  # No assay identity registry: never infer equivalence from a name.
        result.method_constraints.append('尚无检测平台等价性登记，不自动输出跨日期趋势或单位换算。')
        return self.restrict(result, rows)
