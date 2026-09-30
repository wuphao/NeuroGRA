/* Same-origin adapter. The standalone HTML retains its offline behavior. */
'use strict';
let liveJob = null, liveGeneration = 0, liveToken = '', liveExecution = null;
const offlineRenderPanel = renderPanel;
const offlineRenderShell = renderShell;
const phaseNames = {rwe_export:'读取 RWE 患者资料',received:'已创建任务',profiling:'整理患者观察',planning:'智能体任务规划',prepared:'准备完成',prepared_with_limitations:'准备完成（存在限制）',analyzing:'专项分析与检索',completed:'已完成',partial:'部分完成',failed:'失败',interrupted:'已中断'};
const failureNames={patient_not_found:'未找到该患者编号，请核对 RWE patient_number。',patient_has_no_records:'该患者没有可读取的记录。',rwe_source_unavailable:'RWE 来源读取失败，请检查后端与登录状态。',rwe_token_missing:'缺少 RWE 登录凭据，请在服务端配置。',rwe_authentication_or_permission_failed:'RWE 登录已失效或当前账号无访问权限。',rwe_database_unavailable:'无法连接 RWE 数据库。',rwe_api_unavailable:'无法连接 RWE 后端。',rwe_patient_binding_failed:'来源数据与患者不匹配，分析已停止。'};
const failureText=value=>failureNames[value]||value;
async function api(path, options={}) {
  const response = await fetch(path, {cache:'no-store', ...options,
    headers:{'Content-Type':'application/json','X-Workbench-Token':liveToken,...options.headers}});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}
