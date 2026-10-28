'use strict';

// Optional pages share the same API and safe DOM helpers as the core workbench.
window.platformFeatures = {
  knowledge: loadKnowledge,
  settings: loadSettings,
  batches: loadBatches,
};

function selectedDocuments() {
  return Array.from(document.querySelectorAll('#document-list input:checked')).map(input => input.value);
}

async function goToJob(job) {
  selectedJob = job.job_id;
  await show('jobs');
}

async function loadKnowledge() {
  const [sources, versions] = await Promise.all([api('/api/documents'), api('/api/knowledge')]);
  const list = $('document-list');
  list.replaceChildren();
  if (!sources.documents.length) list.append(node('p', '尚未上传文献。'));
  sources.documents.forEach(source => {
    const label = node('label', undefined, 'source-row');
    const input = node('input');
    input.type = 'checkbox';
    input.value = source.document_id;
    input.disabled = source.status !== 'available';
    label.append(input, document.createTextNode(' ' + source.name + ' · ' + (source.bytes / 1024).toFixed(1) + ' KB'));
    if (source.status !== 'available') label.append(badge('原件缺失或损坏', 'warn'));
    list.append(label);
  });
  const target = $('knowledge-versions');
  target.replaceChildren();
  for (const module of ['kb', 'kg']) {
    target.append(node('h3', module === 'kb' ? '原文 KB' : '条件 KG'));
    if (!versions[module].length) target.append(node('p', '暂无已生成版本。'));
    versions[module].forEach(version => {
      const row = node('div', undefined, 'version-row');
      const active = version.release_id === versions['active_' + module];
      const description = module === 'kb'
        ? `${version.documents ?? '—'} 份资料 · ${version.resources ?? '—'} 个原文块 · ${version.vector ? '含向量' : '词法检索'}`
        : `${version.statements ?? '—'} 条声明`;
      row.append(node('p', description), node('small', '版本 ' + version.release_id.slice(-12)));
      row.append(badge(active ? '当前版本' : version.status === 'available' ? '候选或历史版本' : '资源不可用', active ? 'good' : ''));
      if (!active && version.status === 'available') row.append(button('切换为当前版本', async () => {
        await api('/api/knowledge/activate', {module, release_id: version.release_id});
        await loadKnowledge();
        notice('当前知识版本已切换，已有任务继续使用原固定版本。');
      }, true));
      target.append(row);
    });
  }
}

async function uploadDocument() {
  const file = $('document-file').files[0];
  if (!file) return;
  if (file.size > 20000000) throw new Error('单份文献不能超过 20 MB。');
  const content = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.onerror = () => reject(new Error('文件读取失败。'));
    reader.readAsDataURL(file);
  });
  await api('/api/documents', {name: file.name, content});
  await loadKnowledge();
  notice('文献原件已保存，请选择文献进行解析预览。');
  $('document-file').value = '';
}

async function queueDocuments(preview) {
  const ids = selectedDocuments();
  if (!ids.length) throw new Error('请先选择文献。');
  await goToJob(await api(preview ? '/api/documents/preview' : '/api/knowledge/build', {
    document_ids: ids,
    kb: $('build-kb').checked,
    kg: $('build-kg').checked,
    vector: $('build-vector').checked,
  }));
}

window.platformJobResult = function renderExtraResult(container, result) {
  if (result.version_id && result.case_id) {
    container.append(button('查看导入病例', async () => {
      await show('cases');
      await caseDetail(result.case_id);
    }, true));
  }
  if (result.documents) {
    container.append(node('h3', '文献处理结果'));
    result.documents.forEach(source => {
      const box = node('details', undefined, 'version-row');
      const status = {parsed:'解析完成', needs_review:'需要复核', failed:'失败', candidate_ready:'候选就绪', disabled:'未启用', partial:'部分完成'};
      const text = source.status ? status[source.status] || source.status
        : `KB：${status[source.kb] || source.kb}；KG：${status[source.kg] || source.kg}`;
      box.append(node('summary', source.name + ' · ' + text));
      if (source.issues?.length) box.append(node('p', '解析/构建提示：' + source.issues.join('；')));
      (source.preview || []).forEach(block => {
        box.append(node('p', (block.page == null ? '无物理页码' : '第 ' + block.page + ' 页') + ' · ' + block.text));
      });
      container.append(box);
    });
    if (result.kb_release || result.kg_release) {
      container.append(node('p', '候选版本已保存，尚未切换为当前版本。'));
      container.append(button('查看知识版本', () => show('knowledge'), true));
    }
  }
};

function formField(form, text, value, type='text', options={}) {
  const label = node('label', undefined, 'field');
  const input = node('input');
  input.type = type;
  input.value = value ?? '';
  Object.assign(input, options);
  label.append(node('span', text), input);
  form.append(label);
  return input;
}

