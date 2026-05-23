/**
 * Дашборд /health — статус сервисов, графики, журнал.
 */

const STATUS_LABELS = {
  ok: 'OK',
  loading: 'Загрузка',
  degraded: 'Сбой',
  unavailable: 'Недоступен',
};

function formatUptime(sec) {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (h > 0) return `${h} ч ${m} мин`;
  if (m > 0) return `${m} мин ${s} с`;
  return `${s} с`;
}

function formatTime(ts) {
  return new Date(ts * 1000).toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function meterClass(status, loadPercent) {
  if (status === 'unavailable') return 'bad';
  if (status === 'loading') return 'load pulse';
  if (loadPercent != null && loadPercent > 85) return 'warn';
  if (status === 'ok') return 'ok';
  return 'load';
}

function drawChart(canvas, history) {
  if (!canvas || !history?.length) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  const pad = { l: 4, r: 4, t: 8, b: 18 };
  const plotW = w - pad.l - pad.r;
  const plotH = h - pad.t - pad.b;
  const n = history.length;
  const series = [
    { key: 'overall', color: '#c8d4e8' },
    { key: 'llm', color: '#5b9fd4' },
    { key: 'sd', color: '#3ecf8e' },
    { key: 'database', color: '#a78bfa' },
  ];

  ctx.strokeStyle = '#243044';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = pad.t + (plotH * i) / 4;
    ctx.beginPath();
    ctx.moveTo(pad.l, y);
    ctx.lineTo(pad.l + plotW, y);
    ctx.stroke();
  }

  series.forEach(({ key, color }) => {
    ctx.strokeStyle = color;
    ctx.lineWidth = key === 'overall' ? 2.2 : 1.5;
    ctx.beginPath();
    history.forEach((pt, i) => {
      const x = pad.l + (plotW * i) / Math.max(1, n - 1);
      const y = pad.t + plotH * (1 - (pt[key] ?? 0) / 100);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
}

function renderServices(grid, services) {
  grid.innerHTML = '';
  for (const s of services) {
    const card = document.createElement('article');
    card.className = `service-card status-${s.status}`;
    const load = s.load_percent ?? (s.status === 'ok' ? 25 : 0);
    const latLabel =
      s.latency_ms != null && s.latency_ms > 0
        ? `${s.latency_ms} ms`
        : s.status === 'ok'
          ? 'онлайн'
          : '—';
    const mClass = meterClass(s.status, load);
    card.innerHTML = `
      <div class="service-head">
        <h3>${escapeHtml(s.name)}</h3>
        <span class="service-status ${s.status}">${STATUS_LABELS[s.status] || s.status}</span>
      </div>
      <p class="service-detail">${escapeHtml(s.detail || '')}</p>
      <div class="meter-row">
        <div class="meter-label"><span>Нагрузка / отклик</span><span>${escapeHtml(latLabel)}</span></div>
        <div class="meter-track"><div class="meter-fill ${mClass}" style="width:${Math.min(100, Math.max(4, load))}%"></div></div>
      </div>
      ${s.url ? `<div class="service-url">${escapeHtml(s.url)}</div>` : ''}
    `;
    grid.appendChild(card);
  }
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

class HealthDashboard {
  constructor() {
    this.$ = {
      updated: document.getElementById('health-updated'),
      summaryBadge: document.getElementById('summary-badge'),
      summaryUptime: document.getElementById('summary-uptime'),
      summaryGens: document.getElementById('summary-gens'),
      summaryTimeouts: document.getElementById('summary-timeouts'),
      servicesGrid: document.getElementById('services-grid'),
      chart: document.getElementById('chart-overall'),
      logsView: document.getElementById('logs-view'),
      logsCount: document.getElementById('logs-count'),
      autoRefresh: document.getElementById('health-auto-refresh'),
      btnRefresh: document.getElementById('health-refresh'),
      btnLogsSave: document.getElementById('logs-save'),
      btnLogsRefresh: document.getElementById('logs-refresh'),
    };
    this._logText = '';
    this._timer = null;
    this._eventsSocket = null;
    this._eventsLive = false;
    this.$.btnRefresh?.addEventListener('click', () => this.refreshAll());
    this.$.btnLogsRefresh?.addEventListener('click', () => this.fetchLogs());
    this.$.btnLogsSave?.addEventListener('click', () => this.saveLogs());
    this.$.autoRefresh?.addEventListener('change', () => this._schedule());
    window.addEventListener('resize', () => {
      if (this._lastHistory) drawChart(this.$.chart, this._lastHistory);
    });
    this.refreshAll();
    this._schedule();
    this._connectSystemEvents();
  }

  _schedule() {
    if (this._timer) clearInterval(this._timer);
    if (this.$.autoRefresh?.checked) {
      const ms = this._eventsLive ? 30000 : 5000;
      this._timer = setInterval(() => this.refreshAll(), ms);
    }
  }

  _connectSystemEvents() {
    if (typeof SystemEventsSocket !== 'function') return;
    this._eventsSocket = new SystemEventsSocket({
      onOpen: () => {
        this._eventsLive = true;
        this._schedule();
      },
      onClose: () => {
        this._eventsLive = false;
        this._schedule();
      },
      onLogsAppend: (lines) => this._appendLogLines(lines),
    });
    this._eventsSocket.connect();
  }

  _appendLogLines(lines) {
    if (!lines?.length) return;
    const chunk = lines.join('\n');
    this._logText = this._logText ? `${this._logText}\n${chunk}` : chunk;
    this.$.logsView.textContent = this._logText;
    const n = (this._logText.match(/\n/g) || []).length + (this._logText ? 1 : 0);
    this.$.logsCount.textContent = `${n} строк`;
    this.$.logsView.scrollTop = this.$.logsView.scrollHeight;
  }

  async refreshAll() {
    await this.fetchHealth();
    if (!this._eventsLive) {
      await this.fetchLogs();
    }
  }

  async fetchHealth() {
    try {
      const res = await fetch('/api/health');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      this.renderHealth(data);
    } catch (err) {
      this.$.updated.textContent = `Ошибка: ${err.message}`;
      this.$.summaryBadge.textContent = 'Ошибка';
      this.$.summaryBadge.className = 'summary-badge degraded';
    }
  }

  renderHealth(data) {
    this._lastHistory = data.history || [];
    this.$.updated.textContent = `Обновлено: ${formatTime(data.generated_at)}`;
    this.$.summaryBadge.textContent = data.status === 'ok' ? 'Всё в порядке' : 'Есть проблемы';
    this.$.summaryBadge.className = `summary-badge ${data.status}`;
    this.$.summaryUptime.textContent = `Аптайм: ${formatUptime(data.uptime_sec || 0)}`;
    this.$.summaryGens.textContent = `Генераций: ${data.active_generations ?? 0}`;
    this.$.summaryTimeouts.textContent = data.timeouts_ok
      ? 'Таймауты: OK'
      : 'Таймауты: проверьте MCP/request';
    if (data.llm_model_configured) {
      this.$.summaryGens.textContent += ` · LLM: ${data.llm_model_configured}`;
    }
    renderServices(this.$.servicesGrid, data.services || []);
    drawChart(this.$.chart, this._lastHistory);
  }

  async fetchLogs() {
    try {
      const res = await fetch('/api/health/logs?limit=800');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      this._logText = (data.lines || []).join('\n');
      this.$.logsView.textContent = this._logText || '(журнал пуст)';
      this.$.logsCount.textContent = `${data.line_count ?? 0} строк`;
    } catch (err) {
      this.$.logsView.textContent = `Ошибка загрузки лога: ${err.message}`;
    }
  }

  saveLogs() {
    const text = this._logText || this.$.logsView.textContent || '';
    const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `web-chat-health-${stamp}.log`;
    a.click();
    URL.revokeObjectURL(a.href);
  }
}

document.addEventListener('DOMContentLoaded', () => new HealthDashboard());
