'use strict';

(() => {
  const baseRenderIntelligence = renderIntelligence;

  const price = value => value == null || Number.isNaN(Number(value))
    ? '—'
    : `$${Number(value).toLocaleString('tr-TR', { maximumFractionDigits: 2 })}`;
  const num = (value, digits = 1) => value == null || Number.isNaN(Number(value))
    ? '—'
    : Number(value).toLocaleString('tr-TR', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const text = value => safeText(value);
  const pctDistance = (level, marketPrice) => {
    if (level == null || marketPrice == null || !Number(marketPrice)) return '—';
    return `${(((Number(level) - Number(marketPrice)) / Number(marketPrice)) * 100).toFixed(2)}%`;
  };
  const boolText = value => value === true ? 'EVET' : value === false ? 'HAYIR' : text(value);
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

  function structureTone(structure, trend) {
    const s = String(structure || '').toUpperCase();
    const t = String(trend || '').toUpperCase();
    if (s.includes('BULL') || t === 'UP') return 'bull';
    if (s.includes('BEAR') || t === 'DOWN') return 'bear';
    return 'neutral';
  }

  function rsiTone(value) {
    const v = Number(value);
    if (!Number.isFinite(v)) return ['—', 'neutral'];
    if (v >= 70) return ['AŞIRI ALIM', 'bear'];
    if (v <= 30) return ['AŞIRI SATIM', 'bull'];
    if (v >= 55) return ['BOĞA MOMENTUMU', 'bull'];
    if (v <= 45) return ['AYI MOMENTUMU', 'bear'];
    return ['DENGELİ', 'neutral'];
  }

  function adxTone(value) {
    const v = Number(value);
    if (!Number.isFinite(v)) return ['—', 'neutral'];
    if (v >= 30) return ['GÜÇLÜ', 'strong'];
    if (v >= 22) return ['TREND', 'info'];
    return ['ZAYIF / YATAY', 'neutral'];
  }

  function stateChip(label, value, activeClass = 'info') {
    const normalized = String(value ?? '').toUpperCase();
    const inactive = !value || ['NONE', 'FALSE', 'UNAVAILABLE', '—'].includes(normalized);
    return `<span class="ci-chip ${inactive ? 'muted' : activeClass}"><i></i>${esc(label)}: ${esc(boolText(value))}</span>`;
  }

  function renderTimeframeCard(tf, x, marketPrice) {
    const tone = structureTone(x.structure, x.trend);
    const [rsiLabel, rsiClass] = rsiTone(x.rsi);
    const [adxLabel, adxClass] = adxTone(x.adx);
    const rvol = Number(x.relative_volume);
    const rvolWidth = Number.isFinite(rvol) ? clamp((rvol / 2) * 100, 4, 100) : 0;
    const dmiDelta = Number.isFinite(Number(x.plus_di)) && Number.isFinite(Number(x.minus_di))
      ? Number(x.plus_di) - Number(x.minus_di)
      : null;
    const dmiTone = dmiDelta == null ? 'neutral' : dmiDelta > 0 ? 'bull' : dmiDelta < 0 ? 'bear' : 'neutral';
    const patterns = (x.patterns || []).length
      ? (x.patterns || []).map(p => `<span class="ci-pattern">${esc(text(p))}</span>`).join('')
      : '<span class="ci-pattern muted">ETKİN MUM FORMASYONU YOK</span>';
    const lastClosed = x.last_closed_at
      ? formatIstanbulChartTime(Number(x.last_closed_at))
      : '—';

    return `<article class="ci-tf-card ${tone}">
      <div class="ci-card-head">
        <div><span class="ci-tf">${esc(tf.toUpperCase())}</span><small>${esc(text(x.status || 'UNAVAILABLE'))}</small></div>
        <div class="ci-structure ${tone}">${esc(text(x.structure))}</div>
      </div>

      <div class="ci-trend-line">
        <span>Trend</span><b class="${tone}">${esc(text(x.trend))}</b>
        <span class="ci-bars">${Number(x.closed_candles || 0)} kapalı mum</span>
      </div>

      <div class="ci-level-grid">
        <div><span>En Yakın Destek</span><b>${price(x.nearest_support)}</b><small>${pctDistance(x.nearest_support, marketPrice)}</small></div>
        <div><span>En Yakın Direnç</span><b>${price(x.nearest_resistance)}</b><small>${pctDistance(x.nearest_resistance, marketPrice)}</small></div>
      </div>

      <div class="ci-momentum-grid">
        <div><span>RSI 14</span><b>${num(x.rsi, 1)}</b><small class="${rsiClass}">${rsiLabel}</small></div>
        <div><span>ADX 14</span><b>${num(x.adx, 1)}</b><small class="${adxClass}">${adxLabel}</small></div>
        <div><span>DMI + / −</span><b>${num(x.plus_di, 1)} / ${num(x.minus_di, 1)}</b><small class="${dmiTone}">${dmiDelta == null ? '—' : `${dmiDelta >= 0 ? '+' : ''}${dmiDelta.toFixed(1)} FARK`}</small></div>
      </div>

      <div class="ci-ema-block">
        <div class="ci-subhead"><span>EMA DİZİLİMİ</span><small>20 / 50 / 200</small></div>
        <div class="ci-ema-values"><span><i>20</i>${price(x.ema20)}</span><span><i>50</i>${price(x.ema50)}</span><span><i>200</i>${price(x.ema200)}</span></div>
      </div>

      <div class="ci-bb-block">
        <div class="ci-subhead"><span>BOLLINGER</span><small>Üst / Orta / Alt</small></div>
        <div class="ci-bb-values"><span>${price(x.bollinger?.upper)}</span><span>${price(x.bollinger?.mid)}</span><span>${price(x.bollinger?.lower)}</span></div>
      </div>

      <div class="ci-volume-block">
        <div class="ci-volume-copy"><span>Hacim · ${esc(text(x.volume_state))}</span><b>RVOL ${num(x.relative_volume, 2)}</b></div>
        <div class="ci-meter"><span style="width:${rvolWidth}%"></span></div>
        <div class="ci-volume-foot"><span>ATR ${num(x.atr, 2)}</span><span>EMA20 uzaklığı ${num(x.overextension_atr, 2)} ATR</span></div>
      </div>

      <div class="ci-event-chips">
        ${stateChip('BOS', x.bos, tone)}
        ${stateChip('CHoCH', x.choch, tone)}
        ${stateChip('Kırılım', x.breakout_state, tone)}
        ${stateChip('Yeniden test', x.retest_state, tone)}
        ${stateChip('Sahte kırılım', x.fake_breakout, 'warning')}
        ${stateChip('Aşırı uzama', x.overextended, 'warning')}
      </div>

      <div class="ci-patterns">${patterns}</div>
      <div class="ci-card-foot"><span>Son kapanış</span><b>${esc(lastClosed)}</b></div>
    </article>`;
  }

  function ensureMtfDashboard() {
    const state = $('mtfState');
    const box = state?.closest('.reason-box');
    if (!box || $('mtfDashboard')) return;
    box.classList.add('ci-mtf-box');
    box.insertAdjacentHTML('beforeend', `
      <div id="mtfDashboard" class="ci-mtf-dashboard">
        <div class="ci-mtf-metric"><span>Ağırlıklı Puan</span><b id="ciMtfScore">—</b><div class="ci-bias-track"><i></i><span id="ciMtfFill"></span></div><small>-10 KISA · +10 UZUN</small></div>
        <div class="ci-mtf-metric"><span>Tetik Durumu</span><b id="ciMtfTrigger">—</b><small>5D onay kapısı</small></div>
        <div class="ci-mtf-metric"><span>Çatışmalar</span><b id="ciMtfConflictCount">0</b><small>Zaman dilimi uyumsuzluğu</small></div>
        <div class="ci-mtf-metric"><span>İşlem Yetkisi</span><b id="ciMtfAuthority">HAYIR</b><small>Yalnızca yorum</small></div>
      </div>
      <div id="ciMtfConflictChips" class="ci-conflict-chips"></div>
    `);
  }

  function renderMtf(mtf) {
    ensureMtfDashboard();
    const score = Number(mtf.score);
    const safeScore = Number.isFinite(score) ? clamp(score, -10, 10) : 0;
    const scoreEl = $('ciMtfScore');
    if (scoreEl) {
      scoreEl.textContent = Number.isFinite(score) ? `${score > 0 ? '+' : ''}${score}` : '—';
      scoreEl.className = score > 0 ? 'bull' : score < 0 ? 'bear' : 'neutral';
    }
    const fill = $('ciMtfFill');
    if (fill) {
      const magnitude = Math.abs(safeScore) / 10 * 50;
      fill.style.width = `${magnitude}%`;
      fill.style.left = safeScore >= 0 ? '50%' : `${50 - magnitude}%`;
      fill.className = safeScore > 0 ? 'bull' : safeScore < 0 ? 'bear' : 'neutral';
    }
    if ($('ciMtfTrigger')) $('ciMtfTrigger').textContent = text(mtf.state);
    if ($('ciMtfConflictCount')) $('ciMtfConflictCount').textContent = String((mtf.conflicts || []).length);
    if ($('ciMtfAuthority')) $('ciMtfAuthority').textContent = mtf.execution_authority === true ? 'EVET' : 'HAYIR';
    const conflicts = $('ciMtfConflictChips');
    if (conflicts) {
      conflicts.innerHTML = (mtf.conflicts || []).length
        ? (mtf.conflicts || []).map(c => `<span>${esc(text(c))}</span>`).join('')
        : '<span class="ok">ZAMAN DİLİMLERİ UYUMLU · ÇATIŞMA YOK</span>';
    }
  }

  renderIntelligence = function enhancedRenderIntelligence(d) {
    baseRenderIntelligence(d);
    const chart = d.chart_intelligence || {};
    const mtf = d.mtf_interpretation || {};
    const marketPrice = d.market?.price;
    const container = $('tfIntelligence');
    if (container) {
      container.classList.add('ci-tf-grid');
      container.innerHTML = ['4h','1h','15m','5m']
        .map(tf => renderTimeframeCard(tf, chart.timeframes?.[tf] || {}, marketPrice))
        .join('');
    }
    renderMtf(mtf);
  };
})();
