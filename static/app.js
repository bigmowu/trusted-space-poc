'use strict';

const state = {
  currentSandboxId: null,
  currentRequestId: null,
  currentWatermarkId: null,
  pendingTraceId: null,
  sandboxes: [],
  sandboxDrafts: {},
  sandboxResult: null,
  components: [],
  assets: [],
  exportFilter: 'all',
  exportModal: null,
  traceTab: 'id',
  apiOnline: null,
  apiRequestCount: 0,
  backendLogTimer: null,
};

const ROUTES = {
  overview: {
    title: '安全概览',
    subtitle: '统一查看数据流转、出域风险与安全策略运行状态',
  },
  assets: {
    title: '数据资产',
    subtitle: '登记原始数据资产，按分级与涉密属性管理共享边界',
  },
  sandbox: {
    title: '可信沙箱',
    subtitle: '在隔离环境中运行代码，工作卷产物提交出域前不经外网',
  },
  components: {
    title: '组件中心',
    subtitle: '核验水印引擎、沙箱运行时、出域闸门与存储的真实状态和自检链路',
  },
  export: {
    title: '出域申请',
    subtitle: '按数据分级自动选择快速通道或三级审批通道',
  },
  approval: {
    title: '审批中心',
    subtitle: '审核 L3/L4 敏感数据出域用途与接收方风险',
  },
  trace: {
    title: '水印溯源',
    subtitle: '从泄露文件识别导出人、审批链与完整流转轨迹',
  },
  audit: {
    title: '审计日志',
    subtitle: '追踪每一次沙箱执行、策略阻断、审批状态，并展开后端算法阶段',
  },
};

const STATUS_LABELS = {
  pending: '待审批',
  approved: '已通过',
  exported: '已导出',
  rejected: '已驳回',
  RUNNING: '运行中',
  DESTROYED: '已销毁',
  running: '运行中',
  success: '成功',
  failed: '失败',
  blocked: '已阻断',
  ready: '就绪',
  degraded: '降级',
  unavailable: '不可用',
  fast: '快速通道',
  approval: '审批通道',
};

const ACTION_LABELS = {
  'sandbox.create': '创建沙箱',
  'sandbox.exec': '执行代码',
  'sandbox.exec_timeout': '执行超时',
  'sandbox.destroy': '销毁沙箱',
  raw_write_blocked: '写原始卷阻断',
  'data.generate': '生成数据',
  'export.request': '提交出域',
  'export.fast_approved': '快速通道放行',
  'export.approve': '审批通过',
  'export.reject': '审批驳回',
  'export.transfer': '审批转交',
  'export.execute': '执行导出',
  unauthorized_export: '未授权导出',
  'watermark.embed': '水印嵌入',
  'watermark.extract': '水印提取',
  'trace.query': '按 ID 溯源',
  'trace.file': '上传文件溯源',
};

const OPERATION_LABELS = {
  'app.startup': '应用启动',
  'sandbox.seed': '演示沙箱初始化',
  'sandbox.create': '创建沙箱',
  'sandbox.exec': '执行沙箱代码',
  'sandbox.write': '写入工作卷',
  'sandbox.destroy': '销毁沙箱',
  'export.request': '提交出域申请',
  'export.approval': '审批状态迁移',
  'export.execute': '执行水印导出',
  'watermark.extract': '水印提取',
  'trace.query': '水印溯源查询',
  'component.sandbox.selftest': '沙箱组件自检',
  'component.watermark.selftest': '水印组件自检',
};

function $(selector, root = document) {
  return root.querySelector(selector);
}

function esc(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function encodePath(path) {
  return String(path || '')
    .split('/')
    .map((part) => encodeURIComponent(part))
    .join('/');
}

function sandboxFileUrl(sandboxId, path) {
  return `/api/sandbox/${encodeURIComponent(sandboxId)}/files${encodePath(path)}`;
}

function fmtTime(value, includeDate = true) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return esc(value);
  const options = includeDate
    ? { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }
    : { hour: '2-digit', minute: '2-digit', hour12: false };
  return date.toLocaleString('zh-CN', options);
}

function fmtFullTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return esc(value);
  return date.toLocaleString('zh-CN', { hour12: false });
}

function fileSize(bytes) {
  const number = Number(bytes || 0);
  if (number < 1024) return `${number} B`;
  if (number < 1024 * 1024) return `${(number / 1024).toFixed(1)} KB`;
  return `${(number / 1024 / 1024).toFixed(1)} MB`;
}

function levelBadge(level) {
  const safe = ['L1', 'L2', 'L3', 'L4'].includes(level) ? level : 'L1';
  return `<span class="badge badge-level-${safe}">${safe}</span>`;
}

function statusBadge(status) {
  const safe = String(status || 'pending').replace(/[^a-zA-Z0-9_-]/g, '');
  return `<span class="badge badge-${safe}"><i class="status-dot"></i>${esc(STATUS_LABELS[status] || status)}</span>`;
}

function actionLabel(action) {
  return ACTION_LABELS[action] || action;
}

function operationLabel(operation) {
  return OPERATION_LABELS[operation] || operation;
}

function durationLabel(value) {
  const number = Number(value || 0);
  return number < 1 ? `${number.toFixed(2)} ms` : `${number.toFixed(number < 100 ? 1 : 0)} ms`;
}

function traceRowHtml(trace, compact = false) {
  const status = String(trace.status || 'running').replace(/[^a-zA-Z0-9_-]/g, '');
  return `<div class="exec-trace-row ${esc(status)}">
    <div class="exec-trace-main">
      <time>${fmtFullTime(trace.started_at)}</time>
      <strong>${esc(operationLabel(trace.operation))}</strong>
      <span class="mono">${esc(trace.target)}</span>
      ${compact ? '' : `<span class="small muted">${esc(trace.actor)}</span>`}
    </div>
    <div class="exec-trace-meta">
      <span class="badge badge-${status}">${esc(STATUS_LABELS[trace.status] || trace.status)}</span>
      <span>${esc(trace.step_count ?? trace.steps?.length ?? 0)} 阶段</span>
      <span>${durationLabel(trace.duration_ms)}</span>
      <button class="btn btn-ghost btn-sm" onclick="openTraceDrawer('${esc(trace.trace_id)}')">查看执行链</button>
    </div>
  </div>`;
}

function traceStepsHtml(trace) {
  const steps = trace.steps || [];
  const httpId = trace.input?.http_request_id;
  return `<div class="trace-tech-summary">
    <div class="detail-grid trace-tech-grid">
      <div class="detail-cell"><span>TRACE ID</span><strong class="mono">${esc(trace.trace_id)}</strong></div>
      <div class="detail-cell"><span>操作</span><strong>${esc(operationLabel(trace.operation))}</strong></div>
      <div class="detail-cell"><span>状态</span><strong>${esc(STATUS_LABELS[trace.status] || trace.status)}</strong></div>
      <div class="detail-cell"><span>总耗时</span><strong>${durationLabel(trace.duration_ms)}</strong></div>
      <div class="detail-cell"><span>操作人</span><strong>${esc(trace.actor)}</strong></div>
      <div class="detail-cell"><span>目标</span><strong class="mono">${esc(trace.target)}</strong></div>
      <div class="detail-cell"><span>HTTP 请求关联</span><strong class="mono">${esc(httpId || '—')}</strong></div>
      <div class="detail-cell"><span>开始时间</span><strong>${fmtFullTime(trace.started_at)}</strong></div>
    </div>
    ${trace.error ? `<div class="trace-error"><strong>失败原因</strong><pre>${esc(JSON.stringify(trace.error, null, 2))}</pre></div>` : ''}
    <div class="exec-step-list">
      ${steps.length ? steps.map((step) => `<div class="exec-step ${esc(step.status)}">
        <div class="exec-step-head">
          <span class="exec-step-seq">${String(step.seq).padStart(2, '0')}</span>
          <strong class="mono">${esc(step.phase)}</strong>
          <span class="badge badge-${esc(step.status)}">${esc(STATUS_LABELS[step.status] || step.status)}</span>
          <time>${durationLabel(step.duration_ms)}</time>
        </div>
        <details class="exec-step-detail"><summary>阶段参数</summary><pre>${esc(JSON.stringify(step.detail, null, 2))}</pre></details>
      </div>`).join('') : '<div class="empty-state">该追踪没有阶段记录</div>'}
    </div>
    ${trace.result ? `<div class="channel-card fast"><h4>执行结果摘要</h4><pre class="trace-json">${esc(JSON.stringify(trace.result, null, 2))}</pre></div>` : ''}
  </div>`;
}

async function openTraceDrawer(traceId) {
  openDrawer('<aside class="drawer trace-drawer"><div class="drawer-head"><div><h2>后端执行追踪</h2><p>正在读取 trace 与全部业务阶段…</p></div><button class="close-btn" onclick="closeDrawer()">×</button></div><div class="drawer-body"><div class="loading">加载中…</div></div></aside>');
  try {
    const trace = await api(`/api/traces/${encodeURIComponent(traceId)}`);
    openDrawer(`<aside class="drawer trace-drawer">
      <div class="drawer-head"><div><h2>后端执行追踪</h2><p>${esc(operationLabel(trace.operation))} · ${esc(trace.trace_id)}</p></div><button class="close-btn" onclick="closeDrawer()">×</button></div>
      <div class="drawer-body">${traceStepsHtml(trace)}</div>
    </aside>`);
  } catch (error) {
    closeDrawer();
    if (!error.business) toast('执行追踪加载失败', 'error');
  }
}

function toast(message, tone = 'info') {
  const root = $('#toast-root');
  if (!root) return;
  const node = document.createElement('div');
  node.className = `toast ${tone}`;
  node.textContent = message;
  root.appendChild(node);
  setTimeout(() => node.remove(), 3800);
}

function markApiStatus(online, path, status, durationMs) {
  state.apiOnline = online;
  state.apiRequestCount += 1;
  const pill = $('#api-status');
  const label = $('#api-status-text');
  if (pill) {
    pill.classList.remove('api-pending', 'api-online', 'api-offline');
    pill.classList.add(online ? 'api-online' : 'api-offline');
    pill.title = online
      ? `后端已响应 · ${state.apiRequestCount} 次请求 · 最近 ${durationMs}ms`
      : `后端连接失败 · ${path}`;
  }
  if (label) label.textContent = online ? `API 已连接 · ${state.apiRequestCount}` : 'API 异常';
  if (online) console.info(`[API] ${path} status=${status} duration=${durationMs}ms`);
  else console.error(`[API] ${path} status=${status} duration=${durationMs}ms`);
}

async function api(path, options = {}) {
  const started = performance.now();
  const requestOptions = {
    method: options.method || 'GET',
    headers: { ...(options.headers || {}) },
  };
  if (options.body instanceof FormData) {
    requestOptions.body = options.body;
  } else if (options.body !== undefined) {
    requestOptions.headers['Content-Type'] = 'application/json';
    requestOptions.body = JSON.stringify(options.body);
  }

  let response;
  try {
    response = await fetch(path, requestOptions);
  } catch (error) {
    markApiStatus(false, path, 'NETWORK_ERROR', Math.round(performance.now() - started));
    toast('网络请求失败，请确认服务已启动', 'error');
    const wrapped = new Error('NETWORK_ERROR');
    wrapped.business = true;
    throw wrapped;
  }

  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    const payload = await response.json();
    const duration = Math.round(performance.now() - started);
    markApiStatus(true, path, response.status, duration);
    if (payload.code !== 0) {
      toast(payload.message || '请求失败', 'error');
      const error = new Error(payload.message || 'BUSINESS_ERROR');
      error.business = true;
      error.code = payload.code;
      error.traceId = payload.trace_id || response.headers.get('x-trace-id') || null;
      throw error;
    }
    return payload.data;
  }
  markApiStatus(response.ok, path, response.status, Math.round(performance.now() - started));
  if (!response.ok) {
    toast(`请求失败 (${response.status})`, 'error');
    const error = new Error(`HTTP_${response.status}`);
    error.business = true;
    throw error;
  }
  return response;
}

