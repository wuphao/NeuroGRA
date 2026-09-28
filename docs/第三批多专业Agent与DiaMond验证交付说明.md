# 第三批交付：认知、检验与影像 Agent

本批已完成 S10、S11 的代码与集成，S13 已接入影像报告分析及受控工具调用。S12 已交付模型档案、准入、独立进程适配、输出校验及真实环境验证，但真实模型一致性验收被阻断，**M4 不标完成**。这不是训练失败或准确率差的结论，而是当前 raw 入口与 checkpoint 尚不满足可信推理条件。

## 模块及输入输出

| 模块 | 输入 | 输出 | 调用时机 |
|---|---|---|---|
| CognitionAgent (`clinical/specialists.py`) | cognition 观察、原始记录、已提供背景、任务工具权限 | AgentResult：scale_summaries、cognitive_findings、functional_findings、interpretable_scope、claims、uncertainties | 资料盘点发现量表、认知或生活功能资料 |
| LaboratoryAgent (`clinical/specialists.py`) | 检验观察、原值、原单位、方法、样本、参考范围和时间 | AgentResult：test_findings、report_flags、method_constraints、comparable_series、claims | 发现检验或生物标志物记录 |
| ImagingAgent (`clinical/imaging.py`) | 影像报告观察、已登记 ImageAsset、任务背景 | AgentResult：report_findings、model_results、longitudinal_comparison、discrepancies、claims | 发现影像报告或影像资产；只有文本也可分析 |
| `diamond.model_profile` | repo、解释器、checkpoint、超参数、代码、已安装依赖元数据 | DiamondModelProfile：内容哈希、档案指纹、类别映射、验证状态 | 初始化影像工具；不因文件存在就宣称验证通过 |
| `check_diamond_eligibility` | 当前病例内选中资产、配对配置 | EligibilityResult：唯一 MRI/PET 对或具体拒绝原因 | 分类执行之前 |
| `DiamondTool.predict` | 当前任务授权的病例快照、运行与任务截止时间 | DiamondResult：状态、输入哈希、模型指纹、类别/分数或 null、原始输出、错误、缓存命中 | 影像 Agent 有原始资产时；验证未通过只返回 blocked_validation |
| `diamond_input_worker.py` | 单文件 MRI/PET | 三维、有限值、非恒定体积和间距检查结果 | 已验证模型执行前，使用独立影像解释器 |
| `diamond_probe_worker.py` | 外部 repo、指定 checkpoint | 环境、真实加载、预处理/RegBN 检查与验证问题 | `validate-diamond` 命令 |
| `workflow.py` | 各专业最新结果与复查记录 | 主 Agent 复查、综合报告、专业结构、工具执行记录引用 | 子任务完成后依次集成，不把局部缺失写成全病例缺失 |

专业扩展字段为正式 Pydantic schema；每个 SpecialistItem 保留 observation_id、原值、原文、原单位、时间、原始记录引用、context 和 missing_context。未知名称仍按原文保存，内部 name 不替代原表名。

## 专业约束

认知 Agent 不新增自动计分器，不默认做教育校正，不将自制量表改名成 MoCA/MMSE。未提供版本、语言、校正信息或规则未登记时，相关分数推断被撤回，保留原始事实。每个日期独立保存；生活功能叙述单独列出引用，不能直接转换为具体病因确诊。

检验 Agent 不补单位、参考区间或检测方法，不做隐式单位换算。未做保留为未做；报告标记和知识解释分离。目前没有检测平台等价性登记，comparable_series 保守为空，跨平台/样本/单位的趋势不被自动推断。完整原始观察和限制仍可查看。

各专业都复用真实模型、真实知识检索、引用校验和限次修正。模型/检索失败时，专业模块可返回明确标记的原文整理，不伪装为模型分析成功。复查调用实际目标专业的 review 方法，最多两轮，保留版本与未决项。

跨 Agent 重复 claim_id 在报告层消歧；同一事实不作为多份独立证据累计。description 类报告主张使用原文，解释仍需要知识引用。专业原始结果保留为审计资料，不等同于全部已通过最终结论验收。

## 影像工具约束与配置

MRI 必须明确为 T1/T1w/T1WI；PET 必须明确为 FDG；两者检查时间必须精确到天。默认同日，只有显式配置 diamond_max_pair_days 才允许跨日配对。多访视不自动猜配对；影像任务必须收到全部已登记资产，语言模型不能只选择报告而静默遗漏原始影像。

当前直接输入只接受授权数据目录内的单文件 `.nii/.nii.gz/.mha/.nrrd` 三维体积。DICOM 目录拒绝直接分类，避免 raw 脚本默认选择切片最多序列。当前尚未实现显式 SeriesInstanceUID 导出，所以 DICOM 选择/转换是后续解阻工作，不声称已完成。

配置新增：

```yaml
diamond_device: cpu
diamond_timeout_seconds: 120
diamond_max_pair_days: 0
diamond_validation_report: null
```

验证报告是运行配置中的受信任人工验收产物，患者输入不能开启模型。启用要求：模型指纹完全匹配，validation_status=validated，class_num=3，label_map={"0":"CN","1":"MCI","2":"AD"}，且 training_reference_match、preprocessing_verified、regbn_restored、label_map_verified、repeatability_verified 均已验收为 true。当前没有这样的报告，不能仅为跑通而填写 true。

