'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const safe = value => value == null ? '—' : String(value);
  const num = (value, digits=2) => {
    const n = Number(value);
    return Number.isFinite(n) ? n.toLocaleString('tr-TR',{minimumFractionDigits:digits,maximumFractionDigits:digits}) : '—';
  };

  function setBadge(status) {
    const badge = $('liveReadinessBadge');
    if (!badge) return;
    const labels = {READY:'READY',ALMOST_READY:'ALMOST READY',NOT_READY:'NOT READY'};
    badge.textContent = labels[status] || safe(status);
    badge.className = 'badge ' + (status === 'READY' ? 'good' : status === 'ALMOST_READY' ? 'warning' : 'bad');
  }

  function render(payload) {
    setBadge(payload.status);
    const perf = payload.performance || {};
    $('readinessScore').textContent = `${safe(payload.passed)} / ${safe(payload.total)} PASS`;
    $('readinessTrades').textContent = `${safe(perf.total_trades)} / 50`;
    $('readinessDays').textContent = `${num(perf.observation_days,1)} / 30 gün`;
    $('readinessPnl').textContent = Number.isFinite(Number(perf.net_pnl_usdt)) ? `${Number(perf.net_pnl_usdt) >= 0 ? '+' : ''}${num(perf.net_pnl_usdt)} USDT` : '—';
    $('readinessPf').textContent = num(perf.profit_factor);
    $('readinessDd').textContent = `${num(perf.max_drawdown_pct)}%`;
    $('readinessWin').textContent = `${num(perf.win_rate_pct)}%`;

    const criteria = $('readinessCriteria');
    criteria.replaceChildren();
    (payload.criteria || []).forEach(row => {
      const card = document.createElement('div');
      const label = document.createElement('span');
      const value = document.createElement('strong');
      const state = document.createElement('small');
      label.textContent = safe(row.label);
      value.textContent = safe(row.value);
      state.textContent = row.passed ? 'PASS' : 'FAIL';
      state.className = row.passed ? 'positive' : 'negative';
      card.append(label, value, state);
      criteria.appendChild(card);
    });

    const hard = payload.hard_failures || [];
    const warnings = payload.warnings || [];
    const dataError = payload.data_error;
    const message = $('readinessMessage');
    if (dataError) {
      message.textContent = `TESTNET işlem geçmişi alınamıyor: ${safe(dataError)}. Readiness istatistikleri güvenilir sayılmıyor.`;
    } else if (payload.status === 'READY') {
      message.textContent = 'Tüm canlıya geçiş kriterleri geçti. Bu panel yalnızca hazırlık göstergesidir; production execution otomatik olarak açılmaz.';
    } else if (hard.length) {
      message.textContent = `Canlıya geçiş engelli: ${hard.join(', ')}. Performans toplama TESTNET'te devam ediyor.`;
    } else {
      message.textContent = 'Kritik altyapı kriterleri geçti; istatistiksel örneklem tamamlanana kadar TESTNET takibi devam ediyor.';
    }
    $('readinessScope').textContent = `Kaynak: ${safe(payload.source)} · Fills: ${safe(payload.fill_records_observed)} · Journal: ${safe(payload.journal_scope)}${dataError ? ' · Data error: ' + safe(dataError) : ''}${warnings.length ? ' · Uyarı: ' + warnings.join(', ') : ''}`;
  }

  async function refresh() {
    try {
      const response = await fetch('/api/live-readiness', {cache:'no-store'});
      if (!response.ok) throw new Error('READINESS_UNAVAILABLE');
      render(await response.json());
    } catch (_) {
      setBadge('NOT_READY');
      if ($('readinessMessage')) $('readinessMessage').textContent = 'Canlıya hazırlık verisi şu anda alınamıyor.';
    }
  }

  window.addEventListener('DOMContentLoaded', refresh);
  setInterval(refresh, 30000);
})();