function openModal(innerHtml) {
  const root = $('#modal-root');
  root.innerHTML = `<div class="modal-overlay" onclick="if(event.target===this) closeModal()">${innerHtml}</div>`;
  document.body.style.overflow = 'hidden';
}

function closeModal() {
  $('#modal-root').innerHTML = '';
  document.body.style.overflow = '';
}

function openDrawer(innerHtml) {
  const root = $('#drawer-root');
  root.innerHTML = `<div class="drawer-overlay" onclick="if(event.target===this) closeDrawer()">${innerHtml}</div>`;
  document.body.style.overflow = 'hidden';
}

function closeDrawer() {
  $('#drawer-root').innerHTML = '';
  if (!$('#modal-root').innerHTML) document.body.style.overflow = '';
}

function closeOverlays() {
  closeModal();
  closeDrawer();
  document.body.style.overflow = '';
}

function routeName() {
  const value = location.hash.replace(/^#\//, '') || 'overview';
  return ROUTES[value] ? value : 'overview';
}

function setHeader(route) {
  const meta = ROUTES[route];
  $('#page-title').textContent = meta.title;
  $('#page-subtitle').textContent = meta.subtitle;
  $('#breadcrumb-current').textContent = meta.title;
  document.title = `${meta.title} · 可信数据空间`;
  document.querySelectorAll('.main-nav a').forEach((link) => {
    link.classList.toggle('active', link.dataset.route === route);
  });
}

async function renderRoute() {
  const route = routeName();
  if (state.backendLogTimer) {
    clearInterval(state.backendLogTimer);
    state.backendLogTimer = null;
  }
  setHeader(route);
  const content = $('#page-content');
  content.innerHTML = '<div class="loading">正在加载安全数据…</div>';
  try {
    if (route === 'overview') await renderOverview();
    if (route === 'assets') await renderAssets();
    if (route === 'sandbox') await renderSandbox();
    if (route === 'components') await renderComponents();
    if (route === 'export') await renderExport();
    if (route === 'approval') await renderApproval();
    if (route === 'trace') await renderTrace();
    if (route === 'audit') await renderAudit();
  } catch (error) {
    if (!error.business) console.error(error);
    content.innerHTML = `<div class="empty-state"><div><strong>页面加载失败</strong><span>${esc(error.message || '请稍后重试')}</span></div></div>`;
  }
}

function metricIcons(kind) {
  const icons = {
    volume: '<svg viewBox="0 0 24 24" fill="none"><ellipse cx="12" cy="6" rx="7" ry="3" stroke="currentColor" stroke-width="1.7"/><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" stroke="currentColor" stroke-width="1.7"/></svg>',
    sandbox: '<svg viewBox="0 0 24 24" fill="none"><rect x="4" y="5" width="16" height="14" rx="3" stroke="currentColor" stroke-width="1.7"/><path d="m9 10 2 2-2 2M13 15h3" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    watermark: '<svg viewBox="0 0 24 24" fill="none"><path d="M6 4h9l4 4v12H6V4Z" stroke="currentColor" stroke-width="1.7"/><path d="m9 15 2-2 2 2 3-4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    policy: '<svg viewBox="0 0 24 24" fill="none"><path d="M12 3 19 6v5.5c0 4.2-2.7 7.4-7 9.5-4.3-2.1-7-5.3-7-9.5V6l7-3Z" stroke="currentColor" stroke-width="1.7"/><path d="m8.8 11.8 2.1 2.1 4.5-4.7" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };
  return icons[kind] || icons.policy;
}

async function renderOverview() {
  const data = await api('/api/overview');
  const metrics = data.metrics;
  const metricCards = [
    { label: '域可控', value: metrics.readonly_volumes, foot: '只读原始卷', icon: 'volume' },
    { label: '境可信', value: metrics.running_sandboxes, foot: '运行中沙箱', icon: 'sandbox' },
    { label: '源可溯', value: metrics.watermark_files, foot: '水印文件', icon: 'watermark' },
    { label: '权可分', value: metrics.field_policies, foot: '字段策略', icon: 'policy' },
  ];
  const recent = data.recent_exports.length
    ? data.recent_exports.map((item) => `
        <tr>
          <td class="mono">${fmtTime(item.created_at)}</td>
          <td class="strong">${esc(item.applicant)}</td>
          <td>${esc(item.filename)}</td>
          <td>${levelBadge(item.data_level)}</td>
          <td>${statusBadge(item.channel)}</td>
          <td>${statusBadge(item.status)}</td>
          <td class="mono">${esc(item.watermark_id || '—')}</td>
        </tr>`).join('')
    : '<tr><td colspan="7"><div class="empty-state" style="min-height:120px"><div><strong>暂无出域事件</strong><span>从可信沙箱生成文件后发起出域申请</span></div></div></td></tr>';
  const alerts = data.alerts.map((alert) => {
    const tone = ['warning', 'success', 'danger', 'info'].includes(alert.tone) ? alert.tone : 'info';
    return `<div class="alert-item ${tone}"><span class="alert-mark"></span><div><strong>${esc(alert.title)}</strong><p>${esc(alert.detail)}</p>${alert.time ? `<time>${fmtTime(alert.time)}</time>` : ''}</div></div>`;
  }).join('');
  const riskRows = data.risk_bars.map((bar) => `
    <div class="risk-row">
      <div class="risk-name"><span>${esc(bar.label.slice(0, 1))}</span>${esc(bar.label)}</div>
      <div class="risk-track" title="协同前风险 ${bar.before}% / 协同后风险 ${bar.after}%">
        <div class="risk-after" style="width:${Number(bar.score)}%"></div>
        <div class="risk-before-line" style="left:${Number(bar.before)}%"></div>
      </div>
      <div class="risk-values"><span class="before">${bar.before}%</span><span>→</span><span class="after">${bar.score}%</span></div>
    </div>`).join('');

  $('#page-content').innerHTML = `
    <section class="metrics-grid">
      ${metricCards.map((card) => `
        <article class="card metric-card">
          <div class="metric-top"><span class="metric-label">${card.label}</span><span class="metric-icon">${metricIcons(card.icon)}</span></div>
          <div class="metric-value">${card.value}</div>
          <div class="metric-foot"><strong>实时</strong><span>${card.foot}</span></div>
        </article>`).join('')}
    </section>

    <section class="grid-main">
      <article class="card">
        <div class="card-head"><div><h2>最近出域事件</h2><p>数据从可信空间流向外部接收方的实时记录</p></div><a class="card-link" href="#/export">查看全部 →</a></div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>时间</th><th>申请人</th><th>文件</th><th>分级</th><th>通道</th><th>状态</th><th>水印 ID</th></tr></thead>
            <tbody>${recent}</tbody>
          </table>
        </div>
      </article>
      <article class="card">
        <div class="card-head"><div><h2>实时告警</h2><p>策略阻断、待办与出口水印动态</p></div><span class="badge badge-neutral">LIVE</span></div>
        <div class="card-body"><div class="alert-list">${alerts}</div></div>
      </article>
    </section>

    <section class="card risk-card">
      <div class="card-head"><div><h2>四失风险收敛</h2><p>协同治理前后的风险暴露对比，红色刻度为治理前风险</p></div><span class="badge badge-success">安全基线上行</span></div>
      <div class="card-body">${riskRows}</div>
    </section>`;
}

function renderAssetTable(items) {
  if (!items.length) {
    return '<div class="empty-state"><div><strong>没有匹配的数据资产</strong><span>调整搜索词或分级筛选后重试</span></div></div>';
  }
  return `<div class="table-wrap"><table>
    <thead><tr><th>表名</th><th>Owner</th><th>分级</th><th>涉密</th><th>字段数</th><th>行数</th><th>共享状态</th><th>操作</th></tr></thead>
    <tbody>${items.map((item) => `
      <tr>
        <td class="strong mono">${esc(item.table_name)}</td>
        <td>${esc(item.owner)}</td>
        <td>${levelBadge(item.data_level)}</td>
        <td>${item.is_secret ? '<span class="badge badge-danger">涉密</span>' : '<span class="badge badge-neutral">否</span>'}</td>
        <td>${esc(item.field_count)}</td>
        <td>${Number(item.row_count).toLocaleString('zh-CN')}</td>
        <td>${esc(item.share_status)}</td>
        <td><button class="btn btn-secondary btn-sm" onclick="openAssetShare(${Number(item.id)})">申请共享</button></td>
      </tr>`).join('')}</tbody>
  </table></div>`;
}

async function renderAssets() {
  const data = await api('/api/data/assets');
  state.assets = data.items;
  $('#page-content').innerHTML = `
    <div class="page-actions">
      <div><h2>资产目录</h2><p>原始卷资产只读登记，共享必须落入沙箱工作卷并经过出域闸门</p></div>
      <span class="badge badge-info">${data.total} 项资产</span>
    </div>
    <div class="card mb-16">
      <div class="card-body" style="padding-top:18px">
        <div class="toolbar">
          <div class="search-box">
            <svg viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="6.5" stroke="currentColor" stroke-width="1.7"/><path d="m16 16 4 4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>
            <input id="asset-search" placeholder="搜索表名或 Owner" oninput="filterAssets()">
          </div>
          <select id="asset-level" onchange="filterAssets()">
            <option value="all">全部分级</option><option value="L1">L1 公开</option><option value="L2">L2 内部</option><option value="L3">L3 敏感</option><option value="L4">L4 核心</option>
          </select>
          <div class="spacer"></div>
          <span class="small muted">共 ${data.total} 条 · 数据实时来自 SQLite</span>
        </div>
      </div>
    </div>
    <section class="card" id="asset-table">${renderAssetTable(state.assets)}</section>`;
}

function filterAssets() {
  const query = ($('#asset-search')?.value || '').trim().toLowerCase();
  const level = $('#asset-level')?.value || 'all';
  const filtered = state.assets.filter((item) => {
    const matchesQuery = !query || item.table_name.toLowerCase().includes(query) || item.owner.toLowerCase().includes(query);
    const matchesLevel = level === 'all' || item.data_level === level;
    return matchesQuery && matchesLevel;
  });
  $('#asset-table').innerHTML = renderAssetTable(filtered);
}

async function openAssetShare(assetId) {
  const asset = state.assets.find((item) => item.id === assetId);
  if (!asset) return;
  const sandboxData = await api('/api/sandbox');
  const sandboxes = sandboxData.items;
  if (!sandboxes.length) {
    toast('请先创建可信沙箱', 'error');
    return;
  }
  const selected = state.currentSandboxId && sandboxes.some((item) => item.id === state.currentSandboxId)
    ? state.currentSandboxId
    : sandboxes[0].id;
  openModal(`<div class="modal wide">
    <div class="modal-head"><div><h2>申请共享资产</h2><p>系统会生成工作面副本，再进入分级出域流程</p></div><button class="close-btn" onclick="closeModal()">×</button></div>
    <div class="modal-body">
      <div class="review-list mb-16">
        <div class="review-row"><span>数据资产</span><strong class="mono">${esc(asset.table_name)}</strong></div>
        <div class="review-row"><span>Owner / 分级</span><strong>${esc(asset.owner)} · ${esc(asset.data_level)}</strong></div>
        <div class="review-row"><span>数据规模</span><strong>${Number(asset.field_count)} 字段 / ${Number(asset.row_count).toLocaleString('zh-CN')} 行</strong></div>
      </div>
      <div class="form-grid">
        <div class="field"><label>生成到沙箱</label><select id="asset-sandbox">${sandboxes.map((box) => `<option value="${esc(box.id)}" ${box.id === selected ? 'selected' : ''}>${esc(box.id)} · ${esc(STATUS_LABELS[box.status] || box.status)}</option>`).join('')}</select></div>
        <div class="field"><label>演示生成行数</label><input id="asset-rows" type="number" min="10" max="5000" value="${Math.min(5000, Math.max(10, Number(asset.row_count)))}"><span class="form-hint">为便于本地演示，最多生成 5000 行</span></div>
        <div class="field"><label>申请人</label><input id="asset-applicant" value="alice"></div>
        <div class="field"><label>接收方</label><input id="asset-receiver" value="合作方A"></div>
        <div class="field full"><label>使用用途</label><input id="asset-purpose" value="联合分析与建模验证"></div>
      </div>
      <div class="channel-card ${asset.data_level === 'L1' || asset.data_level === 'L2' ? 'fast' : 'approval'}">
        <h4>分级策略：${esc(asset.data_level)}</h4>
        <p>${asset.data_level === 'L1' || asset.data_level === 'L2' ? '命中快速通道，提交后可自动完成水印导出。' : '命中审批通道，需完成审批后才能嵌入水印并导出。'}</p>
      </div>
    </div>
    <div class="modal-foot"><button class="btn btn-ghost" onclick="closeModal()">取消</button><button class="btn btn-primary" onclick="submitAssetShare(${Number(asset.id)})">生成共享副本</button></div>
  </div>`);
}

async function submitAssetShare(assetId) {
  const asset = state.assets.find((item) => item.id === assetId);
  if (!asset) return;
  const sandboxId = $('#asset-sandbox').value;
  const rows = Number($('#asset-rows').value || 5000);
  const applicant = $('#asset-applicant').value.trim();
  const receiver = $('#asset-receiver').value.trim();
  const purpose = $('#asset-purpose').value.trim();
  if (!applicant || !receiver || !purpose) {
    toast('请补全申请人、接收方和用途', 'error');
    return;
  }
  try {
    const generated = await api('/api/data/generate', {
      method: 'POST',
      body: { table_name: asset.table_name, rows, sandbox_id: sandboxId },
    });
    closeModal();
    toast(`已生成 ${rows} 行共享副本`, 'success');
    await openExportModal({
      sandboxId,
      filePath: generated.path,
      level: asset.data_level,
      applicant,
      receiver,
      purpose,
      step: 2,
    });
  } catch (error) {
    if (!error.business) toast('生成共享副本失败', 'error');
  }
}

function selectedSandbox() {
  return state.sandboxes.find((item) => item.id === state.currentSandboxId) || state.sandboxes[0] || null;
}

function sandboxObservationHtml(observation) {
  if (!observation) {
    return `<section class="sandbox-facts observation-warning" id="sandbox-facts">
      <div class="observation-main"><span class="observation-pulse"></span><div><strong>运行事实核验失败</strong><p>无法联合读取数据库、运行时和卷目录</p></div></div>
      <button class="btn btn-secondary btn-sm" onclick="refreshSandboxFacts()">重试</button>
    </section>`;
  }
  const verdictClass = observation.verdict === 'RUNNING_VERIFIED' ? 'success'
    : observation.verdict === 'STALE_RUNNING' ? 'danger' : 'warning';
  const labels = {
    'database.record': '数据库档案',
    'database.running': 'DB RUNNING',
    'process.registry': '进程内注册',
    'runtime.live': '运行时存活',
    'storage.volumes': '挂载卷',
  };
  const checks = (observation.checks || []).map((check) => `
    <span class="observation-check ${check.ok ? 'pass' : 'fail'}" title="${esc(check.detail || '')}">
      <i>${check.ok ? '✓' : '×'}</i>${esc(labels[check.name] || check.name)}
    </span>`).join('');
  const runtime = observation.runtime || {};
  const storage = observation.storage || {};
  const database = observation.database || {};
  const last = observation.last_activity;
  const runtimeHint = runtime.long_lived_process
    ? `后端 ${esc(database.backend || runtime.mode || '-')} · 长驻运行时`
    : 'Mock 无长驻进程；RUNNING 表示已注册、卷可用、可按需启动 python3';
  return `<section class="sandbox-facts observation-${verdictClass}" id="sandbox-facts">
    <div class="observation-main">
      <span class="observation-pulse"></span>
      <div><strong>${esc(observation.verdict_label)}</strong><p>${runtimeHint}</p></div>
    </div>
    <div class="observation-checks">${checks}</div>
    <div class="observation-meta"><span>卷文件 <b>${Number(storage.raw_files || 0) + Number(storage.work_files || 0)}</b></span><span>最近活动 <b>${last ? esc(fmtTime(last.created_at)) : '暂无'}</b></span><span class="mono">${esc(storage.path || '-')}</span></div>
    <button class="btn btn-secondary btn-sm" onclick="refreshSandboxFacts()">刷新事实</button>
  </section>`;
}

async function refreshSandboxFacts() {
  rememberSandboxDraft();
  await renderSandbox();
}

async function renderSandbox() {
  const data = await api('/api/sandbox');
  state.sandboxes = data.items;
  if (!state.currentSandboxId || !state.sandboxes.some((item) => item.id === state.currentSandboxId)) {
    state.currentSandboxId = state.sandboxes[0]?.id || null;
  }
  const current = selectedSandbox();
  if (!current) {
    $('#page-content').innerHTML = `<div class="empty-state"><div><strong>尚无运行中的沙箱</strong><span>创建沙箱后即可编写 Python 并生成数据</span><br><button class="btn btn-primary mt-16" onclick="createSandbox()">新建沙箱</button></div></div>`;
    return;
  }
  const [mountsData, observation] = await Promise.all([
    api(`/api/sandbox/${encodeURIComponent(current.id)}/mounts`),
    api(`/api/sandbox/${encodeURIComponent(current.id)}/status`).catch(() => null),
  ]);
  const code = state.sandboxDrafts[current.id] ?? data.default_code;
  const result = state.sandboxResult?.sandboxId === current.id ? state.sandboxResult : null;
  const consoleText = result
    ? `${result.stdout || ''}${result.stderr ? `\n[stderr]\n${result.stderr}` : ''}\n\n[exit ${result.exit_code}] ${result.duration_ms} ms`
    : '等待执行…\n\n按「运行」在隔离工作卷中执行 Python 代码。';
  const mountPills = mountsData.mounts.map((mount) => `<span class="mount-pill"><b>${esc(mount.path)}</b><span class="${mount.read_only ? 'ro' : 'rw'}">${mount.read_only ? 'RO' : 'RW'}</span></span>`).join('');

  $('#page-content').innerHTML = `
    <div class="sandbox-toolbar">
      <div class="sandbox-identity">
        <select id="sandbox-select" onchange="changeSandbox(this.value)">
          ${state.sandboxes.map((box) => `<option value="${esc(box.id)}" ${box.id === current.id ? 'selected' : ''}>${esc(box.id)}</option>`).join('')}
        </select>
        ${statusBadge(current.status)}
        <a class="badge badge-neutral" href="#/components" title="查看运行时探测与自检">backend · ${esc(current.backend || 'mock')}</a>
        <span class="mono small muted">task: ${esc(current.task_id)}</span>
      </div>
      <div class="sandbox-mounts">${mountPills}</div>
      <button class="btn btn-primary" onclick="createSandbox()">＋ 新建沙箱</button>
    </div>

    ${sandboxObservationHtml(observation)}

    <section class="sandbox-grid">
      <article class="card panel">
        <div class="panel-title"><h3>挂载卷</h3><span>2 MOUNTS</span></div>
        <div class="volume-section">
          <div class="volume-head"><svg viewBox="0 0 24 24" fill="none"><path d="M4 7.5C4 6.7 4.7 6 5.5 6H10l2 2h6.5c.8 0 1.5.7 1.5 1.5v8c0 .8-.7 1.5-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5v-10Z" stroke="currentColor" stroke-width="1.6"/></svg>/mnt/raw<span class="mode ro">只读</span></div>
          <ul class="file-tree"><li>customer.csv</li><li>order.csv</li></ul>
          <div class="volume-divider"></div>
          <div class="volume-head"><svg viewBox="0 0 24 24" fill="none"><path d="M4 7.5C4 6.7 4.7 6 5.5 6H10l2 2h6.5c.8 0 1.5.7 1.5 1.5v8c0 .8-.7 1.5-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5v-10Z" stroke="currentColor" stroke-width="1.6"/></svg>/mnt/work<span class="mode rw">读写</span></div>
          <ul class="file-tree" id="work-tree"><li>读取中…</li></ul>
        </div>
      </article>

      <article class="card panel">
        <div class="panel-title"><h3>Python 工作台</h3><span>${esc(current.id)}</span></div>
        <div class="editor-wrap">
          <textarea id="code-editor" class="code-editor" spellcheck="false" oninput="rememberSandboxDraft()">${esc(code)}</textarea>
          <div class="code-toolbar">
            <button class="btn btn-primary btn-sm" onclick="runSandboxCode()">▶ 运行</button>
            <button class="btn btn-secondary btn-sm" onclick="saveSandboxCode()">保存</button>
            <button class="btn btn-ghost btn-sm" onclick="clearSandboxCode()">清空</button>
            <span class="spacer"></span><span style="color:#8199B9;font-size:10px">python3 · timeout 10s</span>
          </div>
        </div>
      </article>

      <article class="card panel">
        <div class="panel-title">
          <div class="tabs"><button class="tab active" id="tab-console" onclick="switchResultTab('console')">控制台输出</button><button class="tab" id="tab-files" onclick="switchResultTab('files')">文件列表 <span id="file-count">0</span></button></div>
          <span>RESULT</span>
        </div>
        <div class="result-pane" id="pane-console"><pre class="console" id="console-out">${esc(consoleText)}</pre></div>
        <div class="result-pane hidden" id="pane-files"><div class="result-files" id="file-cards"><div class="empty-state" style="min-height:200px">正在读取文件…</div></div></div>
      </article>
    </section>

    <section class="card log-timeline">
      <div class="card-head"><div><h2>运行证据链</h2><p>后端业务阶段来自 SQLite execution_traces，可展开查看文件物化、子进程、卷同步与水印参数</p></div><span class="badge badge-neutral">${esc(current.id)}</span></div>
      <div class="card-body">
        <div class="section-kicker">EXECUTION TRACE</div>
        <div class="exec-trace-list" id="sandbox-traces"><div class="empty-state" style="min-height:90px">加载执行追踪…</div></div>
        <div class="section-kicker mt-16">AUDIT EVENTS</div>
        <div class="log-list" id="sandbox-logs"><div class="empty-state" style="min-height:90px">加载日志…</div></div>
      </div>
    </section>`;

  await loadSandboxFiles();
  await Promise.all([loadSandboxLogs(), loadSandboxTraces()]);
}

function rememberSandboxDraft() {
  if (!state.currentSandboxId) return;
  state.sandboxDrafts[state.currentSandboxId] = $('#code-editor')?.value || '';
}

function renderSandboxFiles(files) {
  const workTree = $('#work-tree');
  const cards = $('#file-cards');
  const count = $('#file-count');
  if (count) count.textContent = String(files.length);
  if (workTree) {
    workTree.innerHTML = files.length
      ? files.map((file) => `<li>${esc(file.name)}</li>`).join('')
      : '<li>暂无工作文件</li>';
  }
  if (!cards) return;
  cards.innerHTML = files.length
    ? files.map((file) => `
      <div class="result-file">
        <div class="result-file-top"><span class="file-icon">CSV</span><div><strong>${esc(file.name)}</strong><small>${fileSize(file.size)} · ${esc(file.path)}</small></div></div>
        <div class="result-file-actions">
          <a class="btn btn-secondary btn-sm" href="${sandboxFileUrl(state.currentSandboxId, file.path)}">下载</a>
          <button class="btn btn-primary btn-sm" onclick="openExportForFileEncoded('${encodeURIComponent(file.path)}')">申请出域</button>
        </div>
      </div>`).join('')
    : '<div class="empty-state" style="min-height:200px"><div><strong>工作卷为空</strong><span>运行代码生成 CSV 后，这里会出现文件</span></div></div>';
}

async function loadSandboxFiles() {
  if (!state.currentSandboxId || routeName() !== 'sandbox') return;
  try {
    const data = await api(`/api/sandbox/${encodeURIComponent(state.currentSandboxId)}/files`);
    renderSandboxFiles(data.files);
  } catch (error) {
    if (!error.business) console.error(error);
  }
}

async function loadSandboxLogs() {
  if (!state.currentSandboxId || routeName() !== 'sandbox') return;
  try {
    const data = await api(`/api/audit/logs?target=${encodeURIComponent(state.currentSandboxId)}&limit=12`);
    const root = $('#sandbox-logs');
    if (!root) return;
    root.innerHTML = data.items.length
      ? data.items.map((log) => `<div class="log-item ${esc(log.result)}"><time>${fmtTime(log.created_at)}</time><div><strong>${esc(actionLabel(log.action))}</strong><p>${esc(log.detail)}</p></div><span class="badge badge-${esc(log.result)}">${esc(STATUS_LABELS[log.result] || log.result)}</span></div>`).join('')
      : '<div class="empty-state" style="min-height:90px">暂无操作日志</div>';
  } catch (error) {
    if (!error.business) console.error(error);
  }
}

async function loadSandboxTraces() {
  if (!state.currentSandboxId || routeName() !== 'sandbox') return;
  try {
    const data = await api(`/api/traces?target=${encodeURIComponent(state.currentSandboxId)}&limit=10`);
    const root = $('#sandbox-traces');
    if (!root) return;
    root.innerHTML = data.items.length
      ? data.items.map((trace) => traceRowHtml(trace, true)).join('')
      : '<div class="empty-state" style="min-height:90px">该沙箱尚无执行追踪</div>';
  } catch (error) {
    if (!error.business) console.error(error);
  }
}

async function changeSandbox(sandboxId) {
  rememberSandboxDraft();
  state.currentSandboxId = sandboxId;
  await renderSandbox();
}

async function createSandbox() {
  try {
    const sandbox = await api('/api/sandbox', { method: 'POST', body: { task_id: 'web-workbench' } });
    state.currentSandboxId = sandbox.id;
    state.sandboxResult = null;
    toast(`沙箱 ${sandbox.id} 已创建`, 'success');
    await renderSandbox();
  } catch (error) {
    if (!error.business) toast('沙箱创建失败', 'error');
  }
}

async function runSandboxCode() {
  rememberSandboxDraft();
  const code = $('#code-editor').value;
  const consoleNode = $('#console-out');
  if (consoleNode) consoleNode.textContent = '正在隔离环境中执行…';
  switchResultTab('console');
  try {
    const result = await api(`/api/sandbox/${encodeURIComponent(state.currentSandboxId)}/exec`, {
      method: 'POST',
      body: { code },
    });
    state.sandboxResult = {
      sandboxId: state.currentSandboxId,
      stdout: result.stdout,
      stderr: result.stderr,
      exit_code: result.exit_code,
      duration_ms: result.duration_ms,
      trace_id: result.trace_id,
    };
    if (consoleNode) {
      consoleNode.textContent = `${result.stdout || ''}${result.stderr ? `\n[stderr]\n${result.stderr}` : ''}\n\n[exit ${result.exit_code}] ${result.duration_ms} ms\n[trace] ${result.trace_id || '—'}`;
    }
    renderSandboxFiles(result.files);
    toast(result.exit_code === 0 ? `执行完成，用时 ${result.duration_ms} ms` : '代码执行结束，请检查 stderr', result.exit_code === 0 ? 'success' : 'error');
    await Promise.all([loadSandboxLogs(), loadSandboxTraces()]);
  } catch (error) {
    if (consoleNode && error.code === 1002) consoleNode.textContent = `BLOCKED: 原始卷 /mnt/raw 为只读卷，写操作已在执行前被拦截。\n[trace] ${error.traceId || '—'}`;
    else if (consoleNode) consoleNode.textContent = `执行失败：${error.message}`;
    await Promise.all([loadSandboxLogs(), loadSandboxTraces()]);
  }
}

function switchResultTab(tab) {
  const consoleTab = $('#tab-console');
  const filesTab = $('#tab-files');
  const consolePane = $('#pane-console');
  const filesPane = $('#pane-files');
  if (!consoleTab || !filesTab || !consolePane || !filesPane) return;
  consoleTab.classList.toggle('active', tab === 'console');
  filesTab.classList.toggle('active', tab === 'files');
  consolePane.classList.toggle('hidden', tab !== 'console');
  filesPane.classList.toggle('hidden', tab !== 'files');
}

async function saveSandboxCode() {
  rememberSandboxDraft();
  const path = prompt('保存到工作卷路径', '/mnt/work/main.py');
  if (!path) return;
  try {
    await api(`/api/sandbox/${encodeURIComponent(state.currentSandboxId)}/write`, {
      method: 'POST',
      body: { path, content: $('#code-editor').value },
    });
    toast(`已保存 ${path}`, 'success');
    await loadSandboxFiles();
    await Promise.all([loadSandboxLogs(), loadSandboxTraces()]);
  } catch (error) {
    if (!error.business) toast('保存失败', 'error');
  }
}

function clearSandboxCode() {
  if (!confirm('确认清空编辑器内容？')) return;
  $('#code-editor').value = '';
  rememberSandboxDraft();
}

function openExportForFileEncoded(encodedPath) {
  openExportModal({ sandboxId: state.currentSandboxId, filePath: decodeURIComponent(encodedPath) });
}

function exportActions(item) {
  if (item.status === 'approved') {
    return `<div class="table-actions"><button class="btn btn-primary btn-sm" onclick="executeRequest('${esc(item.id)}')">执行水印导出</button><button class="btn btn-ghost btn-sm" onclick="showRequest('${esc(item.id)}')">详情</button></div>`;
  }
  if (item.status === 'exported') {
    return `<div class="table-actions"><a class="btn btn-secondary btn-sm" href="/api/export/${encodeURIComponent(item.id)}/download">下载</a><button class="btn btn-ghost btn-sm" onclick="goTrace('${esc(item.watermark_id)}')">溯源</button></div>`;
  }
  return `<button class="btn btn-ghost btn-sm" onclick="showRequest('${esc(item.id)}')">查看详情</button>`;
}

async function renderExport() {
  const status = state.exportFilter;
  const data = await api(`/api/export/requests?status=${encodeURIComponent(status)}`);
  const tabs = [
    ['all', '全部'], ['pending', '待审批'], ['approved', '已通过'], ['exported', '已导出'], ['rejected', '已驳回'],
  ];
  const rows = data.items.length
    ? data.items.map((item) => `
      <tr>
        <td class="mono strong">${esc(item.id)}</td>
        <td>${esc(item.filename)}</td>
        <td>${levelBadge(item.data_level)}</td>
        <td>${statusBadge(item.channel)}</td>
        <td>${esc(item.applicant)}</td>
        <td>${esc(item.channel === 'fast' ? '快速通道' : approvalStageLabel(item.approval_stage))}</td>
        <td>${statusBadge(item.status)}</td>
        <td>${exportActions(item)}</td>
      </tr>`).join('')
    : '<tr><td colspan="8"><div class="empty-state" style="min-height:180px"><div><strong>当前筛选下没有申请</strong><span>从可信沙箱选择文件发起出域，或切换上方状态</span></div></div></td></tr>';
  $('#page-content').innerHTML = `
    <div class="page-actions">
      <div><h2>出域申请单</h2><p>分级决定通道：L1/L2 快速放行，L3/L4 必须完成审批</p></div>
      <button class="btn btn-primary" onclick="openExportModal()">＋ 新建出域申请</button>
    </div>
    <div class="toolbar mb-16">
      <div class="segmented">${tabs.map(([value, label]) => `<button class="${status === value ? 'active' : ''}" onclick="setExportFilter('${value}')">${label}</button>`).join('')}</div>
      <div class="spacer"></div><span class="small muted">共 ${data.total} 条申请</span>
    </div>
    <section class="card"><div class="table-wrap"><table>
      <thead><tr><th>申请单号</th><th>文件</th><th>分级</th><th>通道</th><th>申请人</th><th>当前节点</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div></section>`;
}

async function setExportFilter(value) {
  state.exportFilter = value;
  await renderExport();
}

async function openExportModal(prefill = {}) {
  try {
    const sandboxData = await api('/api/sandbox');
    const sandboxes = sandboxData.items;
    if (!sandboxes.length) {
      toast('没有可用沙箱，请先创建沙箱', 'error');
      return;
    }
    const sandboxId = prefill.sandboxId && sandboxes.some((item) => item.id === prefill.sandboxId)
      ? prefill.sandboxId
      : state.currentSandboxId || sandboxes[0].id;
    const fileData = await api(`/api/sandbox/${encodeURIComponent(sandboxId)}/files`);
    const files = [...fileData.files];
    if (prefill.filePath && !files.some((item) => item.path === prefill.filePath)) {
      files.unshift({ path: prefill.filePath, name: prefill.filePath.split('/').pop(), size: 0 });
    }
    const filePath = prefill.filePath || files[0]?.path || '';
    state.exportModal = {
      step: prefill.step || 1,
      sandboxId,
      sandboxes,
      files,
      filePath,
      level: prefill.level || 'L3',
      applicant: prefill.applicant || 'alice',
      purpose: prefill.purpose || '联合分析与模型验证',
      receiver: prefill.receiver || '合作方A',
      result: null,
    };
    renderExportModal();
  } catch (error) {
    if (!error.business) toast('无法加载可导出文件', 'error');
  }
}

function exportSteps() {
  const labels = ['选择文件', '申请信息', '分级判定', '提交完成'];
  const current = state.exportModal?.step || 1;
  return `<div class="stepper">${labels.map((label, index) => `<div class="step ${index + 1 === current ? 'active' : ''} ${index + 1 < current ? 'done' : ''}"><span>${index + 1 < current ? '✓' : index + 1}</span>${label}</div>`).join('')}</div>`;
}

function renderExportModal() {
  const modal = state.exportModal;
  if (!modal) return;
  let body = '';
  let footer = '';
  if (modal.step === 1) {
    const fileOptions = modal.files.length
      ? modal.files.map((file) => `<option value="${esc(file.path)}" ${file.path === modal.filePath ? 'selected' : ''}>${esc(file.name)} · ${fileSize(file.size)}</option>`).join('')
      : '<option value="">工作卷暂无文件</option>';
    body = `${exportSteps()}
      <div class="form-grid">
        <div class="field"><label>可信沙箱</label><select id="export-sandbox" onchange="changeExportSandbox(this.value)">${modal.sandboxes.map((box) => `<option value="${esc(box.id)}" ${box.id === modal.sandboxId ? 'selected' : ''}>${esc(box.id)} · ${esc(box.status)}</option>`).join('')}</select></div>
        <div class="field"><label>工作卷文件</label><select id="export-file">${fileOptions}</select></div>
      </div>
      <div class="channel-card"><h4>只能导出工作卷产物</h4><p>原始卷 /mnt/raw 为只读数据源，不能直接出域。请在沙箱中生成或加工文件后再申请。</p></div>`;
    footer = `<button class="btn btn-ghost" onclick="closeModal()">取消</button><button class="btn btn-primary" onclick="exportNext()" ${modal.files.length ? '' : 'disabled'}>下一步</button>`;
  } else if (modal.step === 2) {
    body = `${exportSteps()}
      <div class="form-grid">
        <div class="field"><label>申请人</label><input id="export-applicant" value="${esc(modal.applicant)}"></div>
        <div class="field"><label>接收方</label><input id="export-receiver" value="${esc(modal.receiver)}"></div>
        <div class="field full"><label>使用用途</label><input id="export-purpose" value="${esc(modal.purpose)}"></div>
        <div class="field"><label>数据分级</label><select id="export-level">${['L1', 'L2', 'L3', 'L4'].map((level) => `<option value="${level}" ${level === modal.level ? 'selected' : ''}>${level} ${level === 'L1' ? '公开' : level === 'L2' ? '内部' : level === 'L3' ? '敏感' : '核心'}</option>`).join('')}</select></div>
        <div class="field"><label>目标文件</label><input value="${esc(modal.filePath)}" disabled></div>
      </div>`;
    footer = `<button class="btn btn-ghost" onclick="exportBack()">上一步</button><button class="btn btn-primary" onclick="exportNext()">查看分级判定</button>`;
  } else if (modal.step === 3) {
    const fast = modal.level === 'L1' || modal.level === 'L2';
    body = `${exportSteps()}
      <div class="review-list">
        <div class="review-row"><span>目标文件</span><strong class="mono">${esc(modal.filePath)}</strong></div>
        <div class="review-row"><span>数据分级</span><strong>${levelBadge(modal.level)} ${modal.level === 'L1' ? '公开' : modal.level === 'L2' ? '内部' : modal.level === 'L3' ? '敏感' : '核心'}</strong></div>
        <div class="review-row"><span>申请人 / 接收方</span><strong>${esc(modal.applicant)} → ${esc(modal.receiver)}</strong></div>
        <div class="review-row"><span>使用用途</span><strong>${esc(modal.purpose)}</strong></div>
      </div>
      <div class="channel-card ${fast ? 'fast' : 'approval'}">
        <h4>${fast ? '命中快速通道' : '命中审批通道'}</h4>
        <p>${fast ? '该分级可免审批直接生成申请单。提交后将自动调用水印引擎并生成带 WM ID 的下载文件。' : '申请提交后进入 pending 状态，需要审批中心通过后才能执行水印导出。审批链：Owner → 安全 → 合规。'}</p>
      </div>`;
    footer = `<button class="btn btn-ghost" onclick="exportBack()">上一步</button><button class="btn btn-primary" onclick="submitExportRequest()">${fast ? '提交并直接导出' : '提交审批'}</button>`;
  } else {
    const result = modal.result;
    body = `${exportSteps()}
      <div class="success-card">
        <div class="success-mark">✓</div>
        <h3>${result?.watermark_id ? '水印导出完成' : '申请已提交'}</h3>
        <p>${result?.watermark_id ? '文件已通过统一出口并嵌入可追溯水印。' : '申请已进入审批队列，请前往审批中心处理。'}</p>
        <div class="success-meta">
          <div class="review-row"><span>申请单号</span><strong class="mono">${esc(result?.request_id || '')}</strong></div>
          ${result?.watermark_id ? `<div class="review-row"><span>水印 ID</span><strong class="mono link">${esc(result.watermark_id)}</strong></div><div class="review-row"><span>导出文件</span><strong class="mono">${esc(result.filename)}</strong></div>` : '<div class="review-row"><span>当前状态</span><strong>待审批</strong></div>'}
        </div>
      </div>`;
    footer = `<button class="btn btn-ghost" onclick="closeModal()">关闭</button>${result?.watermark_id ? `<a class="btn btn-secondary" href="${esc(result.download_url)}">下载文件</a><button class="btn btn-primary" onclick="closeModal(); goTrace('${esc(result.watermark_id)}')">查看溯源</button>` : `<a class="btn btn-primary" href="#/approval" onclick="closeModal()">前往审批中心</a>`}`;
  }
  openModal(`<div class="modal">
    <div class="modal-head"><div><h2>新建出域申请</h2><p>所有导出强制经过水印引擎，原始卷文件不可直接导出</p></div><button class="close-btn" onclick="closeModal()">×</button></div>
    <div class="modal-body">${body}</div>
    <div class="modal-foot">${footer}</div>
  </div>`);
}

function captureExportFields() {
  const modal = state.exportModal;
  if (!modal) return;
  if (modal.step === 1) {
    modal.filePath = $('#export-file')?.value || modal.filePath;
  }
  if (modal.step === 2) {
    modal.applicant = $('#export-applicant')?.value.trim() || '';
    modal.receiver = $('#export-receiver')?.value.trim() || '';
    modal.purpose = $('#export-purpose')?.value.trim() || '';
    modal.level = $('#export-level')?.value || modal.level;
  }
}

async function changeExportSandbox(sandboxId) {
  const modal = state.exportModal;
  if (!modal) return;
  modal.sandboxId = sandboxId;
  try {
    const data = await api(`/api/sandbox/${encodeURIComponent(sandboxId)}/files`);
    modal.files = data.files;
    modal.filePath = data.files[0]?.path || '';
    renderExportModal();
  } catch (error) {
    if (!error.business) toast('读取沙箱文件失败', 'error');
  }
}

function exportBack() {
  captureExportFields();
  if (state.exportModal.step > 1) state.exportModal.step -= 1;
  renderExportModal();
}

function exportNext() {
  captureExportFields();
  const modal = state.exportModal;
  if (modal.step === 1 && !modal.filePath) {
    toast('工作卷暂无文件，请先运行代码生成 CSV', 'error');
    return;
  }
  if (modal.step === 2 && (!modal.applicant || !modal.receiver || !modal.purpose)) {
    toast('请完整填写申请人、接收方和用途', 'error');
    return;
  }
  if (modal.step === 3) {
    submitExportRequest();
    return;
  }
  modal.step += 1;
  renderExportModal();
}

async function submitExportRequest() {
  const modal = state.exportModal;
  if (!modal) return;
  try {
    const request = await api('/api/export/request', {
      method: 'POST',
      body: {
        sandbox_id: modal.sandboxId,
        file_path: modal.filePath,
        applicant: modal.applicant,
        purpose: modal.purpose,
        receiver: modal.receiver,
        data_level: modal.level,
      },
    });
    state.currentRequestId = request.id;
    let result = {
      request_id: request.id,
      watermark_id: null,
      filename: null,
      download_url: null,
    };
    if (request.status === 'approved') {
      const exported = await api(`/api/export/${encodeURIComponent(request.id)}/execute`, {
        method: 'POST',
        body: { actor: modal.applicant },
      });
      result = exported;
      state.currentWatermarkId = exported.watermark_id;
      toast(`水印导出完成：${exported.watermark_id}`, 'success');
    } else {
      toast('申请已进入审批中心', 'success');
    }
    modal.result = result;
    modal.step = 4;
    renderExportModal();
  } catch (error) {
    if (!error.business) toast('提交申请失败', 'error');
  }
}

async function executeRequest(requestId) {
  try {
    const result = await api(`/api/export/${encodeURIComponent(requestId)}/execute`, {
      method: 'POST',
      body: { actor: 'alice' },
    });
    state.currentWatermarkId = result.watermark_id;
    toast(`导出完成：${result.watermark_id}`, 'success');
    await renderExport();
  } catch (error) {
    if (!error.business) toast('执行导出失败', 'error');
  }
}

async function showRequest(requestId) {
  try {
    const detail = await api(`/api/export/${encodeURIComponent(requestId)}`);
    const item = detail.request;
    const previewRows = detail.preview?.length
      ? detail.preview.map((row) => `<tr>${Object.values(row).map((value) => `<td>${esc(value)}</td>`).join('')}</tr>`).join('')
      : '<tr><td colspan="5"><div class="empty-state" style="min-height:80px">文件预览不可用</div></td></tr>';
    const previewHead = detail.preview?.length ? Object.keys(detail.preview[0]).map((key) => `<th>${esc(key)}</th>`).join('') : '<th>预览</th>';
    openModal(`<div class="modal wide">
      <div class="modal-head"><div><h2>申请详情</h2><p class="mono">${esc(item.id)}</p></div><button class="close-btn" onclick="closeModal()">×</button></div>
      <div class="modal-body">
        <div class="review-list">
          <div class="review-row"><span>申请状态</span><strong>${statusBadge(item.status)}</strong></div>
          <div class="review-row"><span>文件 / 分级</span><strong class="mono">${esc(item.file_path)} · ${esc(item.data_level)}</strong></div>
          <div class="review-row"><span>申请人 / 接收方</span><strong>${esc(item.applicant)} → ${esc(item.receiver)}</strong></div>
          <div class="review-row"><span>用途</span><strong>${esc(item.purpose)}</strong></div>
          <div class="review-row"><span>水印 ID</span><strong class="mono">${esc(item.watermark_id || '尚未生成')}</strong></div>
        </div>
        <div class="section-label">脱敏预览 · 前 5 行</div>
        <div class="mini-table table-wrap"><table><thead><tr>${previewHead}</tr></thead><tbody>${previewRows}</tbody></table></div>
      </div>
      <div class="modal-foot"><button class="btn btn-ghost" onclick="closeModal()">关闭</button>${item.status === 'approved' ? `<button class="btn btn-primary" onclick="closeModal(); executeRequest('${esc(item.id)}')">执行导出</button>` : ''}${item.status === 'exported' ? `<a class="btn btn-primary" href="/api/export/${encodeURIComponent(item.id)}/download">下载文件</a>` : ''}</div>
    </div>`);
  } catch (error) {
    if (!error.business) toast('读取详情失败', 'error');
  }
}

function approvalChainMarkup(level, active = 'Owner') {
  const nodes = ['Owner 审批', '安全审批', '合规审批'];
  return `<div class="chain">${nodes.map((node, index) => `<div class="chain-node ${index < active ? 'done' : index === active ? 'active' : ''}"><strong>${node}</strong><small>${index === 0 ? '数据 Owner 核验业务用途' : index === 1 ? '安全团队校验敏感字段与接收方' : '合规团队确认授权与留痕'}</small></div>`).join('')}</div>`;
}

function approvalStageLabel(stage) {
  const value = Number(stage || 0);
  return ['Owner 审批', '安全审批', '合规审批', '三级会签完成'][Math.min(3, Math.max(0, value))];
}

async function renderApproval() {
  const data = await api('/api/export/requests?status=pending');
  const items = data.items.filter((item) => item.data_level === 'L3' || item.data_level === 'L4');
  const rows = items.length
    ? items.map((item) => `
      <tr>
        <td class="mono strong">${esc(item.id)}</td>
        <td>${esc(item.applicant)}</td>
        <td>${levelBadge(item.data_level)}</td>
        <td><span style="color:${item.data_level === 'L4' ? '#BE123C' : '#B45309'}">${esc(item.risk)}</span></td>
        <td>${fmtTime(item.created_at)}</td>
        <td><button class="btn btn-primary btn-sm" onclick="openApprovalDrawer('${esc(item.id)}')">审批</button></td>
      </tr>`).join('')
    : '<tr><td colspan="6"><div class="empty-state" style="min-height:190px"><div><strong>审批队列已清空</strong><span>L3/L4 出域申请会实时进入这里</span></div></div></td></tr>';
  $('#page-content').innerHTML = `
    <div class="page-actions">
      <div><h2>敏感数据审批待办</h2><p>只展示 L3/L4 待审批申请，审批通过后可一键完成水印导出</p></div>
      <span class="badge badge-warning">${items.length} 项待办</span>
    </div>
    <section class="card"><div class="card-head"><div><h2>待办列表</h2><p>风险提示由分级与涉密字段自动生成</p></div><span class="badge badge-neutral">Owner → 安全 → 合规</span></div>
      <div class="table-wrap"><table>
        <thead><tr><th>申请单号</th><th>申请人</th><th>分级</th><th>风险提示</th><th>提交时间</th><th>操作</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
    </section>`;
}

async function openApprovalDrawer(requestId) {
  try {
    const detail = await api(`/api/export/${encodeURIComponent(requestId)}`);
    const request = detail.request;
    const previewRows = detail.preview?.length
      ? detail.preview.map((row) => `<tr>${Object.values(row).map((value) => `<td>${esc(value)}</td>`).join('')}</tr>`).join('')
      : '<tr><td colspan="5"><div class="empty-state" style="min-height:70px">预览不可用</div></td></tr>';
    const previewHead = detail.preview?.length ? Object.keys(detail.preview[0]).map((key) => `<th>${esc(key)}</th>`).join('') : '<th>预览</th>';
    openDrawer(`<aside class="drawer">
      <div class="drawer-head"><div><h2>审批敏感数据出域</h2><p class="mono">${esc(request.id)}</p></div><button class="close-btn" onclick="closeDrawer()">×</button></div>
      <div class="drawer-body">
        <div class="review-list">
          <div class="review-row"><span>申请人 / 接收方</span><strong>${esc(request.applicant)} → ${esc(request.receiver)}</strong></div>
          <div class="review-row"><span>数据分级</span><strong>${levelBadge(request.data_level)}</strong></div>
          <div class="review-row"><span>使用用途</span><strong>${esc(request.purpose)}</strong></div>
          <div class="review-row"><span>风险提示</span><strong style="color:#B45309">${esc(detail.risk)}</strong></div>
        </div>
        <div class="section-label">审批链</div>
        ${approvalChainMarkup(request.data_level, Number(request.approval_stage || 0))}
        <div class="section-label">脱敏前 5 行预览</div>
        <div class="mini-table table-wrap"><table><thead><tr>${previewHead}</tr></thead><tbody>${previewRows}</tbody></table></div>
        <div class="section-label">水印策略</div>
        <div class="channel-card"><h4>哈希槽 · 3 份冗余</h4><p>按 customer_id 哈希定位 amount LSB 槽位，写入水印 ID、导出人、时间、文件 ID 与申请单号。删行不超过 10% 仍可提取。</p></div>
        <div class="section-label">审批意见</div>
        <div class="field"><label>审批人</label><input id="approval-actor" value="security-owner"></div>
        <div class="field mt-16"><label>意见</label><textarea id="approval-comment" rows="3" placeholder="填写审批依据与限制条件">用途明确，接收方协议已核验，同意最小范围出域。</textarea></div>
      </div>
      <div class="drawer-foot"><button class="btn btn-ghost" onclick="handleApproval('${esc(request.id)}','transfer')">转交</button><button class="btn btn-danger" onclick="handleApproval('${esc(request.id)}','reject')">驳回</button><button class="btn btn-primary" onclick="handleApproval('${esc(request.id)}','approve')">${Number(request.approval_stage || 0) >= 2 ? '通过并加水印' : `通过 · 转${Number(request.approval_stage || 0) === 0 ? '安全' : '合规'}审批`}</button></div>
    </aside>`);
  } catch (error) {
    if (!error.business) toast('打开审批详情失败', 'error');
  }
}

async function handleApproval(requestId, decision) {
  const approver = $('#approval-actor')?.value.trim() || 'security-owner';
  const comment = $('#approval-comment')?.value.trim() || '';
  try {
    const approved = await api(`/api/export/${encodeURIComponent(requestId)}/approve`, {
      method: 'POST',
      body: { approver, decision, comment },
    });
    if (decision === 'approve') {
      if (approved.status !== 'approved') {
        closeDrawer();
        toast(`${approvalStageLabel(Number(approved.approval_stage || 0) - 1)}已通过，已流转至${approvalStageLabel(approved.approval_stage)}`, 'success');
        if (routeName() === 'approval') await renderApproval();
        return;
      }
      const exported = await api(`/api/export/${encodeURIComponent(requestId)}/execute`, {
        method: 'POST',
        body: { actor: approver },
      });
      state.currentWatermarkId = exported.watermark_id;
      closeDrawer();
      toast(`审批通过，水印文件 ${exported.watermark_id} 已生成`, 'success');
      openModal(`<div class="modal">
        <div class="modal-head"><div><h2>审批与水印导出完成</h2><p>出口文件已写入水印映射，可立即下载或发起溯源查询</p></div><button class="close-btn" onclick="closeModal()">×</button></div>
        <div class="modal-body"><div class="success-card"><div class="success-mark">✓</div><h3>${esc(exported.watermark_id)}</h3><p>${esc(exported.filename)}</p>
          <div class="success-meta"><div class="review-row"><span>申请单号</span><strong class="mono">${esc(requestId)}</strong></div><div class="review-row"><span>审批人</span><strong>${esc(approver)}</strong></div><div class="review-row"><span>水印匹配</span><strong>哈希槽 3× 冗余</strong></div></div>
        </div></div>
        <div class="modal-foot"><button class="btn btn-ghost" onclick="closeModal()">关闭</button><a class="btn btn-secondary" href="${esc(exported.download_url)}">下载文件</a><button class="btn btn-primary" onclick="closeModal(); goTrace('${esc(exported.watermark_id)}')">查看溯源</button></div>
      </div>`);
    } else {
      closeDrawer();
      toast(decision === 'reject' ? '申请已驳回' : '已转交下一审批人', 'success');
    }
    if (routeName() === 'approval') await renderApproval();
  } catch (error) {
    if (!error.business) toast('审批操作失败', 'error');
  }
}

async function renderTrace() {
  const data = await api('/api/watermark/list');
  const rows = data.items.length
    ? data.items.map((item) => `
      <tr>
        <td>${esc(item.filename)}</td>
        <td class="mono strong">${esc(item.watermark_id)}</td>
        <td>${esc(item.user_id)}</td>
        <td>${fmtTime(item.export_time)}</td>
        <td>${esc(item.receiver)}</td>
        <td>${statusBadge('exported')}</td>
        <td><div class="table-actions"><a class="btn btn-secondary btn-sm" href="/api/watermark/${encodeURIComponent(item.watermark_id)}/download">下载</a><button class="btn btn-ghost btn-sm" onclick="traceWatermark('${esc(item.watermark_id)}')">查看溯源</button></div></td>
      </tr>`).join('')
    : '<tr><td colspan="7"><div class="empty-state" style="min-height:150px"><div><strong>暂无已导出文件</strong><span>完成一次出域导出后，可在这里按水印 ID 或文件溯源</span></div></div></td></tr>';
  $('#page-content').innerHTML = `
    <div class="page-actions">
      <div><h2>出口文件与水印档案</h2><p>每个导出文件都拥有唯一水印 ID，可从内容反查到人与审批链</p></div>
      <span class="badge badge-info">${data.total} 个水印文件</span>
    </div>
    <section class="card mb-16">
      <div class="card-head"><div><h2>已导出文件</h2><p>水印映射与出域申请实时关联</p></div><a class="card-link" href="#/export">查看申请 →</a></div>
      <div class="table-wrap"><table><thead><tr><th>文件名</th><th>水印 ID</th><th>导出人</th><th>导出时间</th><th>接收方</th><th>状态</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div>
    </section>
    <section class="card">
      <div class="card-head"><div><h2>泄露文件溯源</h2><p>输入水印 ID 或上传疑似泄露 CSV，内存提取，不落盘</p></div><span class="badge badge-success">≤ 20MB · 仅 CSV</span></div>
      <div class="card-body">
        <div class="query-tabs"><button id="query-tab-id" class="active" onclick="switchTraceTab('id')">按水印 ID 查</button><button id="query-tab-file" onclick="switchTraceTab('file')">上传文件查</button></div>
        <div id="query-panel-id" class="query-panel">
          <div class="toolbar"><input id="trace-id-input" class="mono" style="min-width:310px" placeholder="WM-YYYYMMDD-XXXXXXXX" onkeydown="if(event.key==='Enter') queryTraceById()"><button class="btn btn-primary" onclick="queryTraceById()">查询溯源</button></div>
        </div>
        <div id="query-panel-file" class="query-panel hidden">
          <div class="upload-drop">
            <svg viewBox="0 0 24 24" fill="none"><path d="M12 15V4m0 0 4 4m-4-4L8 8M5 15v3.5c0 .8.7 1.5 1.5 1.5h11c.8 0 1.5-.7 1.5-1.5V15" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>
            <div style="flex:1"><strong style="display:block;font-size:12px">选择疑似泄露文件</strong><span class="small muted">服务端仅在内存中提取，不保存上传文件</span><input id="trace-file" type="file" accept=".csv" class="mt-16" onchange="uploadTraceFile(this.files[0])"></div>
          </div>
        </div>
        <div id="trace-result" class="mt-16"></div>
      </div>
    </section>`;
  if (state.pendingTraceId) {
    const pending = state.pendingTraceId;
    state.pendingTraceId = null;
    $('#trace-id-input').value = pending;
    await queryTraceById();
  }
}

function switchTraceTab(tab) {
  state.traceTab = tab;
  $('#query-tab-id').classList.toggle('active', tab === 'id');
  $('#query-tab-file').classList.toggle('active', tab === 'file');
  $('#query-panel-id').classList.toggle('hidden', tab !== 'id');
  $('#query-panel-file').classList.toggle('hidden', tab !== 'file');
}

function traceWatermark(watermarkId) {
  if (routeName() !== 'trace') {
    state.pendingTraceId = watermarkId;
    location.hash = '#/trace';
    return;
  }
  switchTraceTab('id');
  $('#trace-id-input').value = watermarkId;
  queryTraceById();
}

async function queryTraceById() {
  const watermarkId = $('#trace-id-input')?.value.trim();
  if (!watermarkId) {
    toast('请输入水印 ID', 'error');
    return;
  }
  try {
    const [data, evidence] = await Promise.all([
      api(`/api/trace/${encodeURIComponent(watermarkId)}`),
      api(`/api/watermark/${encodeURIComponent(watermarkId)}/evidence`).catch(() => null),
    ]);
    state.currentWatermarkId = watermarkId;
    renderTraceResult(data, evidence);
  } catch (error) {
    if (!error.business) toast('溯源查询失败', 'error');
  }
}

async function uploadTraceFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.csv')) {
    toast('仅支持 .csv 文件', 'error');
    return;
  }
  if (file.size > 20 * 1024 * 1024) {
    toast('文件不能超过 20MB', 'error');
    return;
  }
  const form = new FormData();
  form.append('file', file);
  const root = $('#trace-result');
  root.innerHTML = '<div class="loading">正在内存中提取水印…</div>';
  try {
    const data = await api('/api/trace/file', { method: 'POST', body: form });
    state.currentWatermarkId = data.trace.watermark_id;
    const evidence = await api(`/api/watermark/${encodeURIComponent(data.trace.watermark_id)}/evidence`).catch(() => null);
    renderTraceResult(data, evidence);
    toast(`识别成功，匹配度 ${Number(data.match_score).toFixed(2)}%`, 'success');
  } catch (error) {
    root.innerHTML = '<div class="empty-state"><div><strong>未识别到有效水印</strong><span>文件可能被大幅篡改，或并非本系统导出</span></div></div>';
  }
}