适配器使用参数列表和 shell=False，Windows 隐藏子进程并优先使用 Job Object 清理超时进程树；工具调用计入原运行预算。执行前有独立体积检查；执行后检查输入和权重未变化、CSV 单行、输入对应、三类标签、分数有限且位于 [0,1]、和接近 1、argmax 与类别一致。失败时 prediction=null、scores={}。

缓存键绑定权重/代码/超参数/解释器/依赖元数据、输入内容与路径、device。缓存文件也校验哈希与 CSV；内容或模型改变会失效。阻断与失败不伪装为成功缓存。输出 softmax_uncalibrated，不能称为真实患病概率，也不提供脑区定位。

## S12 真实验证结果

验证日期：2026-09-24。没有修改 DiaMond 外部工程或重新训练。

- Python 3.12.0；PyTorch 2.7.0+cu128；CUDA 可用。
- SimpleITK 2.5.5、MONAI 1.5.2、TorchIO 1.2.1 可导入。
- 指定 `DiaMond_multi_split4_bestval.pt` 实际加载成功，三路模型和 head 的 state_dict 加载成功，class_num=3、modality=multi。
- raw 预处理把三维数组直接传入 TorchIO，实际报错：输入要求 `(channels,x,y,z)` 四维张量。训练路径先加通道维，测试输出为 `[1,128,128,128]`。
- raw 的 RegBN 初始化未传 f_num_channels，实际触发 `TypeError: unsupported operand type(s) for *: 'int' and 'NoneType'`。
- checkpoint 仅有 epoch/model_state_dict/head_state_dict/optimizer_state_dict/loss，无独立 RegBN 状态；训练代码的保存字典也没有独立 RegBN。当前不能证明重现训练验证阶段的输出，不能用新初始化的状态替代验收。
- 训练标签源码包含 CN=0、MCI/FTD=1、Dementia/AD=2。raw 输出将类 1 标成 MCI，因此还需核对实际训练样本标签组成与最终类别语义，不能仅凭输出列名确认疾病标签。

因此 validation_status=blocked_validation；**未交付“真实患者影像推理成功”或“一致性已通过”的结论**。适配器成功路径、超时、缓存及输出校验以协议替身验证；这些测试不替代 S12.5 的参考样本一致性验证。

[环境与模型验证 JSON](../output/clinical/diamond_validation/diamond_188852a813567bb807491eec/environment.json) · [内容指纹档案](../output/clinical/diamond_validation/diamond_188852a813567bb807491eec/model_profile.json)

解阻顺序：修复并对齐 raw 预处理与 RegBN 构造 → 找到/恢复与权重对应的训练 RegBN 状态或建立可证明的等价推理路径 → 核对类别语义与空间约定 → 使用明确身份/访视的验证样本和原训练参考输出验证一致性与重复性 → 生成绑定新指纹的验收报告。修复前两处运行错误本身不足以通过全部模型验收。

## 真实集成演示

病例为合成资料，没有使用真实患者信息调用语言模型。

1. 多专业病例 `tests/fixtures/multispecialty.json`：运行 `7ff8138b51504267a11f6b6382f9a37b`。病史、认知、检验、影像报告四类任务全部进入执行；15 次真实 qwen3.6:35b 模型调用、4 次真实检索、合计 19 条返回命中（不是去重后的证据数）。BM25 正常，图谱失败、向量未配置如实保留。最终 partial + insufficient_data，不宣称所有模型表述都已无误。
2. 双模态准入病例 `tests/fixtures/imaging_gate.json`：运行 `64032f4a91894c0c8eb083ebd4214458`。1 次真实主 Agent 模型规划、1 次真实检索，影像 Agent 收到两份资产并实际调用工具；工具返回 blocked_validation，prediction=null，scores={}，没有执行分类。两个体积是人工生成的全零 NIfTI，仅验证控制流程，不能用于模型效果评价；即便未来解除模型阻断，体积检查也会拒绝恒定体积。
3. 已完成运行的 resume 检查保持调用计数，不重复完成的模型/检索任务。

[多专业报告](病例报告/patient_a79fb0a46c16e398cc1ee78f/7ff8138b51504267a11f6b6382f9a37b/report.md) · [影像阻断报告](病例报告/patient_092ab89b7a78492790496ce6/64032f4a91894c0c8eb083ebd4214458/report.md)

## 复现与测试

```powershell
$env:PYTHONPATH = "$PWD/code"
python -m neurogra.clinical.cli analyze --patient code/neurogra/clinical/tests/fixtures/multispecialty.json
python -m neurogra.clinical.cli validate-diamond
python -m neurogra.clinical.cli inspect-run --run-id <运行ID>
python -m neurogra.clinical.cli resume --run-id <运行ID>
python -m unittest discover -s code/neurogra/clinical/tests -q
python -m unittest discover -s code/neurogra/knowledge/tests -q
```

本地影像准入示例文件位于 `data/clinical/patients/third_batch_synthetic/`，患者/影像/运行数据均不提交 Git。新环境没有这些文件时，影像示例会正确返回路径不可用，而不是生成替代影像。

验证结果：75 项 clinical 测试 + 18 项 knowledge 回归测试，共 93 项通过。新增 20 项覆盖未知量表、缺方法与参考范围、未做、跨日期保留、单模态/示踪剂/时间/多序列拒绝、未验证模型不运行、CSV 非法分数/错输入拒绝、超时清理、缓存与输入/权重指纹变化、仅报告不调用分类、影像任务不得遗漏资产、影像执行器抵达验证阻断。
