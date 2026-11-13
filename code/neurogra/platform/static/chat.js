const $ = id => document.getElementById(id);
const state = {patients: [], conversations: [], active: null, patient: null, history: false, roster: 'idle', limit: 30,
  pickerMode: 'primary', pickerContinuation: null};
const modelPicker = {info: null, busy: false, drafts: new Map(), ollamaUrl: ''};
const jobLabels = {queued: '排队中', running: '分析中', succeeded: '已完成', failed: '分析失败', interrupted: '已中断', cancelled: '已取消'};
const reportErrors = {
  worker_lease_expired: '分析已中断，请重新发起分析。',
  platform_ownership_lost: '分析已中断，请重新发起分析。',
  platform_deadline: '分析时间已用完，请重新发起分析。',
  platform_cancelled: '分析已取消。',
  model_queue_full: '模型等待队列已满，请稍后重新发起分析。',
  model_queue_deadline: '等待模型超时，请稍后重新发起分析。',
};
const stages = {waiting_for_model_resource: '等待模型资源', preparing: '整理患者资料', context_plan: '制定分析计划', llm_started: '模型分析中', analysis_batch: '处理表单资料', report_ready: '正在准备报告'};
const roles = {history: '病史智能体', cognition: '认知智能体', laboratory: '检验智能体', imaging: '影像智能体'};
const fileLabels = {markdown: '完整报告', data_appendix: '资料附录', audit: '执行审计', provenance: '来源记录'};
let csrf = '';
let pollTimer;
let detailVersion = 0;

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const use = document.createElementNS(svg.namespaceURI, 'use');
  svg.classList.add('icon');
  svg.setAttribute('aria-hidden', 'true');
  use.setAttribute('href', '#' + name);
  svg.append(use);
  return svg;
}

function button(label, className, action) {
  const node = element('button', label, className);
  node.type = 'button';
  node.addEventListener('click', action);
  return node;
}