function watermarkEvidenceHtml(evidence) {
  if (!evidence) {
    return '<div class="card mb-16"><div class="card-body"><div class="empty-state" style="min-height:110px"><div><strong>水印证据档案读取失败</strong><span>溯源结果仍可用，但暂时无法展示逐行写入差异</span></div></div></div></div>';
  }
  if (!evidence.available) {
    return `<div class="card mb-16"><div class="card-head"><div><h2>水印写入证据</h2><p>算法如何落到文件内容</p></div><span class="badge badge-warning">历史记录</span></div><div class="card-body"><div class="channel-card approval"><h4>暂无逐行证据</h4><p>${esc(evidence.message || '该水印没有写入证据档案')}</p></div></div></div>`;
  }
  const algorithm = evidence.algorithm || {};
  const frame = evidence.frame || {};
  const diff = evidence.diff || {};
  const verification = evidence.verification || {};
  const samples = diff.changed_samples || [];
  const sampleRows = samples.map((item) => `
    <tr>
      <td class="mono">${esc(item.customer_id)}</td>
      <td class="mono muted">${esc(item.sha256_prefix)}</td>
      <td class="mono">${esc(item.slot)}</td>
      <td><span class="badge badge-neutral">副本 ${Number(item.replica) + 1}/3</span></td>
      <td class="mono">${esc(item.amount_before)}</td>
      <td class="mono strong" style="color:#0F766E">${esc(item.amount_after)}</td>
      <td><span class="bit-tag bit-${Number(item.lsb_after)}">${esc(item.lsb_before)} → ${esc(item.lsb_after)}</span></td>
      <td class="mono">${esc(item.delta)}</td>
    </tr>`).join('') || '<tr><td colspan="8"><div class="empty-state" style="min-height:70px">本次导出的 amount LSB 已全部匹配目标比特，因此没有数值发生改变</div></td></tr>';
  const match = Number(verification.match_score || 0).toFixed(2);
  return `
    <div class="card mb-16 watermark-evidence-card">
      <div class="card-head">
        <div><h2>水印写入证据</h2><p>不是可见印章，而是把水印帧写进每一行 <span class="mono">amount</span> 的整数最低位</p></div>
        <span class="badge badge-success">${esc(algorithm.version || 'hash-slot-v1')} · 回读通过</span>
      </div>
      <div class="card-body">
        <div class="watermark-explain">
          <div><span>承载字段</span><strong class="mono">amount</strong><small>整数最低位 LSB</small></div>
          <div class="watermark-arrow">→</div>
          <div><span>行定位</span><strong class="mono">SHA256(customer_id)</strong><small>前 8 位决定槽位</small></div>
          <div class="watermark-arrow">→</div>
          <div><span>冗余写入</span><strong class="mono">${esc(frame.repeat)} 份</strong><small>删行/乱序仍可投票恢复</small></div>
          <div class="watermark-arrow">→</div>
          <div><span>完整性</span><strong class="mono">CRC32</strong><small>缺失位枚举修复</small></div>
        </div>
        <div class="watermark-metrics">
          <div><span>总行数</span><strong>${Number(diff.rows_total || 0).toLocaleString()}</strong></div>
          <div><span>真实改动行</span><strong>${Number(diff.changed_rows || 0).toLocaleString()}</strong><small>${esc(diff.changed_ratio)}%</small></div>
          <div><span>未改动行</span><strong>${Number(diff.unchanged_rows || 0).toLocaleString()}</strong><small>LSB 原本已匹配</small></div>
          <div><span>amount 最大扰动</span><strong>${esc(diff.max_amount_delta)}</strong><small>只调整奇偶性</small></div>
          <div><span>水印帧</span><strong>${esc(frame.frame_bytes)} B</strong><small>${esc(frame.total_slots)} 个槽位</small></div>
          <div><span>回读匹配度</span><strong>${match}%</strong><small>${Number(verification.corrected_bits || 0)} 位 CRC 修复</small></div>
        </div>
        <div class="watermark-formula mono">${esc(algorithm.method)}<br>slot = int(SHA256(customer_id)[:8], 16) % ${esc(frame.total_slots)} · bit=0 → amount 为偶数 · bit=1 → amount 为奇数</div>
        <div class="watermark-note"><strong>为什么在水印文件里搜索不到 WM-ID？</strong> 因为水印 ID 不是明文列，而是压缩帧编码进 <span class="mono">amount</span> 奇偶位；提取后得到载荷，再与数据库中的水印映射交叉验证。</div>
        <div class="table-wrap watermark-sample-table">
          <table>
            <thead><tr><th>客户 ID</th><th>SHA256 前 8 位</th><th>槽位</th><th>冗余</th><th>amount 原值</th><th>amount 导出值</th><th>LSB</th><th>差值</th></tr></thead>
            <tbody>${sampleRows}</tbody>
          </table>
        </div>
        <div class="watermark-verify-row">
          <div class="channel-card fast"><h4>导出后重新提取</h4><p>水印 ID <span class="mono">${esc(verification.extracted_watermark_id)}</span> · 匹配 ${match}% · 未知位 ${esc(verification.unknown_bits)} · CRC32 校验 <strong>通过</strong></p></div>
          <details><summary class="component-json-summary">查看内嵌业务载荷</summary><pre class="trace-json">${esc(JSON.stringify(evidence.payload || {}, null, 2))}</pre></details>
        </div>
      </div>
    </div>`;
}