async function loadSettings() {
  const current = await api('/api/settings');
  const form = node('form', undefined, 'settings-grid');
  form.onsubmit = event => event.preventDefault();
  const model = formField(form, '生成模型', current.model.model, 'text', {required:true});
  const url = formField(form, '生成服务地址', current.model.base_url, 'url', {required:true});
  const timeout = formField(form, '单次模型超时（秒）', current.model.timeout_seconds, 'number', {min:1, required:true});
  const embedding = current.kb?.embedding || current.kg?.embedding || {};
  const embedModel = formField(form, '嵌入模型', embedding.model || 'qwen3-embedding:0.6b');
  const embedUrl = formField(form, '嵌入服务地址', embedding.base_url || 'http://127.0.0.1:11434', 'url');
  const calls = formField(form, '单任务总调用额度', current.budget.llm, 'number', {min:1, required:true});
  const seconds = formField(form, '单任务时限（秒）', current.budget.seconds, 'number', {min:60, required:true});
  const rounds = formField(form, '最多分析轮次', current.max_rounds, 'number', {min:1, max:3, required:true});
  const parallel = formField(form, '专科并发上限', current.max_parallel_tasks, 'number', {min:1, max:8, required:true});
  const graph = current.kg?.graph || {};
  const uri = formField(form, 'Neo4j 地址', graph.uri || 'bolt://127.0.0.1:7687');
  const database = formField(form, 'Neo4j 数据库', graph.database || 'neo4j');
  const username = formField(form, 'Neo4j 用户名', graph.username || 'neo4j');
  const permissions = node('div', undefined, 'switches');
  const sources = {};
  for (const [name, title] of [['use_kb','默认启用原文 KB'], ['use_kg','默认启用条件 KG']]) {
    const label = node('label');
    const control = node('input');
    control.type = 'checkbox';
    control.checked = current.main_agent[name];
    sources[name] = control;
    label.append(control, document.createTextNode(' ' + title));
    permissions.append(label);
  }
  const save = button('保存运行设置', async () => {
    if (!form.reportValidity()) return;
    const access = Object.fromEntries(Object.entries(sources).map(([key, control]) => [key, control.checked]));
    const agents = Object.fromEntries(Object.keys(current.agents).map(role => [role, access]));
    await api('/api/settings', {
      model:{model:model.value.trim(), base_url:url.value.trim(), timeout_seconds:Number(timeout.value)},
      kb:{embedding:{model:embedModel.value.trim(), base_url:embedUrl.value.trim()}},
      kg:{embedding:{model:embedModel.value.trim(), base_url:embedUrl.value.trim()}, graph:{uri:uri.value.trim(), username:username.value.trim(), database:database.value.trim()}},
      budget:{llm:Number(calls.value), seconds:Number(seconds.value)},
      max_rounds:Number(rounds.value), max_parallel_tasks:Number(parallel.value), main_agent:access, agents,
    });
    notice('运行设置已保存，后续任务使用新设置；已有任务配置保持不变。');
  });
  save.type = 'button';
  $('settings-form').replaceChildren(form, permissions, save);
}

async function loadBatches() {
  const [casesResult, batchesResult] = await Promise.all([api('/api/cases'), api('/api/batches')]);
  const choices = $('batch-cases');
  choices.replaceChildren();
  if (!casesResult.cases.length) choices.append(node('p', '请先导入病例。'));
  casesResult.cases.forEach(source => {
    const label = node('label', undefined, 'source-row');
    const input = node('input');
    input.type = 'checkbox';
    input.value = source.case_id;
    label.append(input, document.createTextNode(' ' + source.patient_number));
    choices.append(label);
  });
  const target = $('batch-list');
  target.replaceChildren();
  if (!batchesResult.batches.length) target.append(node('p', '暂无批次。'));
  batchesResult.batches.forEach(batch => {
    const box = node('details', undefined, 'version-row');
    box.append(node('summary', batch.name + ' · ' + batch.planned + ' 例'));
    box.append(node('p', Object.entries(batch.counts).map(([status,count]) => (labels[status] || status) + ' ' + count).join('；')));
    if (batch.not_submitted.length) box.append(node('p', batch.not_submitted.length + ' 例未提交成功，批次尚不完整。'));
    batch.jobs.forEach(job => box.append(button((labels[job.status] || job.status) + ' · ' + time(job.created), () => goToJob(job), true)));
    target.append(box);
  });
}

$('document-file').onchange = () => action(uploadDocument);
$('preview-documents').onclick = () => action(() => queueDocuments(true));
$('build-documents').onclick = () => action(() => queueDocuments(false));
$('import-rwe').onclick = () => action(async () => {
  const number = $('rwe-number').value.trim();
  if (!number) throw new Error('请输入 RWE 患者编号。');
  await goToJob(await api('/api/rwe/import', {patient_number:number}));
});
$('submit-batch').onclick = () => action(async () => {
  const ids = Array.from(document.querySelectorAll('#batch-cases input:checked')).map(input => input.value);
  if (!ids.length) throw new Error('请选择需要分析的病例。');
  const versions = await Promise.all(ids.map(async id => (await api('/api/cases/' + id + '/versions')).versions[0].version_id));
  await api('/api/batches', {version_ids:versions, profile:$('batch-profile').value, name:$('batch-name').value});
  await loadBatches();
  notice('批次已登记，病例任务通过统一队列执行。');
});
