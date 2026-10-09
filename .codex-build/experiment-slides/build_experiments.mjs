import fs from 'node:fs/promises';
import path from 'node:path';
import { Presentation, PresentationFile } from '@oai/artifact-tool';

const OUT = 'D:/Python Project/NeuroGRA/.codex-build/experiment-slides';
const pres = Presentation.create({slideSize:{width:1280,height:720}});
const C={blue:'#02549D',navy:'#071A43',body:'#17243A',muted:'#526982',pale:'#EDF5FB',border:'#B9D9F0',white:'#FEFEFE'};
const FONT='微软雅黑';
let serial=0;
function text(sl,x,y,w,h,value,size=20,bold=false,color=C.body,align='left'){
  const sh=sl.shapes.add({name:`text-${++serial}`,geometry:'textbox',position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});
  sh.text=value;
  sh.text.style={typeface:FONT,fontSize:size,bold,color,alignment:align,verticalAlignment:'middle',autoFit:'shrinkText',wrap:'square',insets:{left:0,right:0,top:0,bottom:0}};
  return sh;
}
function box(sl,x,y,w,h,fill=C.white,border=C.border,radius=10){
  return sl.shapes.add({name:`surface-${++serial}`,geometry:'roundRect',position:{left:x,top:y,width:w,height:h},fill,line:{fill:border,width:1.3},borderRadius:radius});
}
function panel(sl,x,y,w,h,title,num){
  box(sl,x,y,w,h);
  box(sl,x+1,y+1,w-2,50,C.pale,C.pale,8);
  text(sl,x+15,y+7,46,34,num,29,true,C.blue,'center');
  text(sl,x+73,y+7,w-89,34,title,25.5,true,C.navy);
}
function label(sl,x,y,w,s){return text(sl,x,y,w,29,s,20.5,true,C.blue);}
function table(sl,x,y,w,h,values,widths,size=18.3,highlightLast=false){
  const tb=sl.tables.add({rows:values.length,columns:values[0].length,left:x,top:y,width:w,height:h,values,columnWidths:widths});
  tb.borders.assign({style:'solid',fill:'#D5E5F1',width:0.85});
  tb.styleOptions={headerRow:false,bandedRows:false};
  for(let r=0;r<values.length;r++){
    tb.rows[r].height=h/values.length;
    for(let c=0;c<values[0].length;c++){
      const cell=tb.getCell(r,c);
      const head=r===0,hot=highlightLast&&r===values.length-1;
      cell.fill=head?C.pale:hot?'#F0F7FC':C.white;
      cell.text.style={typeface:FONT,fontSize:size,bold:head||hot,color:head||hot?C.navy:C.body,alignment:c===0?'left':'left',verticalAlignment:'middle',autoFit:'shrinkText',insets:{left:9,right:8,top:5,bottom:5}};
    }
  }
  return tb;
}
function connect(sl,a,b,from='bottom',to='top'){
  return sl.shapes.connect(a,b,{kind:from==='right'?'straight':'elbow',fromSide:from,toSide:to,line:{fill:'#7FA9CA',width:2},tail:{type:'triangle',width:'sm',length:'sm'}});
}
function node(sl,x,y,w,h,title,detail,emphasis=false){
  const sh=box(sl,x,y,w,h,emphasis?C.pale:C.white,emphasis?C.blue:C.border,8);
  text(sl,x+10,y+10,w-20,28,title,21,true,emphasis?C.blue:C.navy,'center');
  text(sl,x+10,y+43,w-20,h-49,detail,18,false,C.body,'center');
  return sh;
}
function notes(sl,s){sl.speakerNotes.textFrame.setText('内容来源：用户提供的实验设计文本。全部样本规模均为规划数量，尚未表示采集、标注或实验已完成。\n'+s);}

// 1. 数据与整体框架
{
 const sl=pres.slides.add(); sl.background.fill='#FFFFFF';
 text(sl,27,94,1180,40,'实验数据与总体实验',29,true,C.navy);
 panel(sl,24,150,575,482,'实验数据与标注','01');
 panel(sl,617,150,639,482,'总体实验框架','02');
 text(sl,43,210,534,27,'AD/MCI 辅助诊断，主任务为 CN / MCI / AD 三分类',18.3,false,C.body);
 table(sl,42,249,538,228,[
  ['数据集','来源与计划规模','评价用途'],
  ['患者病例集','ADNI\n200–300 名独立患者','诊断、报告\n鲁棒性'],
  ['医学知识集','公开指南与研究文献\n50–100 份，200–400 条声明','条件化知识\n构建'],
  ['病例—证据\n问题集','ADNI 病例与知识语料\n100–150 个病例问题','检索、补查\n证据评价']
 ],[116,288,134],17.8);
 label(sl,44,493,530,'报告金标准：计划选取 60–100 个病例子集');
 text(sl,44,535,532,67,'按患者 ID 划分，同一患者访视不跨集合\n仅使用当前及既往记录，固定知识语料版本',19,false,C.body);
 const input=node(sl,651,211,570,64,'患者病例数据 + 医学知识文档','',false);
 const e1=node(sl,651,306,270,86,'实验一：知识构建','条件及来源是否完整');
 const e2=node(sl,951,306,270,86,'实验二：检索与补查','证据是否适用、缺口是否补齐');
 const e3=node(sl,651,423,570,86,'实验三：可解释诊断报告质量','声明支持、必要证据覆盖、临床错误',true);
 const e4=node(sl,651,540,270,68,'实验四：诊断性能','患者级临床状态判断');
 const e5=node(sl,951,540,270,68,'实验五：消融与鲁棒性','模块贡献与输入扰动');
 connect(sl,input,e1); connect(sl,input,e2); connect(sl,e1,e3);connect(sl,e2,e3);connect(sl,e3,e4);connect(sl,e3,e5);
 text(sl,29,648,920,24,'计划规模，尚未完成采集或标注。ADNI 访视诊断标签用于临床状态评价。',16,false,C.muted);
 notes(sl,'ADNI 的临床标签不能等同于独立病理确诊。病例证据集需标注知识的适用性与必要性。报告金标准来自病例子集。');
}