function renderTraceResult(payload, evidence = null) {
  const trace = payload.trace || payload;
  const match = Number(payload.match_score ?? trace.match_score ?? 100);
  const mapping = trace.mapping;
  const request = trace.request;
  const timeline = trace.timeline || [];
  const safeScore = Math.max(0, Math.min(100, match));
  const iconMap = { ingest: '湖', sandbox: '箱', generate: '生', request: '申', approval: '批', watermark: '印', export: '出' };
  const timelineHtml = timeline.map((node) => `<div class="trace-node done"><div class="trace-dot">${iconMap[node.key] || '✓'}</div><strong>${esc(node.title)}</strong><time>${fmtTime(node.time)}</time><p>${esc(node.detail)}</p></div>`).join('');
  const root = $('#trace-result');
  if (!root) return;
  root.innerHTML = `
    <div class="card trace-hero">
      <div><span class="badge badge-success">水印命中</span><h3 class="mt-16">${esc(trace.watermark_id)}</h3><p>导出人 <strong style="color:#0A1A30">${esc(mapping.export_user)}</strong> · ${esc(trace.filename)} · 接收方 ${esc(request.receiver)}</p></div>
      <div class="score-ring" style="background:conic-gradient(#0F766E 0 ${safeScore}%, #E6F1EF ${safeScore}% 100%)"><strong>${safeScore.toFixed(1)}%</strong></div>
    </div>
    ${watermarkEvidenceHtml(evidence)}
    <div class="card mb-16">
      <div class="card-head"><div><h2>全链路时间线</h2><p>从数据入湖到文件出域的七步证据链</p></div><span class="badge badge-neutral">MATCH ${safeScore.toFixed(1)}%</span></div>
      <div class="card-body"><div class="trace-timeline">${timelineHtml}</div></div>
    </div>
    <div class="card">
      <div class="card-head"><div><h2>溯源详情</h2><p>水印载荷与数据库映射交叉验证</p></div><a class="btn btn-secondary btn-sm" href="${esc(trace.download_url)}">下载原件</a></div>
      <div class="card-body">
        <div class="detail-grid">
          <div class="detail-cell"><span>水印 ID</span><strong class="mono">${esc(mapping.watermark_id)}</strong></div>
          <div class="detail-cell"><span>导出人</span><strong>${esc(mapping.export_user)}</strong></div>
          <div class="detail-cell"><span>导出时间</span><strong>${fmtFullTime(mapping.export_time)}</strong></div>
          <div class="detail-cell"><span>文件 ID</span><strong class="mono">${esc(mapping.file_id)}</strong></div>
          <div class="detail-cell"><span>数据范围</span><strong class="mono">${esc(mapping.data_scope)}</strong></div>
          <div class="detail-cell"><span>沙箱 ID</span><strong class="mono">${esc(mapping.sandbox_id)}</strong></div>
          <div class="detail-cell"><span>审批链</span><strong>${esc(mapping.approval_chain)}</strong></div>
          <div class="detail-cell"><span>申请单号</span><strong class="mono">${esc(request.id)}</strong></div>
        </div>
        ${payload.extracted ? `<div class="channel-card fast"><h4>文件内嵌载荷</h4><p class="mono">${esc(JSON.stringify(payload.extracted))}</p></div>` : ''}
      </div>
    </div>`;
}