function clearPatient() {
  replay++; report=null; provenance=null; liveExecution=null; mode='empty';
  activeTab='report'; offlineRenderShell();
  $('#patient-name').textContent=$('#patient-input').value?'患者 · '+$('#patient-input').value:'等待选择患者';
  $('#patient-tag').textContent='待分析';
  $('#agents').innerHTML='<p class="tiny muted">提交后由模型根据实际资料规划任务。</p>';
  $('#retrieval-badge').textContent='尚未检索';
}
function paintExecution(execution) {
  if (!execution) return;
  const calls=list(execution.calls);
  const count=kind=>calls.filter(c=>c.kind===kind).reduce((a,c)=>a+c.n,0);
  $('#agent-footer').textContent=`${count('llm')} 次模型调用 · ${count('retrieval')} 次检索调用`;
  $('#retrieval-badge').textContent=`${count('retrieval')} 次检索`;
  $('#retrieval').innerHTML='<p class="tiny muted">调用次数来自运行日志；检索片段及适用条件见知识来源。</p>';
  if(report) {
    $('#agents').innerHTML=list(report.agent_results).map(a=>{
      const role=list(provenance?.tasks?.tasks).find(t=>t.task_id===a.task_id)?.agent_type;
      const name=({history:'病史智能体',cognition:'认知智能体',laboratory:'实验室智能体',imaging:'影像智能体'})[role]||role||'专项智能体';
      return `<div class="agent"><span class="agent-icon">${icon('brain')}</span><div style="min-width:0"><div class="name">${esc(name)}</div><div class="sub">${esc(phaseNames[a.status]||a.status)}</div></div></div>`;
    }).join('');
  }
}
renderShell = function() { offlineRenderShell(); paintExecution(liveExecution); };
renderPanel = function() {
  if(activeTab==='history') { renderHistory(); return; }
  if(liveJob && !report && activeTab==='report') { paintJob(liveJob); return; }
  if(activeTab==='process' && liveExecution) {
    $('#panel').innerHTML=`<h2>真实运行记录</h2><p class="tiny muted key">${esc(liveExecution.run_id)}</p><div class="timeline" style="margin-top:24px">${list(liveExecution.events).map(e=>`<div class="event"><h3>${esc(e.type)}</h3><p>${esc(asText(e.payload))}</p></div>`).join('') || '<p>尚无事件。</p>'}</div>`;
    return;
  }
  offlineRenderPanel();
  if(mode==='empty') $('#panel').innerHTML=empty('输入患者编号，开始分析','系统将读取 RWE 资料，执行智能体分析与知识检索，并生成带有来源依据的报告。',false);
};
function paintJob(job) {
  const running=job.status==='running';
  const phase=phaseNames[job.phase]||job.phase||job.status;
  $('#patient-name').textContent='患者 · '+job.patient_number;
  $('#patient-tag').textContent='系统任务';
  $('#patient-meta').textContent=job.run_id?'运行编号 · '+job.run_id:'正在接入 RWE';
  $('#run-status').textContent=running?phase:(phaseNames[job.status]||job.status);
  $('#run-status').className='tag '+(job.status==='completed'?'green':'amber');
  const progress={rwe_export:1,received:1,profiling:1,planning:2,prepared:2,prepared_with_limitations:2,analyzing:3,completed:4,partial:4}[job.phase]||0;
  drawStages(job.status==='failed'||job.status==='interrupted'?0:progress);
  paintExecution(job);
  if(activeTab==='report') $('#panel').innerHTML=`<div class="report-title"><div><div class="eyebrow">LIVE ANALYSIS</div><h2>${running?'正在生成可追溯报告':phaseNames[job.status]||job.status}</h2></div></div><div class="notice">${icon(running?'clock':'info')}<div><h3>${esc(running?phase:failureText(job.error)||'任务已结束')}</h3><p>${running?'系统正在执行真实任务。页面每 3 秒读取一次状态，可以刷新后继续查看。':'可从分析记录查看详情，或重新提交患者编号。'}</p></div></div><p class="muted">${list(job.calls).map(c=>esc(c.kind)+' / '+esc(c.status)+'：'+c.n).join(' · ')||(running?'等待来源数据，不展示未经分析的结论。':'未生成报告。')}</p>`;
}
async function openReport(runId, generation) {
  const data=await api('/api/reports/'+encodeURIComponent(runId));
  if(generation!==liveGeneration)return;
  if(!validateReport(data.report)||data.provenance.run_id!==data.report.run_id||data.provenance.patient_id!==data.report.patient_id)throw Error('报告与依据不匹配。');
  report=data.report;provenance=data.provenance;mode='import';liveJob=null;
  liveExecution=data.execution;$('#patient-input').value=report.patient_id;activeTab='report';renderShell();
  if(report.status==='completed'||report.status==='partial')drawStages(4);
}
async function watchJob(id, generation) {
  try {
    const job=await api('/api/jobs/'+encodeURIComponent(id));
    if(generation!==liveGeneration)return;
    liveJob=job;liveExecution={run_id:job.run_id,status:job.phase,calls:job.calls,events:job.events};
    paintJob(job);
    if(job.status==='running')setTimeout(()=>watchJob(id,generation),3000);
    else if(job.run_id && ['completed','partial'].includes(job.status)) await openReport(job.run_id,generation);
  } catch(e) {
    if(generation!==liveGeneration)return;
    toast(e.message+'；3 秒后重试。');
    setTimeout(()=>watchJob(id,generation),3000);
  }
}
async function renderHistory() {
  $('#panel').innerHTML='<h2>分析记录</h2><p class="muted">正在读取记录…</p>';
  try {
    const data=await api('/api/jobs');
    if(activeTab!=='history')return;
    $('#panel').innerHTML='<h2 style="margin-bottom:20px">分析记录</h2>'+data.jobs.map(j=>`<div class="history-item" style="margin-bottom:12px"><div><h3>${esc(j.patient_number)}</h3><p class="tiny muted">${esc(phaseNames[j.status]||j.status)}${j.created?' · '+new Date(j.created*1000).toLocaleString():''}</p><p class="tiny muted key">${esc(j.error||j.run_id||'正在读取 RWE')}</p></div><button class="btn small" data-live-job="${esc(j.job_id||'')}" data-live-run="${esc(j.run_id||'')}">查看</button></div>`).join('') || '<p>暂无分析记录。</p>';
  }catch(e){if(activeTab==='history')$('#panel').innerHTML=empty('无法读取历史记录',esc(e.message),false);}
}
// Capture prevents the standalone demo submit handler from executing.
$('#query-form').addEventListener('submit', async event=>{
  event.preventDefault();event.stopImmediatePropagation();
  const patient=$('#patient-input').value.trim();
  if(!patient){toast('请填写 RWE 患者编号。');return;}
  const generation=++liveGeneration;liveJob=null;clearPatient();
  const submit=$('#query-form button');submit.disabled=true;
  try {
    const job=await api('/api/jobs',{method:'POST',body:JSON.stringify({patient_number:patient})});
    if(generation===liveGeneration){localStorage.setItem('neurogra.activeJob',job.job_id);await watchJob(job.job_id,generation);}
  }catch(e){toast(e.message);$('#panel').innerHTML=empty('未能启动分析',esc(e.message),false);}
  finally{submit.disabled=false;}
},true);
document.addEventListener('click',async e=>{
  const b=e.target.closest('button');if(!b)return;
  if(b.id==='import-btn'||b.hasAttribute('data-import')){liveGeneration++;liveJob=null;liveExecution=null;}
  if(!b.hasAttribute('data-live-job'))return;
  const generation=++liveGeneration;liveJob=null;clearPatient();
  try{
    if(b.dataset.liveJob){localStorage.setItem('neurogra.activeJob',b.dataset.liveJob);await watchJob(b.dataset.liveJob,generation);}
    else await openReport(b.dataset.liveRun,generation);
  }catch(error){toast(error.message);}
});
async function connectWorkbench(){
  $$('.preview').forEach(e=>e.textContent='本机系统');
  $('.rail-bottom .tiny:last-child').textContent='RWE · 已接入分析服务';
  $('.query-note').textContent='真实分析 · 本机服务';
  $('#query-form button').innerHTML=icon('play')+'开始分析';
  const historyButton=document.createElement('button');
  historyButton.className='btn';historyButton.dataset.nav='history';
  historyButton.innerHTML=icon('clock')+'分析记录';
  $('.page-head .actions').prepend(historyButton);
  $('#patient-input').value='';liveJob=null;clearPatient();renderPanel();
  try{
    liveToken=(await api('/api/session')).token;
    const id=localStorage.getItem('neurogra.activeJob');
    if(id&&/^[a-f0-9]{32}$/.test(id)) {
      try{await api('/api/jobs/'+id);watchJob(id,++liveGeneration);}catch{localStorage.removeItem('neurogra.activeJob');}
    }
  }catch(e){toast('服务连接失败：'+e.message);}
}
connectWorkbench();
