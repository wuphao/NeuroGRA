"""Imaging reports remain independent of gated classifier output."""
from .specialists import SourceSpecialist, items
from .history import SYSTEM
from .schemas import AgentResult, RetrievalRequest, Issue
from .llm import ModelFailure
from .storage import BudgetExceeded
from .utils import identity


class ImagingAgent(SourceSpecialist):
    role = 'imaging'
    system = SYSTEM.replace('病史', '影像报告').replace('level写history', 'level写imaging') + '''
只能描述报告原文，不根据检索生成患者影像所见。分类工具只输出类别分数，不提供脑区定位，不能当作真实患病概率。
区分检查时间、报告所见和工具分类；没有原始影像或工具执行记录时，不声称看过图像或运行过模型。
'''

    def enrich(self, result, snapshot):
        rows = items(snapshot, 'imaging')
        result.report_findings = rows
        result.longitudinal_comparison = ['按原始日期分别保留；未提供可比序列或配准信息时不推断纵向变化。']
        return self.restrict(result, rows)

    async def analyze(self, task, context):
        if context.snapshot.observations:
            result = await super().analyze(task, context)
        else:
            result = AgentResult(result_id=identity('result', task.task_id), task_id=task.task_id,
                case_version=task.case_version, status='partial', used_refs=list(task.input_refs))
            try:
                await context.tools.search_knowledge(RetrievalRequest(request_id=identity('imaging_search', task.task_id),
                    question='MRI T1 FDG PET 阿尔茨海默病影像评估适用条件', top_k=3))
            except (ValueError, ModelFailure, BudgetExceeded, TimeoutError) as exc:
                result.issues.append(Issue(code='imaging_retrieval_unavailable', stage='imaging', message=type(exc).__name__))
        if context.snapshot.images:
            prediction = await context.tools.diamond_predict()
            if prediction.status == 'completed':
                result.model_results = [prediction.result_id]
                result.status = 'completed' if not result.issues else 'partial'
                result.uncertainties.append('DiaMond类别分数未经临床概率校准，不能从分类输出推断脑区定位。')
            else:
                result.status = 'partial'
                result.uncertainties.extend([f'DiaMond：{prediction.status}'] + prediction.warnings)
        else:
            result.uncertainties.append('仅有报告文本，未执行DiaMond模型分类。')
        return result