async function renderAudit() {
  $('#page-content').innerHTML = `
    <div class="page-actions">
      <div><h2>安全审计轨迹</h2><p>所有操作从 SQLite 审计表实时读取，阻断记录以红色高亮</p></div>
      <span class="badge badge-success">不可变留痕</span>
    </div>
    <div class="two-col">
      <div>
        <section class="card filter-card">
          <div class="filter-row">
            <div class="field"><label>开始日期</label><input id="audit-start" type="date"></div>
            <div class="field"><label>结束日期</label><input id="audit-end" type="date"></div>
            <div class="field"><label>操作类型</label><select id="audit-action"><option value="all">全部操作</option>${Object.entries(ACTION_LABELS).map(([value, label]) => `<option value="${value}">${label}</option>`).join('')}</select></div>
            <div class="field"><label>结果</label><select id="audit-result"><option value="all">全部结果</option><option value="success">成功</option><option value="failed">失败</option><option value="blocked">已阻断</option></select></div>
            <button class="btn btn-primary" onclick="loadAuditLogs()">筛选</button><button class="btn btn-ghost" onclick="resetAuditFilters()">重置</button>
          </div>
        </section>
        <section class="card" id="audit-table"><div class="loading">正在加载审计日志…</div></section>
        <section class="card mt-16">
          <div class="card-head">
            <div><h2>后端执行追踪</h2><p>每次操作内部的真实阶段、耗时、参数与错误；点开即可核对算法和服务状态</p></div>
            <div class="trace-filter"><select id="audit-operation"><option value="">全部操作</option>${Object.entries(OPERATION_LABELS).map(([value, label]) => `<option value="${value}">${label}</option>`).join('')}</select><button class="btn btn-secondary btn-sm" onclick="loadAuditTraces()">刷新</button></div>
          </div>
          <div class="card-body"><div class="exec-trace-list" id="audit-traces"><div class="empty-state" style="min-height:110px">正在加载执行追踪…</div></div></div>
        </section>
        <section class="card mt-16">
          <div class="card-head">
            <div><h2>业务生命周期日志</h2><p>INFO 级事件：沙箱初始化/执行/销毁、水印嵌入与提取、审批和导出</p></div>
            <div class="trace-filter">
              <select id="backend-log-source" onchange="loadBackendLogs()" aria-label="日志类型">
                <option value="events">业务事件</option>
                <option value="http">HTTP 访问</option>
                <option value="trace">执行细节</option>
              </select>
              <button class="btn btn-secondary btn-sm" onclick="loadBackendLogs()">刷新</button>
            </div>
          </div>
          <div class="card-body"><pre class="console backend-log-output" id="backend-log-output">正在读取后端日志…</pre><div class="small muted mt-16" id="backend-log-path"></div></div>
        </section>
      </div>
      <aside class="stats-stack" id="audit-stats"></aside>
    </div>`;
  await Promise.all([loadAuditLogs(), loadAuditTraces(), loadBackendLogs()]);
  state.backendLogTimer = setInterval(() => {
    if (routeName() === 'audit') loadBackendLogs(true);
  }, 5000);
}