async function request(path, body) {
  const response = await fetch(path, {
    cache: 'no-store',
    ...(body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Workbench-Token': csrf}, body: JSON.stringify(body)})
  });
  if (response.status === 401) {
    location.replace('/login');
    throw new Error('登录已失效，请重新登录。');
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败，请重试。');
  return data;
}

async function modelAction(action) {
  if (modelPicker.busy) return;
  modelPicker.busy = true;
  $('model-fields').disabled = true;
  try {
    await action();
  } catch (error) {
    $('model-status').textContent = error.message;
  } finally {
    modelPicker.busy = false;
    $('model-fields').disabled = !modelPicker.info;
  }
}

function showModelProvider() {
  const selected = modelPicker.info.providers.find(item => item.id === $('model-provider').value);
  const local = selected.id === 'ollama';
  $('model-endpoint').readOnly = !local;
  $('model-endpoint').value = local ? modelPicker.ollamaUrl : selected.endpoint;
  const key = selected.key_configured ? 'Key 已配置' : `请在 ${modelPicker.info.credentials_file} 中填写 Key`;
  $('model-key-status').textContent = local ? '' : key +
    (modelPicker.info.external_patient_data ? ' · 病例外发已启用' : ' · 病例外发关闭');
}

function applyModelSettings(info) {
  modelPicker.info = info;
  modelPicker.ollamaUrl = info.model.base_url;
  modelPicker.drafts.set(info.model.provider, info.model.model);
  $('current-model').textContent = info.model.model;
  const provider = info.providers.find(item => item.id === info.model.provider);
  const label = `${provider.label} · ${info.model.model}`;
  $('active-model').textContent = '当前模型 · ' + label;
  $('model-trigger').title = label;
  $('model-provider').replaceChildren(...info.providers.map(item => {
    const option = element('option', item.label);
    option.value = item.id;
    return option;
  }));
  $('model-provider').value = info.model.provider;
  $('model-id').value = info.model.model;
  $('model-timeout').value = info.model.timeout_seconds;
  $('model-context').value = info.model.context_tokens;
  $('model-output').value = info.model.max_output_tokens;
  $('model-list-field').hidden = true;
  showModelProvider();
}

async function loadModelSettings() {
  await modelAction(async () => {
    $('model-status').textContent = '正在读取模型配置…';
    applyModelSettings(await request('/api/llm'));
    $('model-status').textContent = '';
  });
}

function closeModelPicker(restoreFocus = false) {
  if ($('model-panel').hidden) return;
  $('model-panel').hidden = true;
  $('model-trigger').setAttribute('aria-expanded', 'false');
  if (restoreFocus) $('model-trigger').focus();
}

async function toggleModelPicker() {
  if (!$('model-panel').hidden) {
    closeModelPicker(true);
    return;
  }
  $('model-panel').hidden = false;
  $('model-trigger').setAttribute('aria-expanded', 'true');
  await loadModelSettings();
  if (!$('model-panel').hidden && document.activeElement === $('model-trigger') && !$('model-fields').disabled) {
    $('model-provider').focus();
  }
}

function modelValues() {
  return {provider: $('model-provider').value, model: $('model-id').value.trim(),
    base_url: modelPicker.ollamaUrl.trim(), timeout_seconds: Number($('model-timeout').value),
    context_tokens: Number($('model-context').value), max_output_tokens: Number($('model-output').value)};
}

function validModelForm() {
  if ($('model-fields').disabled) return false;
  if ($('model-advanced').querySelector(':invalid')) $('model-advanced').open = true;
  return $('model-form').reportValidity();
}

const patientPath = patient => '/api/patients/' + encodeURIComponent(patient.patientKey);
const sessionPath = patient => patient ? patientPath(patient) + '/chat' : '/api/chat';
const chatPath = conversation => sessionPath(conversation.patient) + (conversation.id ? '/' + encodeURIComponent(conversation.id) : '');
const analysisPath = patient => patientPath(patient) + '/analysis';
const artifactPath = (card, file) => analysisPath(card.patient) + '/' + encodeURIComponent(card.job.job_id) + '/artifacts/' + encodeURIComponent(file.artifact_id);
const patientLabel = patient => patient.name === patient.id ? patient.name : `${patient.name} · ${patient.id}`;
const activeJob = job => ['queued', 'running'].includes(job?.status);
const filesFor = card => (card.attempt?.artifacts || []).filter(file => file.status === 'available');

async function loadPatients() {
  if (state.roster === 'loading') return;
  state.roster = 'loading';
  renderPatients();
  try {
    const data = await request('/api/patients');
    state.patients = data.patients;
    state.roster = 'ready';
  } catch {
    state.roster = 'failed';
  }
  renderPatients();
}

function renderPatients() {
  const search = $('patient-search').value.trim().toLowerCase();
  const patients = state.patients.filter(patient => patientLabel(patient).toLowerCase().includes(search));
  $('picker-status').textContent = state.roster === 'loading' ? '正在读取患者…' : state.roster === 'failed' ? '患者列表读取失败。' : patients.length ? `找到 ${patients.length} 位患者` : '没有匹配的患者。';
  $('retry-patients').hidden = state.roster !== 'failed';
  $('more-patients').hidden = patients.length <= state.limit;
  $('patient-options').replaceChildren(...patients.slice(0, state.limit).map(patient => {
    const option = button(undefined, 'patient-option', async () => {
      $('patient-picker').close();
      if (state.pickerMode === 'comparison') {
        const conversation = state.active;
        if (!conversation?.patient || patient.patientKey === conversation.patient.patientKey) return;
        if (conversation.comparisonSaving) return;
        const previous = [...conversation.comparisonPatients];
        const index = conversation.comparisonPatients.findIndex(item => item.patientKey === patient.patientKey);
        if (index >= 0) conversation.comparisonPatients.splice(index, 1);
        else if (conversation.comparisonPatients.length < 5) conversation.comparisonPatients.push(patient);
        else { $('page-status').textContent = '最多选择 5 位对比患者。'; return; }
        syncControls();
        conversation.comparisonSaving = true;
        syncControls();
        const continuation = state.pickerContinuation;
        state.pickerContinuation = null;
        try {
          await ensureConversation(conversation);
          const result = await request(chatPath(conversation) + '/subjects', {
            patient_ids: conversation.comparisonPatients.map(item => item.patientKey)});
          conversation.comparisonPatients = result.patients;
          syncControls();
          if (continuation && index < 0) continuation();
        } catch (error) {
          conversation.comparisonPatients = previous;
          if (state.active === conversation) $('page-status').textContent = error.message;
          syncControls();
        } finally {
          conversation.comparisonSaving = false;
          syncControls();
        }
      } else selectPatient(patient);
    });
    const selected = state.active?.comparisonPatients?.some(item => item.patientKey === patient.patientKey);
    option.append(element('strong', patient.name + (state.pickerMode === 'comparison' && selected ? ' · 已选择' : '')),
      element('small', `编号 ${patient.id}`));
    if (state.pickerMode === 'comparison' && patient.patientKey === state.active?.patient?.patientKey) option.disabled = true;
    return option;
  }));
}

function openPicker(mode = 'primary', continuation = null) {
  state.pickerMode = mode;
  state.pickerContinuation = continuation;
  $('picker-title').textContent = mode === 'comparison' ? '选择对比患者' : '选择患者';
  $('patient-search').value = '';
  state.limit = 30;
  $('patient-picker').showModal();
  $('patient-search').focus();
  loadPatients();
}

function newConversation(patient, id = null, created = null) {
  return {patient, id, created, messages: [], draft: '', context: null, comparisonPatients: [],
    sending: false, submitting: false, loaded: !id};
}

async function loadSessions(patient) {
  try {
    const {sessions} = await request(sessionPath(patient));
    for (const {id, created} of sessions) {
      if (!state.conversations.some(item => item.id === id)) state.conversations.push(newConversation(patient, id, created));
    }
    if (!patient) $('legacy-chat').hidden = !sessions.length;
    renderSidebar();
  } catch (error) {
    if (patient && state.patient?.patientKey === patient.patientKey) $('page-status').textContent = error.message;
  }
}

async function selectPatient(patient, restoreId = null) {
  if ($('detail').open) $('detail').close();
  state.patient = patient;
  state.history = false;
  const existing = state.conversations.find(item => item.patient?.patientKey === patient.patientKey &&
    (!restoreId || item.id === restoreId));
  const target = existing || newConversation(patient);
  activate(target);
  await loadSessions(patient);
  if (restoreId && state.active === target) {
    const saved = state.conversations.find(item => item.patient?.patientKey === patient.patientKey && item.id === restoreId);
    if (saved && saved !== target) activate(saved);
  }
}

async function ensureConversation(conversation) {
  if (conversation.id) return;
  conversation.creating ??= request(sessionPath(conversation.patient), {}).then(({id}) => {
    conversation.id = id;
    conversation.created = Date.now() / 1000;
    if (state.active === conversation) rememberConversation(conversation);
    renderSidebar();
  }).finally(() => { conversation.creating = null; });
  await conversation.creating;
}

async function loadConversation(conversation) {
  if (conversation.loaded || conversation.loading) return;
  conversation.loading = true;
  syncControls();
  try {
    const {messages, selected_patients: selectedPatients = []} = await request(chatPath(conversation));
    conversation.messages = messages;
    conversation.comparisonPatients = selectedPatients;
    conversation.loaded = true;
    renderThread(conversation, true);
    await Promise.all(messages.filter(item => item.kind === 'analysis').map(async card => {
      try {
        const job = await request(analysisPath(card.patient) + '/' + encodeURIComponent(card.job_id));
        await updateCard(conversation, card, job);
      } catch (error) {
        card.error = error.message;
      }
    }));
  } catch (error) {
    if (state.active === conversation) $('page-status').textContent = error.message;
  } finally {
    conversation.loading = false;
    renderSidebar();
    renderThread(conversation);
    schedulePoll(conversation);
  }
}

function toggleMenu(open) {
  $('sidebar').classList.toggle('is-open', open);
  $('menu-toggle').setAttribute('aria-expanded', String(open));
}

function activate(conversation) {
  clearTimeout(pollTimer);
  $('page-status').textContent = '';
  state.conversations = [conversation, ...state.conversations.filter(item => item !== conversation &&
    (item.id || item.draft || item.messages.length || item.sending || item.submitting))].filter(Boolean);
  state.active = conversation;
  if (conversation?.patient) rememberConversation(conversation);
  $('question').value = conversation?.draft || '';
  toggleMenu(false);
  renderSidebar();
  renderThread(conversation, true);
  if (conversation) {
    schedulePoll(conversation, 0);
    loadConversation(conversation);
  }
}

function rememberConversation(conversation) {
  sessionStorage.setItem('neurogra-patient-chat', JSON.stringify({
    patient: conversation.patient.patientKey, sid: conversation.id
  }));
}

function renderSidebar() {
  const list = $('conversations');
  list.replaceChildren(...state.conversations.filter(item => state.history ? !item.patient :
    item.patient && item.patient.patientKey === state.patient?.patientKey).map(conversation => {
    const firstQuestion = conversation.messages.find(item => item.kind === 'user')?.text;
    const label = firstQuestion || (conversation.created ? '会话 · ' + formatTime(conversation.created) : '新会话');
    const entry = button(undefined, 'conversation', () => { activate(conversation); $('question').focus(); });
    entry.title = label;
    if (conversation === state.active) entry.setAttribute('aria-current', 'true');
    entry.append(icon('chat'), element('span', label));
    return entry;
  }));
}

function syncControls() {
  const conversation = state.active;
  const activeJobs = conversation?.messages.filter(message => message.kind === 'analysis' && activeJob(message.job)) || [];
  const busy = activeJobs.length > 0;
  const busyLabel = activeJobs.some(message => message.job.status === 'running') ? '运行中' : '排队中';
  const writable = !!conversation?.patient && !state.history;
  $('patient-label').textContent = !state.history && state.patient ? patientLabel(state.patient) : '选择患者';
  $('compare-patient').disabled = !writable || !!conversation?.comparisonSaving;
  const comparison = conversation?.comparisonPatients || [];
  $('compare-label').textContent = comparison.length ? '对比：' + comparison.map(patient => patient.name).join('、') : '添加对比患者';
  $('compare-patient').title = comparison.length ? '点击已选择的患者可移除' : '显式选择其他患者';
  $('welcome-patient').hidden = writable;
  $('question').placeholder = state.history ? '旧会话仅供查看' : writable ? '输入消息…' : '请先选择患者';
  $('question').disabled = !writable;
  $('send').disabled = !writable || conversation.loading || conversation.sending || !$('question').value.trim();
  $('analyze').disabled = !writable || conversation.loading || conversation.submitting;
  $('analyze-label').textContent = conversation?.submitting ? '正在提交' : busy ? `智能分析（${busyLabel}）` : '智能分析';
  $('report-context').hidden = !conversation?.context;
  $('context-label').textContent = conversation?.context ? `引用报告 · ${patientLabel(conversation.context.patient)}` : '';
}

function renderThread(conversation, scroll = false) {
  if (state.active !== conversation) return;
  const timeline = $('timeline');
  const nearBottom = timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < 48;
  const messages = $('messages');
  const items = conversation?.messages || [];
  $('welcome').hidden = items.length > 0;
  messages.hidden = !items.length;
  messages.replaceChildren(...items.map((message, index) => message.kind === 'analysis' ? renderCard(conversation, message) : renderMessage(conversation, message, index)));
  syncControls();
  if (scroll || nearBottom) timeline.scrollTop = timeline.scrollHeight;
}

function renderMessage(conversation, message, index) {
  const node = element('article', undefined, 'message ' + message.kind);
  node.setAttribute('aria-label', message.kind === 'user' ? '用户消息' : '助手回复');
  const bubble = element('div', undefined, 'bubble');
  if (message.kind === 'assistant') {
    const mark = element('span', undefined, 'message-mark');
    mark.append(icon('logo'));
    node.append(mark);
    bubble.append(window.renderPatientReport(message.text, {anchorPrefix: `chat-message-${index}-`, openFile: () => null}));
    if (message.result_status === 'partial') bubble.append(element('span', '部分完成', 'badge'));
    if (message.interaction?.type === 'confirm_report') {
      const actions = element('div', undefined, 'card-actions');
      if (message.interaction.resolved) actions.append(element('span', '该选择已处理或已过期。', 'analysis-meta'));
      else for (const [choice, label] of [['reanalyze', '重新分析'], ['view_previous', '查看旧报告']]) {
        const option = button(label, 'button', () => answerInteraction(conversation, message, choice));
        option.disabled = !!message.interaction.busy;
        actions.append(option);
      }
      bubble.append(actions);
    }
    if (message.interaction?.type === 'select_patients') {
      const actions = element('div', undefined, 'card-actions');
      actions.append(button('选择对比患者', 'button', () => {
        const original = conversation.messages.slice(0, index).reverse().find(item => item.kind === 'user')?.text;
        openPicker('comparison', () => {
          if (state.active !== conversation || !original) return;
          $('question').value = original;
          sendQuestion();
        });
      }));
      bubble.append(actions);
    }
    if (message.turn?.resumable && ['interrupted', 'failed'].includes(message.turn.status)) {
      const actions = element('div', undefined, 'card-actions');
      const resume = button('恢复分析', 'button', () => resumeTurn(conversation, message));
      resume.disabled = !!message.resuming;
      actions.append(resume);
      bubble.append(actions);
    }
    if (message.comparison) bubble.append(renderComparison(message.comparison));
    if (message.collaboration_progress?.available && message.turn?.status !== 'completed') {
      const progress = message.collaboration_progress;
      const panel = element('details', undefined, 'reply-evidence');
      panel.append(element('summary', `协作进度 · ${progress.tasks.length} 项任务`));
      const list = element('ol');
      for (const task of progress.tasks) {
        const label = roles[task.agent_type] || {
          'patient.read': '患者资料读取', 'knowledge.search': '本地知识检索',
          'evidence.review': '依据核查', 'answer.synthesize': '综合回答',
          'dialogue.delegate': '局部专科分析', 'scope.clarify': '确认任务范围',
          'comparison.run': '患者比较', 'cohort.aggregate': '群体统计', 'report.request': '完整报告',
        }[task.kind] || '协作任务';
        list.append(element('li', `${label} · ${taskStatus(task.state)}`));
      }
      panel.append(list);
      bubble.append(panel);
    }
    if (message.sources?.length) {
      const evidence = element('details', undefined, 'reply-evidence');
      evidence.append(element('summary', '查看回答依据'));
      message.sources.forEach((source, i) => evidence.append(renderFollowupSource(source, i + 1)));
      bubble.append(evidence);
    }
    if (message.workflow || message.collaboration?.length) {
      const panel = element('details', undefined, 'reply-evidence');
      const collaboration = message.collaboration || [];
      const unit = collaboration.some(task => task.node) ? '项专科任务' : '位专科智能体';
      panel.append(element('summary', collaboration.length ? `本轮协作 · ${collaboration.length} ${unit}` : '本轮执行'));
      if (message.workflow) panel.append(renderWorkflow(message.workflow));
      else if (message.report_ref) panel.append(element('p', '证据来源 · 引用报告快照', 'analysis-meta'));
      for (const task of collaboration) {
        const row = element('section', undefined, 'collaboration-task');
        const status = taskStatus(task.status);
        row.append(element('strong', (roles[task.role] || '专科智能体') + (task.node ? ` · ${task.node}` : '')),
          element('span', status, 'badge'));
        row.append(element('p', task.question));
        if (task.depends_on) row.append(element('p', '前置任务 · ' + task.depends_on, 'analysis-meta'));
        if (task.output) row.append(element('p', '任务输出 · ' + task.output));
        if (task.reused) row.append(element('p', '来源核对后复用上轮结果', 'analysis-meta'));
        for (const gap of task.gaps || []) row.append(element('p', gap, 'analysis-meta'));
        for (const request of task.requests || []) {
          const requestStatus = {retrieved: '已补查（仍需核实）', attempted: '已尝试（未完成）',
            not_executed: '待核实（未执行）'}[request.status] || '状态待核实';
          row.append(element('p', `${requestStatus}：${request.question}`, 'analysis-meta'));
          if (request.reason) row.append(element('p', `提出原因：${request.reason}`, 'analysis-meta'));
        }
        for (const issue of task.review_issues || []) {
          if (issue.target_claim) row.append(element('p', `待核查观点：${issue.target_claim}`, 'analysis-meta'));
          row.append(element('p', `待核查：${issue.reason}`, 'analysis-meta'));
          if (issue.basis?.length) {
            for (const basis of issue.basis) {
              const kind = basis.type === 'patient' ? '患者记录' : '本地知识';
              const detail = [basis.title, basis.date, basis.version].filter(Boolean).join(' · ');
              row.append(element('p', `关联${kind}：${detail} — ${basis.text || ''}`, 'analysis-meta'));
            }
          } else if (issue.basis_ids?.length) {
            row.append(element('p', `关联依据：${issue.basis_ids.join('、')}`, 'analysis-meta'));
          }
        }
        for (const source of task.sources || []) row.append(renderFollowupSource(source));
        panel.append(row);
      }
      bubble.append(panel);
    }
  } else {
    bubble.append(element('p', message.text));
  }
  const reportCard = message.report_ref && conversation.messages.find(card => card.kind === 'analysis' &&
    (card.job?.job_id || card.job_id) === message.report_ref?.job_id);
  const attempt = reportCard?.job?.attempts.find(item =>
    item.artifacts.some(file => file.artifact_id === message.report_ref.artifact_id));
  const report = attempt ? {...reportCard, attempt} : message.report_ref ? null : message.report;
  if (report) bubble.append(button('查看引用的分析报告', 'source-link', () => showReport(report)));
  node.append(bubble);
  return node;
}

function taskStatus(status) {
  return {completed: '已完成', succeeded: '已完成', pending: '等待依赖', ready: '等待执行',
    running: '正在执行', partial: '部分完成', skipped: '未执行', skipped_no_data: '无记录',
    waiting: '等待确认', interrupted: '已中断', cancelled: '已取消', stale: '等待更新',
    date_unknown: '日期待核实', conflict: '来源冲突', timed_out: '超时', failed: '执行失败'}[status] || '状态未知';
}

function renderWorkflow(workflow) {
  const detail = element('div', undefined, 'analysis-meta');
  const route = {rules: '规则规划', model: '模型规划', clarify: '等待澄清'}[workflow.route];
  const source = workflow.source === 'knowledge' ? '本地知识' : '患者资料与知识';
  detail.append(element('p', `${source} · ${route} · 表单读取任务 ${workflow.reads} 项 · 已标记复用 ${workflow.reused} 项结果`));
  if (workflow.removed.length) detail.append(element('p', '本轮移除 · ' + workflow.removed.join('、')));
  const list = element('ol');
  for (const task of workflow.tasks) {
    const label = task.action === 'reused' ? `${taskStatus(task.status)}（复用已保存结果）` : taskStatus(task.status);
    const entry = element('li', `${task.title} · ${label}`);
    if (task.dependencies.length) entry.append(element('p', '依赖 · ' + task.dependencies.join('、')));
    if (task.detail) entry.append(element('p', task.detail));
    if (task.evidence_refs.length) entry.append(element('p', '来源记录 · ' + task.evidence_refs.join('、')));
    list.append(entry);
  }
  if (list.childElementCount) detail.append(list);
  return detail;
}

function renderComparison(result) {
  const details = element('details', undefined, 'reply-evidence');
  details.append(element('summary', '查看访视数据与计算依据'));
  const {start, end, last} = result.window;
  const window = start || end ? `${start || '最早记录'} 至 ${end || '最新记录'}` : '可读取的历史';
  details.append(element('p', `查询范围 · ${window}${last ? ` · 最近 ${last} 个访视日` : ''}`, 'analysis-meta'));
  if (!result.records.length) return details;
  const container = element('div', undefined, 'comparison-table-scroll');
  const table = element('table', undefined, 'comparison-table');
  const head = element('tr');
  for (const title of ['访视日期', '原始总分', '较上次变化', '来源与说明']) {
    const column = element('th', title);
    column.scope = 'col';
    head.append(column);
  }
  const thead = element('thead');
  thead.append(head);
  const body = element('tbody');
  const changes = new Map(result.changes.map(step => [step.date, step.delta]));
  for (const record of result.records) {
    const row = element('tr');
    const source = [record.record_id && `记录 ${record.record_id}`, record.field,
      record.excluded || [record.version, record.scoring].filter(Boolean).join(' · ')].filter(Boolean).join(' · ');
    for (const value of [record.date || '缺失', record.value, !record.excluded && changes.get(record.date) || '—', source || '—']) {
      row.append(element('td', value));
    }
    body.append(row);
  }
  table.append(thead, body);
  container.append(table);
  details.append(container);
  if (result.total > result.records.length) {
    details.append(element('p', `显示最近 ${result.records.length} 条记录，计算覆盖全部 ${result.total} 条候选记录。`, 'analysis-meta'));
  }
  return details;
}

function renderFollowupSource(source, index) {
  const details = element('details', undefined, 'followup-source');
  details.append(element('summary', index ? `依据 ${index} · ${source.title}` : source.title));
  if (source.text) details.append(element('p', source.text));
  if (source.dates?.length) details.append(element('p', '记录日期 · ' + source.dates.join('、'), 'analysis-meta'));
  if (source.partial) details.append(element('p', '本轮仅覆盖部分记录或字段。', 'analysis-meta'));
  if (source.version || source.page) details.append(element('p', [source.version && `版本 ${source.version}`,
    source.page && `第 ${source.page} 页`].filter(Boolean).join(' · '), 'analysis-meta'));
  for (const record of source.records || []) {
    if (record.date || record.text !== source.text) {
      details.append(element('p', [record.date, record.text].filter(Boolean).join(' · ')));
    }
  }
  if (source.refs?.length) details.append(element('p', '资料引用 · ' + source.refs.join('、'), 'analysis-meta'));
  return details;
}

function formatTime(seconds) {
  return seconds ? new Date(seconds * 1000).toLocaleString('zh-CN', {hour12: false}) : '';
}

function renderCard(conversation, card) {
  const node = element('article', undefined, 'analysis-card');
  const header = element('div', undefined, 'analysis-head');
  const partial = card.attempt?.result?.execution_status === 'partial' || card.report?.status === 'partial';
  const status = card.job ? partial && card.job.status === 'succeeded' ? '部分完成' : jobLabels[card.job.status] || card.job.status : card.error ? '提交未确认' : '正在提交';
  header.append(icon('report'), element('strong', '病例分析'), element('span', status, 'badge'));
  node.append(header);
  const time = card.job?.created ? ' · ' + formatTime(card.job.created) : '';
  node.append(element('p', patientLabel(card.patient) + time, 'analysis-meta'));
  const knowledgeNote = card.job?.payload?.patient_workspace?.knowledge_note;
  if (knowledgeNote) node.append(element('p', knowledgeNote, 'analysis-stage'));
  const latest = card.attempt?.events?.find(event => stages[event.kind]);
  if (activeJob(card.job) && latest) node.append(element('p', stages[latest.kind], 'analysis-stage'));
  const collaboration = card.job?.collaboration;
  if (collaboration?.tasks?.length) {
    const panel = element('details', undefined, 'reply-evidence');
    panel.append(element('summary', `协作进度 · ${collaboration.tasks.length} 项任务`));
    const list = element('ol');
    const labels = {'patient.read': '患者资料读取', 'knowledge.search': '本地知识检索',
      'imaging.predict': '影像工具', 'answer.synthesize': '报告综合'};
    for (const task of collaboration.tasks) {
      const label = roles[task.agent_type] || labels[task.kind] || '协作任务';
      list.append(element('li', `${label} · ${taskStatus(task.state)}`));
    }
    panel.append(list);
    node.append(panel);
  }
  if (card.report?.summary) node.append(element('p', card.report.summary));
  if (card.report?.unresolved?.length) node.append(element('p', '待核实 · ' + card.report.unresolved.slice(0, 3).join('；'), 'analysis-stage'));
  const attemptError = card.attempt?.error;
  const message = card.error || (attemptError ? reportErrors[attemptError] || '分析未完成，请重新发起分析。' : '');
  if (message) node.append(element('p', message, 'analysis-stage'));
  if (card.job?.status === 'succeeded' && !filesFor(card).some(file => file.kind === 'markdown')) node.append(element('p', '报告文件暂不可用。', 'analysis-stage'));
  const actions = element('div', undefined, 'card-actions');
  if (filesFor(card).some(file => file.kind === 'markdown')) actions.append(button('查看完整报告', 'button', () => showReport(card)));
  if (card.report) {
    actions.append(button('分析依据', 'button', () => showEvidence(card)));
    if (conversation.patient) actions.append(button('追问此报告', 'button', () => {
      conversation.context = card;
      syncControls();
      $('question').focus();
    }));
  }
  if (actions.childElementCount) node.append(actions);
  return node;
}

async function sendQuestion() {
  const conversation = state.active;
  const text = $('question').value.trim();
  if (!conversation?.patient || !text || conversation.sending || conversation.loading) return;
  if (!conversation.loaded) {
    await loadConversation(conversation);
    if (state.active !== conversation || !conversation.loaded || conversation.sending) return;
  }
  conversation.sending = true;
  conversation.draft = '';
  const report = conversation.context;
  const reportFile = report && filesFor(report).find(file => file.kind === 'json');
  const reportRef = reportFile ? {job_id: report.job.job_id, artifact_id: reportFile.artifact_id} : null;
  const comparisonIds = conversation.comparisonPatients.map(patient => patient.patientKey);
  const inputKey = JSON.stringify([text, reportRef, comparisonIds]);
  const requestId = conversation.retry?.key === inputKey ? conversation.retry.id : crypto.randomUUID();
  conversation.retry = {key: inputKey, id: requestId};
  $('page-status').textContent = '';
  $('question').value = '';
  const user = {kind: 'user', text, report};
  const reply = {kind: 'assistant', text: report ? '正在核对报告与相关资料…' : '正在准备回复…'};
  conversation.messages.push(user, reply);
  renderThread(conversation, true);
  try {
    await ensureConversation(conversation);
    const data = await request(chatPath(conversation), {message: text, request_id: requestId, report: reportRef,
      comparison_patient_ids: comparisonIds,
      capabilities: ['interactions_v1', 'async_turns_v1', 'collaboration_progress_v1']});
    Object.assign(reply, data.reply, {report, turn: data.turn});
    conversation.retry = null;
    if (reply.analysis_job_id) await attachAnalysis(conversation, reply.analysis_job_id);
    renderSidebar();
  } catch (error) {
    conversation.messages = conversation.messages.filter(message => message !== user && message !== reply);
    if (!conversation.draft) {
      conversation.draft = text;
      conversation.context ??= report;
      if (state.active === conversation) $('question').value = text;
    }
    if (state.active === conversation) $('page-status').textContent = error.message;
  } finally {
    conversation.sending = false;
    renderThread(conversation);
    schedulePoll(conversation);
  }
}

async function attachAnalysis(conversation, jobId) {
  let card = conversation.messages.find(item => item.kind === 'analysis' &&
    (item.job_id || item.job?.job_id) === jobId);
  if (!card) {
    card = {kind: 'analysis', patient: conversation.patient, job_id: jobId};
    conversation.messages.push(card);
  }
  try {
    await updateCard(conversation, card, await request(analysisPath(conversation.patient) + '/' + encodeURIComponent(jobId)));
  } catch (error) {
    card.error = error.message;
  }
  schedulePoll(conversation);
}

async function answerInteraction(conversation, message, choice) {
  const interaction = message.interaction;
  if (!interaction || interaction.busy || interaction.resolved) return;
  interaction.busy = true;
  renderThread(conversation);
  try {
    const data = await request(chatPath(conversation), {request_id: crypto.randomUUID(),
      action_response: {action_id: interaction.action_id, choice}, capabilities: ['interactions_v1']});
    interaction.resolved = true;
    conversation.messages.push({kind: 'user', text: choice === 'reanalyze' ? '重新分析' : '查看旧报告'}, data.reply);
    if (data.reply.analysis_job_id) await attachAnalysis(conversation, data.reply.analysis_job_id);
    renderSidebar();
  } catch (error) {
    if (state.active === conversation) $('page-status').textContent = error.message;
  } finally {
    interaction.busy = false;
    renderThread(conversation, true);
  }
}

async function resumeTurn(conversation, message) {
  if (message.resuming || !message.turn?.resumable) return;
  message.resuming = true;
  renderThread(conversation);
  try {
    const path = chatPath(conversation) + '/turns/' + encodeURIComponent(message.turn.request_id) + '/resume';
    const data = await request(path, {});
    Object.assign(message, data.reply, {turn: data.turn});
  } catch (error) {
    if (state.active === conversation) $('page-status').textContent = error.message;
  } finally {
    message.resuming = false;
    renderThread(conversation);
    schedulePoll(conversation, 0);
  }
}

async function updateCard(conversation, card, job) {
  card.job = job;
  card.error = '';
  card.attempt = job.attempts.at(-1);
  renderThread(conversation);
  const file = filesFor(card).find(item => item.kind === 'json');
  if (activeJob(job) || !file || card.report) return;
  try {
    const response = await request(artifactPath(card, file));
    card.report = JSON.parse(response.text);
  } catch (error) {
    card.error = `报告摘要读取失败。${error.message}`;
  }
  renderThread(conversation);
}

function schedulePoll(conversation, delay = 2500) {
  if (state.active !== conversation) return;
  clearTimeout(pollTimer);
  const pending = conversation.messages.filter(card => card.kind === 'analysis' && activeJob(card.job));
  const turns = conversation.messages.filter(message => message.kind === 'assistant' &&
    ['accepted', 'waiting_model', 'running'].includes(message.turn?.status));
  if (!pending.length && !turns.length) return;
  pollTimer = setTimeout(async () => {
    for (const message of turns) {
      try {
        const path = chatPath(conversation) + '/turns/' + encodeURIComponent(message.turn.request_id);
        const result = await request(path);
        Object.assign(message, result.reply, {turn: result.turn});
        if (result.turn.status === 'completed') {
          delete message.collaboration_progress;
        } else {
          try {
            message.collaboration_progress = await request(path + '/collaboration');
          } catch (_) {
            delete message.collaboration_progress;
          }
        }
        if (result.turn.status === 'completed' && message.analysis_job_id) {
          await attachAnalysis(conversation, message.analysis_job_id);
        }
      } catch (error) {
        if (state.active === conversation) $('page-status').textContent = `暂时无法刷新对话状态。${error.message}`;
      }
    }
    for (const card of pending) {
      try {
        const job = await request(analysisPath(card.patient) + '/' + encodeURIComponent(card.job.job_id));
        await updateCard(conversation, card, job);
      } catch (error) {
        card.error = `暂时无法刷新任务状态。${error.message}`;
        renderThread(conversation);
      }
    }
    renderThread(conversation);
    schedulePoll(conversation, pending.some(card => card.error) ? 6500 : 2500);
  }, delay);
}

async function startAnalysis() {
  const conversation = state.active;
  const patient = conversation?.patient;
  if (!patient || $('analyze').disabled) return;
  conversation.submitting = true;
  const requestId = conversation.analysisRetry?.patient === patient.patientKey ?
    conversation.analysisRetry.id : crypto.randomUUID();
  const card = conversation.messages.find(item => item.kind === 'analysis' && item.requestId === requestId) ||
    {kind: 'analysis', patient, requestId};
  conversation.analysisRetry = {patient: patient.patientKey, id: requestId};
  card.error = '';
  if (!conversation.messages.includes(card)) conversation.messages.push(card);
  renderThread(conversation, true);
  try {
    await ensureConversation(conversation);
    const job = await request(chatPath(conversation) + '/analysis', {request_id: requestId});
    conversation.analysisRetry = null;
    const existing = conversation.messages.find(item => item !== card && item.kind === 'analysis' && item.job?.job_id === job.job_id);
    if (existing) conversation.messages = conversation.messages.filter(item => item !== card);
    await updateCard(conversation, existing || card, job);
  } catch (error) {
    card.error = error.message;
  } finally {
    conversation.submitting = false;
    renderThread(conversation);
    schedulePoll(conversation);
  }
}

function showDetail(title, patient, content) {
  $('detail-title').textContent = title;
  $('detail-subtitle').textContent = patientLabel(patient);
  $('detail-body').replaceChildren(content);
  if (!$('detail').open) $('detail').showModal();
  $('detail').scrollTop = 0;
  return ++detailVersion;
}

async function showReport(card, kind = 'markdown') {
  const file = filesFor(card).find(item => item.kind === kind);
  if (!file) {
    showDetail('病例分析报告', card.patient, element('p', '该报告文件暂不可用。'));
    return;
  }
  const version = showDetail(fileLabels[kind] || '病例分析报告', card.patient, element('p', '正在读取报告…'));
  const path = artifactPath(card, file);
  try {
    const data = await request(path);
    if (version !== detailVersion || !$('detail').open) return;
    const actions = element('div', undefined, 'card-actions');
    const download = element('a', '下载当前文件', 'button');
    download.href = path + '?download';
    actions.append(download);
    for (const item of filesFor(card)) {
      if (item.kind !== kind && fileLabels[item.kind]) actions.append(button(fileLabels[item.kind], 'button', () => showReport(card, item.kind)));
    }
    const report = kind === 'provenance' ? element('pre', data.text) : window.renderPatientReport(data.text, {
      anchorPrefix: 'chat-report-',
      openFile: (label, target) => filesFor(card).some(item => item.kind === target) ? button(label, 'source-link', () => showReport(card, target)) : null
    });
    $('detail-body').replaceChildren(actions, report);
  } catch (error) {
    if (version === detailVersion) $('detail-body').replaceChildren(element('p', error.message));
  }
}

async function showEvidence(card) {
  const version = showDetail('分析依据', card.patient, element('p', '正在读取执行依据…'));
  const file = filesFor(card).find(item => item.kind === 'provenance');
  let taskRoles = new Map();
  try {
    if (file) {
      const data = JSON.parse((await request(artifactPath(card, file))).text);
      taskRoles = new Map((data.tasks?.tasks || []).map(task => [task.task_id, roles[task.agent_type]]));
    }
  } catch { /* Older reports can still display their saved findings. */ }
  if (version !== detailVersion || !$('detail').open) return;
  const content = element('div');
  const results = card.report.agent_results || [];
  if (!results.length) content.append(element('p', '这份报告未提供可展示的专科执行记录。'));
  for (const result of results) {
    const title = taskRoles.get(result.task_id) || '专科执行记录';
    const status = {completed: '已完成', partial: '部分完成', skipped_no_data: '缺少资料', failed: '未完成'}[result.status];
    content.append(element('h3', title + (status ? ` · ${status}` : '')));
    const findings = [...new Set([...(result.findings || []), ...(result.claims || []).map(claim => claim.text).filter(Boolean)])];
    for (const finding of findings) content.append(element('p', finding));
    if (result.used_refs?.length) content.append(element('p', '资料引用 · ' + result.used_refs.join('、'), 'analysis-meta'));
  }
  if (card.report.claims?.length) {
    content.append(element('h3', '报告综合判断'));
    for (const claim of card.report.claims) {
      content.append(element('p', claim.text));
      const refs = [...new Set([...(claim.observation_ids || []), ...(claim.evidence_ids || [])])];
      if (refs.length) content.append(element('p', '依据 · ' + refs.join('、'), 'analysis-meta'));
    }
  }
  content.append(button('打开完整报告与资料附录', 'button', () => showReport(card)));
  $('detail-body').replaceChildren(content);
}

$('new-chat').addEventListener('click', () => {
  if (!state.patient || state.history) return openPicker();
  activate(newConversation(state.patient));
  $('question').focus();
});
$('menu-toggle').addEventListener('click', () => toggleMenu(!$('sidebar').classList.contains('is-open')));
$('patient-search').addEventListener('input', () => { state.limit = 30; renderPatients(); });
$('more-patients').addEventListener('click', () => { state.limit += 30; renderPatients(); });
$('retry-patients').addEventListener('click', loadPatients);
$('choose-patient').addEventListener('click', () => openPicker());
$('welcome-patient').addEventListener('click', () => openPicker());
$('compare-patient').addEventListener('click', () => openPicker('comparison'));
$('legacy-chat').addEventListener('click', () => {
  if ($('detail').open) $('detail').close();
  state.history = true;
  activate(state.conversations.find(item => !item.patient) || null);
});
$('analyze').addEventListener('click', startAnalysis);
$('clear-context').addEventListener('click', () => { state.active.context = null; syncControls(); });
$('composer').addEventListener('submit', event => { event.preventDefault(); sendQuestion(); });
$('question').addEventListener('input', () => { if (state.active) state.active.draft = $('question').value; syncControls(); });
$('question').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!$('send').disabled) $('composer').requestSubmit();
  }
});
$('logout').addEventListener('click', async () => {
  try { await request('/api/auth/logout', {}); sessionStorage.removeItem('neurogra-patient-chat'); location.replace('/login'); }
  catch (error) { $('page-status').textContent = error.message; }
});
$('detail').addEventListener('close', () => { detailVersion++; });
$('model-trigger').addEventListener('click', toggleModelPicker);
$('close-model').addEventListener('click', () => closeModelPicker(true));
document.addEventListener('pointerdown', event => {
  if (!$('model-panel').contains(event.target) && !$('model-trigger').contains(event.target)) closeModelPicker();
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !$('model-panel').hidden) {
    event.preventDefault();
    closeModelPicker(true);
  }
});
$('model-provider').addEventListener('change', () => {
  $('model-id').value = modelPicker.drafts.get($('model-provider').value) || '';
  $('model-list-field').hidden = true;
  $('model-status').textContent = '';
  showModelProvider();
});
$('model-id').addEventListener('input', () => modelPicker.drafts.set($('model-provider').value, $('model-id').value));
$('model-endpoint').addEventListener('input', () => { modelPicker.ollamaUrl = $('model-endpoint').value; });
$('model-list').addEventListener('change', () => {
  const id = $('model-list').value;
  if (id) {
    $('model-id').value = id;
    modelPicker.drafts.set($('model-provider').value, id);
  }
});
$('discover-models').addEventListener('click', () => modelAction(async () => {
  $('model-list-field').hidden = true;
  $('model-status').textContent = '正在获取模型列表…';
  const {models} = await request('/api/llm/models', {provider: $('model-provider').value, base_url: modelPicker.ollamaUrl.trim()});
  const placeholder = element('option', '请选择模型');
  placeholder.value = '';
  $('model-list').replaceChildren(placeholder, ...models.map(id => {
    const option = element('option', id);
    option.value = id;
    return option;
  }));
  $('model-list-field').hidden = !models.length;
  $('model-status').textContent = models.length ? `已获取 ${models.length} 个模型。` : '供应商未返回模型，可以手填模型 ID。';
}));
$('test-model').addEventListener('click', () => {
  if (!validModelForm()) return;
  modelAction(async () => {
    $('model-status').textContent = '正在测试连接…';
    const {model} = await request('/api/llm/test', modelValues());
    $('model-status').textContent = `${model} 连通性测试通过。`;
  });
});
$('model-form').addEventListener('submit', event => {
  event.preventDefault();
  if (!validModelForm()) return;
  modelAction(async () => {
    $('model-status').textContent = '正在切换模型…';
    applyModelSettings(await request('/api/llm', modelValues()));
    $('model-status').textContent = '';
    closeModelPicker(true);
  });
});

renderSidebar();
try {
  const session = await request('/api/session');
  if (!session.authenticated) location.replace('/login');
  else {
    csrf = session.token;
    const account = await request('/api/auth/session');
    if (!account.authenticated) location.replace('/login');
    else {
      $('account-label').textContent = account.account || 'RWE 工作区';
      activate(null);
      const saved = JSON.parse(sessionStorage.getItem('neurogra-patient-chat') || 'null');
      await Promise.all([loadSessions(null), loadModelSettings(), saved ? loadPatients() : null]);
      const patient = state.patients.find(item => item.patientKey === saved?.patient);
      if (patient && !state.active) await selectPatient(patient, saved.sid);
    }
  }
} catch (error) {
  $('page-status').textContent = error.message;
}