// 2. 两个算法分别评价
{
 const sl=pres.slides.add();sl.background.fill='#FFFFFF';
 panel(sl,24,98,607,534,'实验一：条件化知识构建','01');
 panel(sl,649,98,607,534,'实验二：患者证据检索与补查','02');
 text(sl,44,160,565,46,'验证医学声明的条件归属、逻辑关系及来源保留',20,false,C.body);
 label(sl,44,213,560,'对照方法');
 table(sl,43,250,569,184,[
  ['方法','核心变化'],
  ['B1 三元组抽取','抽取主体、关系、客体'],
  ['B2 条件抽取','加入人群、时间及排除条件'],
  ['B3 文档解析 + 条件抽取','联合处理正文、表格与脚注'],
  ['完整算法一','跨位置条件归属、来源及逻辑关联']
 ],[241,328],18.0,true);
 label(sl,44,445,565,'主要指标');
 text(sl,44,481,565,30,'条件归属 F1、完整声明准确率',22,true,C.navy);
 text(sl,44,520,565,26,'辅助：声明 / 条件抽取 F1、来源定位准确率',18.3,false,C.body);
 text(sl,44,562,565,52,'分场景：同句同段、跨段标题、表格表注、\n角标脚注与跨页条件',19,false,C.body);

 text(sl,669,160,565,46,'验证患者状态匹配、证据缺口识别与定向补查',20,false,C.body);
 label(sl,669,213,560,'对照方法');
 table(sl,668,250,569,184,[
  ['方法','检索策略'],
  ['Dense / Hybrid RAG','向量检索 / 关键词与向量混合检索'],
  ['KG-RAG','普通实体关系图谱检索'],
  ['Agentic RAG','LLM 规划与迭代检索'],
  ['完整算法二','状态匹配、缺口识别、定向补查']
 ],[203,366],18.0,true);
 label(sl,669,445,565,'主要指标');
 text(sl,669,481,565,57,'知识适用性 Macro-F1、必要证据覆盖率\n证据缺口识别 F1',21,true,C.navy);
 text(sl,669,545,565,27,'辅助：Recall@10、nDCG@10、补查成功率及成本',17.8,false,C.body);
 text(sl,669,580,565,40,'知识缺口：补查原文\n患者数据缺口：明确提示缺失',18.3,false,C.body);
 text(sl,29,648,930,24,'公平对照：同一知识库与检索预算。固定条件化图谱，额外比较算法二的独立效果。',16,false,C.muted);
 notes(sl,'B2 与 B3 使用充分设计的提示词。实验二的知识适用性分为适用、不适用、条件未知。知识缺口与患者数据缺口分别评价，文献检索不能补出患者尚未开展的检查结果。Dense RAG 与 Hybrid RAG 为两个独立对照组，表中合并一行仅为排版。固定条件化图谱时，比较普通检索、Agentic RAG 与算法二。');
}