async function loadAuditTraces() {
  const root = $('#audit-traces');
  if (!root) return;
  const operation = $('#audit-operation')?.value || '';
  const params = new URLSearchParams({ limit: '20' });
  if (operation) params.set('operation', operation);
  try {
    const data = await api(`/api/traces?${params.toString()}`);
    root.innerHTML = data.items.length
      ? data.items.map((trace) => traceRowHtml(trace)).join('')
      : '<div class="empty-state" style="min-height:110px"><div><strong>没有匹配的执行追踪</strong><span>先在沙箱执行代码或提交出域申请</span></div></div>';
  } catch (error) {
    if (!error.business) root.textContent = `读取执行追踪失败：${error.message}`;
  }
}

async function loadBackendLogs(silent = false) {
  const output = $('#backend-log-output');
  if (!output) return;
  const source = $('#backend-log-source')?.value || 'events';
  try {
    const data = await api(`/api/system/logs?limit=80&source=${encodeURIComponent(source)}`);
    if (data.source !== source) throw new Error('后端仍是旧版本，请在终端重启 ./start.sh 后再刷新');
    output.textContent = data.items.length ? data.items.join('\n') : '暂无后端日志';
    output.scrollTop = output.scrollHeight;
    const pathNode = $('#backend-log-path');
    if (pathNode) pathNode.textContent = `${data.label} · ${data.path} · 最近更新 ${data.updated_at ? fmtFullTime(data.updated_at) : '—'} · 每 5 秒刷新`;
  } catch (error) {
    if (!silent) output.textContent = `读取日志失败：${error.message}`;
  }
}

async function loadAuditLogs() {
  const params = new URLSearchParams();
  const start = $('#audit-start')?.value;
  const end = $('#audit-end')?.value;
  const action = $('#audit-action')?.value;
  const result = $('#audit-result')?.value;
  if (start) params.set('start', start);
  if (end) params.set('end', end);
  if (action) params.set('action', action);
  if (result) params.set('result', result);
  params.set('limit', '300');
  try {
    const data = await api(`/api/audit/logs?${params.toString()}`);
    const rows = data.items.length
      ? data.items.map((log) => {
        const blocked = log.result === 'blocked' || ['raw_write_blocked', 'unauthorized_export'].includes(log.action);
        return `<tr class="${blocked ? 'audit-blocked' : ''}">
          <td class="mono">${fmtFullTime(log.created_at)}</td>
          <td>${esc(log.actor)}</td>
          <td class="strong">${esc(actionLabel(log.action))}</td>
          <td class="mono">${esc(log.target)}</td>
          <td>${statusBadge(log.result)}</td>
          <td class="mono">${esc(log.ip)}</td>
          <td style="max-width:320px;white-space:normal">${esc(log.detail)}</td>
        </tr>`;
      }).join('')
      : '<tr><td colspan="7"><div class="empty-state" style="min-height:180px"><div><strong>没有匹配的审计记录</strong><span>调整筛选条件或先执行一次沙箱操作</span></div></div></td></tr>';
    $('#audit-table').innerHTML = `<div class="card-head"><div><h2>日志明细</h2><p>共 ${data.total} 条记录</p></div><span class="badge badge-neutral">LIVE LOG</span></div><div class="table-wrap"><table><thead><tr><th>时间</th><th>操作人</th><th>操作类型</th><th>对象</th><th>结果</th><th>IP</th><th>详情</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    $('#audit-stats').innerHTML = `
      <div class="card stat-tile danger"><span>原始卷写尝试</span><strong>${data.stats.raw_write_attempts}</strong><p>命中 /mnt/raw 只读策略，执行前即被阻断</p></div>
      <div class="card stat-tile warning"><span>未授权导出尝试</span><strong>${data.stats.unauthorized_exports}</strong><p>未审批申请或原始卷文件直接导出</p></div>
      <div class="card stat-tile success"><span>审批超时</span><strong>${data.stats.approval_timeouts}</strong><p>待审批超过 24 小时的申请数量</p></div>
      <div class="card stat-tile"><span>当前筛选记录</span><strong>${data.total}</strong><p>审计日志按时间倒序展示，最多 300 条</p></div>`;
  } catch (error) {
    if (!error.business) toast('审计日志加载失败', 'error');
  }
}

function resetAuditFilters() {
  $('#audit-start').value = '';
  $('#audit-end').value = '';
  $('#audit-action').value = 'all';
  $('#audit-result').value = 'all';
  loadAuditLogs();
}

function goTrace(watermarkId) {
  state.pendingTraceId = watermarkId;
  if (routeName() === 'trace') {
    state.pendingTraceId = null;
    traceWatermark(watermarkId);
  } else {
    location.hash = '#/trace';
  }
}

function componentStatusBadge(status) {
  return `<span class="badge badge-${esc(status)}"><i class="status-dot"></i>${esc(STATUS_LABELS[status] || status)}</span>`;
}

function componentCheckHtml(check) {
  return `<div class="component-check ${check.ok ? 'ok' : 'fail'}">
    <span>${check.ok ? 'PASS' : 'FAIL'}</span>
    <strong class="mono">${esc(check.name)}</strong>
    <code>${esc(typeof check.detail === 'string' ? check.detail : JSON.stringify(check.detail))}</code>
  </div>`;
}

function componentVariantsHtml(item) {
  if (!item.runtime_variants?.length) return '';
  return `<div class="component-variants">
    ${item.runtime_variants.map((variant) => `<div class="component-variant ${variant.active ? 'active' : ''}">
      <div><strong class="mono">${esc(variant.backend)}</strong>${variant.active ? '<span class="badge badge-info">当前</span>' : ''}</div>
      <span class="badge badge-${esc(variant.status)}">${esc(STATUS_LABELS[variant.status] || variant.status)}</span>
      <p>${esc(variant.message || '')}</p>
      <code>${esc(variant.manifest?.kind || variant.kind || '')}</code>
    </div>`).join('')}
  </div>`;
}

async function renderComponents() {
  const data = await api('/api/components');
  state.components = data.items;
  const summary = data.summary;
  const cards = data.items.map((item) => {
    const capabilities = Object.keys(item.capabilities || {}).slice(0, 5);
    return `<article class="card component-card ${esc(item.status)}">
      <div class="component-card-head">
        <div><span class="component-category">${esc(item.category)}</span><h3>${esc(item.name)}</h3><p class="mono">${esc(item.id)}</p></div>
        ${componentStatusBadge(item.status)}
      </div>
      <p class="component-description">${esc(item.description)}</p>
      <div class="component-provider"><span>实现</span><strong>${esc(item.provider)}</strong><em>${esc(item.version)}</em></div>
      <p class="component-message">${esc(item.message)}</p>
      <div class="component-tags">${capabilities.map((key) => `<span>${esc(key)}</span>`).join('')}</div>
      <div class="component-deps"><span>依赖</span>${(item.dependencies || []).map((dep) => `<code>${esc(dep)}</code>`).join('') || '<em>无</em>'}</div>
      ${componentVariantsHtml(item)}
      <div class="component-card-foot">
        <button class="btn btn-ghost btn-sm" onclick="openComponentDrawer('${esc(item.id)}')">查看探测详情</button>
        ${item.self_test?.available ? `<button class="btn btn-secondary btn-sm" onclick="runComponentSelfTest('${esc(item.id)}', event)">运行闭环自检</button>` : '<span class="small muted">只读探测</span>'}
      </div>
    </article>`;
  }).join('');
  $('#page-content').innerHTML = `
    <div class="page-actions">
      <div><h2>可替换组件与运行证据</h2><p>状态来自当前进程、Docker/OpenSandbox 探测与 SQLite；点击自检会真实执行一条完整链路</p></div>
      <span class="badge badge-info">SANDBOX · ${esc(data.active_sandbox_backend.toUpperCase())}</span>
    </div>
    <div class="metrics-grid component-metrics">
      <div class="card metric-card"><div class="metric-top"><span class="metric-label">组件总数</span><span class="metric-icon">${metricIcons('policy')}</span></div><div class="metric-value">${summary.total}</div><div class="metric-foot">统一状态契约</div></div>
      <div class="card metric-card"><div class="metric-top"><span class="metric-label">就绪</span><span class="metric-icon">${metricIcons('policy')}</span></div><div class="metric-value">${summary.ready}</div><div class="metric-foot"><strong>READY</strong> 可承担真实请求</div></div>
      <div class="card metric-card"><div class="metric-top"><span class="metric-label">降级</span><span class="metric-icon">${metricIcons('sandbox')}</span></div><div class="metric-value">${summary.degraded}</div><div class="metric-foot">SDK 已装但未完成连通验证</div></div>
      <div class="card metric-card"><div class="metric-top"><span class="metric-label">不可用</span><span class="metric-icon">${metricIcons('watermark')}</span></div><div class="metric-value">${summary.unavailable}</div><div class="metric-foot">附探测项与修复命令</div></div>
    </div>
    <div class="component-grid">${cards}</div>`;
  $('#sidebar-runtime').textContent = `LOCAL · SANDBOX ${String(data.active_sandbox_backend).toUpperCase()}`;
}

async function openComponentDrawer(componentId) {
  openDrawer('<aside class="drawer component-drawer"><div class="drawer-head"><div><h2>组件探测</h2><p>正在读取实现、能力与依赖…</p></div><button class="close-btn" onclick="closeDrawer()">×</button></div><div class="drawer-body"><div class="loading">加载中…</div></div></aside>');
  try {
    const item = await api(`/api/components/${encodeURIComponent(componentId)}`);
    const last = item.last_self_test;
    openDrawer(`<aside class="drawer component-drawer">
      <div class="drawer-head"><div><h2>${esc(item.name)}</h2><p><span class="mono">${esc(item.id)}</span> · ${esc(item.provider)} · ${esc(item.version)}</p></div><button class="close-btn" onclick="closeDrawer()">×</button></div>
      <div class="drawer-body">
        <div class="detail-grid component-detail-grid">
          <div class="detail-cell"><span>状态</span><strong>${esc(STATUS_LABELS[item.status] || item.status)}</strong></div>
          <div class="detail-cell"><span>类别</span><strong>${esc(item.category)}</strong></div>
          <div class="detail-cell"><span>实现方</span><strong>${esc(item.provider)}</strong></div>
          <div class="detail-cell"><span>版本</span><strong class="mono">${esc(item.version)}</strong></div>
        </div>
        ${item.active_backend ? `<div class="channel-card fast mt-16"><h4>当前沙箱后端</h4><p class="mono">${esc(item.active_backend)} · ${esc(item.message)}</p></div>` : `<p class="component-message mt-16">${esc(item.message)}</p>`}
        <h4 class="component-section-title">探测项</h4>
        <div class="component-check-list">${(item.checks || []).map(componentCheckHtml).join('') || '<p class="muted">无</p>'}</div>
        ${componentVariantsHtml(item)}
        <h4 class="component-section-title">能力与配置</h4>
        <details open><summary class="component-json-summary">capabilities</summary><pre class="trace-json">${esc(JSON.stringify(item.capabilities || {}, null, 2))}</pre></details>
        <details class="mt-16"><summary class="component-json-summary">config</summary><pre class="trace-json">${esc(JSON.stringify(item.config || {}, null, 2))}</pre></details>
        <div class="component-deps drawer-deps"><span>依赖</span>${(item.dependencies || []).map((dep) => `<code>${esc(dep)}</code>`).join('') || '<em>无</em>'}</div>
        ${item.remediation ? `<div class="trace-error mt-16"><strong>修复建议</strong><pre>${esc(item.remediation)}</pre></div>` : ''}
        ${last ? `<div class="channel-card mt-16"><h4>最近一次自检</h4><p><span class="badge badge-${esc(last.status)}">${esc(STATUS_LABELS[last.status] || last.status)}</span> <span class="mono">${esc(last.trace_id)}</span></p><button class="btn btn-ghost btn-sm" onclick="openTraceDrawer('${esc(last.trace_id)}')">查看自检执行链</button></div>` : ''}
      </div>
      <div class="drawer-foot">${item.self_test?.available ? `<button class="btn btn-primary" onclick="runComponentSelfTest('${esc(item.id)}', event)">运行闭环自检</button>` : '<span class="small muted">该组件暂未提供主动自检</span>'}<button class="btn btn-ghost" onclick="closeDrawer()">关闭</button></div>
    </aside>`);
  } catch (error) {
    closeDrawer();
    if (!error.business) toast('组件详情加载失败', 'error');
  }
}

async function runComponentSelfTest(componentId, event) {
  const button = event?.currentTarget || null;
  if (button) button.disabled = true;
  try {
    toast(`正在执行 ${componentId} 闭环自检…`, 'info');
    const result = await api(`/api/components/${encodeURIComponent(componentId)}/self-test`, {
      method: 'POST',
      body: { actor: 'component-console' },
    });
    toast(`自检通过 · ${result.trace_id}`, 'success');
    if (result.trace_id) await openTraceDrawer(result.trace_id);
    if (routeName() === 'components') await renderComponents();
  } catch (error) {
    if (error.traceId) {
      toast(`自检失败，失败链路：${error.traceId}`, 'error');
      await openTraceDrawer(error.traceId);
    } else if (!error.business) {
      toast('自检执行失败', 'error');
    }
  } finally {
    if (button) button.disabled = false;
  }
}

window.addEventListener('hashchange', renderRoute);
if (!location.hash) history.replaceState(null, '', '#/overview');
renderRoute();