// 3. 核心报告实验
{
 const sl=pres.slides.add();sl.background.fill='#FFFFFF';
 text(sl,27,95,1180,40,'实验三：可解释辅助诊断报告质量评价',29,true,C.navy);
 text(sl,29,150,1218,30,'统一报告章节：综合判断、患者证据、医学依据、限制与冲突、辅助建议',21,false,C.body);
 const a=node(sl,29,205,250,91,'自然语言报告','不存在的信息明确标为缺失');
 const b=node(sl,313,205,279,91,'原子医学声明拆解','诊断结论、事实、知识、缺失');
 const c=node(sl,626,205,282,91,'逐条证据核验','患者记录、知识原文、专家标准');
 const d=node(sl,942,205,310,91,'指标统计与错误分级','判断、证据定位及错误原因',true);
 connect(sl,a,b,'right','left');connect(sl,b,c,'right','left');connect(sl,c,d,'right','left');
 text(sl,31,318,1214,29,'临床错误：患者事实、医学知识、条件适用、关键证据遗漏、过度诊断、来源错误',19.5,false,C.body);
 panel(sl,24,367,730,265,'报告质量主要终点','03');
 table(sl,42,428,694,139,[
  ['主要终点','计算依据','评价目标'],
  ['必要证据覆盖率','正确覆盖 / 必要证据','解释完整性'],
  ['医学声明支持率','可信来源支持 / 可核验声明','证据忠实性'],
  ['严重临床错误率','含严重错误的报告 / 全部报告','临床安全性']
 ],[210,332,152],18.0);
 text(sl,44,585,687,32,'辅助：患者事实准确率、知识适用性 Macro-F1、无依据陈述率',18.2,false,C.body);
 panel(sl,772,367,484,265,'专家标注与评价验证','04');
 text(sl,791,429,445,29,'金标准：60–100 个病例子集',21,true,C.navy);
 text(sl,791,466,445,51,'参考标准含关键事实、必要证据、\n适用条件、资料缺失与错误清单',18.7,false,C.body);
 text(sl,791,527,445,50,'规则开发：15–20 例开发病例\n独立复核：60–100 份匿名随机报告',18.7,false,C.body);
 text(sl,791,587,445,30,'轻微 / 中等 / 严重分级，验证自动核验',18.5,false,C.body);
 text(sl,29,648,912,24,'各指标分别报告。自动严重错误识别不足时，以专家人工评价作为主要结果。',16,false,C.muted);
 notes(sl,'不设置唯一标准报告，也不将指标强行加权为总分。专家参考答案还包含可接受诊断结论及证据不足的判断。条件允许时双专家独立标注并仲裁。验证自动评分的 Precision、Recall、F1 及专家一致性，达到预设标准后再用于剩余报告。专家不知报告所属方法。');
}

// 4. 系统性能、消融与鲁棒性
{
 const sl=pres.slides.add();sl.background.fill='#FFFFFF';
 panel(sl,24,98,607,534,'实验四：整体系统诊断性能','04');
 panel(sl,649,98,607,534,'实验五：消融与鲁棒性','05');
 text(sl,44,160,565,42,'评价知识构建与检索优化对患者级判断的贡献',20,false,C.body);
 label(sl,44,210,565,'系统对照');
 table(sl,43,239,569,207,[
  ['方法','验证作用'],
  ['单 LLM','基础诊断分析'],
  ['单 LLM + 文本 RAG','外部知识增强'],
  ['多智能体 + 文本 RAG','多智能体协作'],
  ['多智能体 + 普通 KG-RAG','普通图谱检索'],
  ['完整系统','条件化图谱 +\n患者状态引导检索']
 ],[310,259],17.8,true);
 label(sl,44,488,565,'诊断指标与结果展示');
 text(sl,44,526,565,48,'Accuracy、Macro-F1、Balanced Accuracy\n混淆矩阵，另列报告质量结果',20.5,true,C.navy);
 text(sl,44,580,565,38,'证据不足：报告拒绝判断率，\n并统计已判断病例的准确率',18.8,false,C.body);

 label(sl,669,160,565,'两个算法的模块消融');
 table(sl,668,199,569,175,[
  ['实验组','条件化知识构建','患者状态引导检索'],
  ['A0 基础系统','无','无'],
  ['A1 仅算法一','有','无'],
  ['A2 仅算法二','无','有'],
  ['A3 完整系统','有','有']
 ],[190,178,201],17.6,true);
 text(sl,669,389,565,48,'共同评价：诊断 Macro-F1、证据覆盖率、\n医学声明支持率、严重临床错误率',19,false,C.body);
 label(sl,669,444,565,'鲁棒性场景');
 table(sl,668,479,569,113,[
  ['输入扰动','评价重点'],
  ['模态缺失','缺失提示、诊断表现下降'],
  ['证据冲突','冲突识别、无依据断言'],
  ['知识条件变化','成对病例适用性、引用正确性']
 ],[158,411],17.6);
 text(sl,669,611,565,21,'固定协作框架；解释实验固定候选诊断',16.4,false,C.muted);
 text(sl,29,648,935,24,'同一病例、底层 LLM、知识语料、报告结构及可比预算。缺失模态区分自然缺失与人为遮蔽。',15.5,false,C.muted);
 notes(sl,'主任务为 CN/MCI/AD 三类临床状态判断。只有获得有意义的类别概率时才增加 AUROC、Brier Score 或校准误差，不把自然语言概率直接当作校准概率。四个消融组保留多智能体分析框架。固定候选诊断的解释实验排除诊断类别差异的干扰。鲁棒性中的条件变化采用满足与不满足条件的成对病例。所有内容是实验设计，不呈现虚构结果。');
}
await (await PresentationFile.exportPptx(pres)).save(path.join(OUT,'body-draft.pptx'));
await fs.writeFile(path.join(OUT,'authored-inspection.ndjson'),(await pres.inspect({kind:'slide,textbox,shape,table',maxChars:100000})).ndjson);
console.log('Created four editable experiment slides');
