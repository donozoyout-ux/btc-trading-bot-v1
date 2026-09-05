'use strict';

(() => {
  const ACCOUNT_POLL_MS = 3000;
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '—').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
  const num = value => {
    if (value === null || value === undefined || value === '') return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };
  const fmt = (value, digits = 2) => {
    const parsed = num(value);
    return parsed === null ? '—' : parsed.toLocaleString('en-US', {minimumFractionDigits: digits, maximumFractionDigits: digits});
  };
  const price = value => {
    const parsed = num(value);
    return parsed === null ? '—' : `$${parsed.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
  };
  const signed = (value, digits = 2) => {
    const parsed = num(value);
    if (parsed === null) return '—';
    return `${parsed > 0 ? '+' : ''}${fmt(parsed, digits)}`;
  };
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  function installStyle() {
    if ($('liveTradeTrackerStyle')) return;
    const style = document.createElement('style');
    style.id = 'liveTradeTrackerStyle';
    style.textContent = `
      .dashboard-board .zone-trade{grid-column:1/-1}
      .live-trade-tracker{padding:14px!important;border-radius:16px!important;position:relative;overflow:hidden}
      .live-trade-tracker::before{content:'';position:absolute;inset:0 auto 0 0;width:3px;background:linear-gradient(180deg,rgba(61,224,255,.85),rgba(156,108,255,.55));opacity:.9}
      .trade-track-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}
      .trade-track-title{display:flex;align-items:center;gap:10px;min-width:0}
      .trade-track-title h2{font-size:14px;margin:0;letter-spacing:.01em}
      .trade-track-title small{display:block;color:#6e8299;font-size:8px;margin-top:3px;letter-spacing:.06em}
      .trade-track-badges{display:flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:flex-end}
      .trade-track-badge{padding:6px 9px;border-radius:999px;border:1px solid rgba(255,255,255,.08);font-size:8px;font-weight:800;letter-spacing:.05em;color:#8fa6bd;background:rgba(255,255,255,.025)}
      .trade-track-badge.long{color:#7cf7c1;border-color:rgba(79,235,169,.25);background:rgba(79,235,169,.07)}
      .trade-track-badge.short{color:#ff9fae;border-color:rgba(255,97,122,.25);background:rgba(255,97,122,.07)}
      .trade-track-badge.good{color:#72f0bc;border-color:rgba(79,235,169,.22)}
      .trade-track-badge.warn{color:#ffd37c;border-color:rgba(255,192,79,.24)}
      .trade-track-grid{display:grid;grid-template-columns:repeat(10,minmax(0,1fr));gap:6px}
      .trade-track-stat{min-width:0;padding:9px 10px;border-radius:11px;border:1px solid rgba(255,255,255,.045);background:rgba(255,255,255,.018)}
      .trade-track-stat span{display:block;color:#61768d;font-size:7px;font-weight:800;letter-spacing:.08em;white-space:nowrap}
      .trade-track-stat strong{display:block;color:#dbe9f6;font-size:12px;margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      .trade-track-stat small{display:block;color:#596d83;font-size:7px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      .trade-track-stat.pnl.positive strong{color:#5aefae}.trade-track-stat.pnl.negative strong{color:#ff788d}
      .trade-track-stat.stop strong{color:#ff8f9e}.trade-track-stat.target strong{color:#6de7c0}
      .trade-track-progress-wrap{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:10px;align-items:center;margin-top:9px;padding:9px 10px;border:1px solid rgba(255,255,255,.045);border-radius:11px;background:rgba(3,9,16,.28)}
      .trade-track-progress{height:7px;border-radius:999px;background:rgba(255,255,255,.055);overflow:hidden;position:relative}
      .trade-track-progress>i{display:block;height:100%;width:0;border-radius:inherit;background:linear-gradient(90deg,rgba(255,105,126,.78),rgba(61,224,255,.9),rgba(82,235,172,.9));transition:width .35s ease}
      .trade-track-progress-wrap b{font-size:8px;color:#8fa5bb;white-space:nowrap}.trade-track-progress-wrap small{font-size:8px;color:#64798f;white-space:nowrap}
      .trade-track-flat{padding:16px;border-radius:11px;border:1px dashed rgba(255,255,255,.07);color:#6f8499;text-align:center;font-size:10px}
      .trade-track-flat b{color:#a7b8c8}
      .trade-track-error{color:#ff9aa8}
      @media(max-width:1550px){.trade-track-grid{grid-template-columns:repeat(5,minmax(0,1fr))}}
      @media(max-width:950px){.trade-track-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.trade-track-progress-wrap{grid-template-columns:1fr}.trade-track-head{align-items:flex-start}.trade-track-badges{justify-content:flex-start}}
    `;
    document.head.appendChild(style);
  }

  function buildTracker(board) {
    if ($('liveTradeTracker')) return $('liveTradeTracker');
    installStyle();
    const section = document.createElement('section');
    section.id = 'liveTradeTracker';
    section.className = 'glass panel live-trade-tracker zone-trade';
    section.innerHTML = `
      <div class="trade-track-head">
        <div class="trade-track-title">
          <div>
            <h2>Aktif İşlem Takibi</h2>
            <small>BINANCE FUTURES TESTNET · EXCHANGE SOURCE OF TRUTH · 3 SN REFRESH</small>
          </div>
        </div>
        <div class="trade-track-badges">
          <span id="tradeTrackSide" class="trade-track-badge">FLAT</span>
          <span id="tradeTrackProtection" class="trade-track-badge">KORUMA BEKLENİYOR</span>
          <span id="tradeTrackUpdated" class="trade-track-badge">YÜKLENİYOR</span>
        </div>
      </div>
      <div id="tradeTrackBody" class="trade-track-flat"><b>Binance Testnet pozisyonu kontrol ediliyor…</b></div>
    `;
    const hero = board.querySelector('.zone-hero');
    if (hero && hero.parentElement === board) hero.insertAdjacentElement('afterend', section);
    else board.prepend(section);
    return section;
  }

  function orderTrigger(order) {
    return num(order?.stop_price) ?? num(order?.price);
  }

  function protectiveOrders(orders, symbol) {
    const relevant = (orders || []).filter(order => String(order.symbol || '').toUpperCase() === symbol);
    const stop = relevant.find(order => {
      const type = String(order.type || '').toUpperCase();
      return type.includes('STOP') && !type.includes('TAKE_PROFIT');
    }) || null;
    const targets = relevant.filter(order => String(order.type || '').toUpperCase().includes('TAKE_PROFIT'));
    return {stop, targets, relevant};
  }

  function sortTargets(targets, entry) {
    return [...targets].sort((a, b) => {
      const av = orderTrigger(a), bv = orderTrigger(b);
      if (av === null) return 1;
      if (bv === null) return -1;
      return Math.abs(av - entry) - Math.abs(bv - entry);
    });
  }

  function distancePct(side, from, target, kind) {
    if (!from || !target) return null;
    if (kind === 'target') {
      return side === 'LONG' ? ((target - from) / from) * 100 : ((from - target) / from) * 100;
    }
    return side === 'LONG' ? ((from - target) / from) * 100 : ((target - from) / from) * 100;
  }

  function progressPct(side, mark, stop, tp1) {
    if ([mark, stop, tp1].some(value => value === null || value === undefined)) return null;
    const denominator = side === 'LONG' ? tp1 - stop : stop - tp1;
    if (!Number.isFinite(denominator) || denominator <= 0) return null;
    const numerator = side === 'LONG' ? mark - stop : stop - mark;
    return clamp((numerator / denominator) * 100, 0, 100);
  }

  function renderFlat(message = 'Açık BTCUSDT pozisyonu yok.') {
    $('tradeTrackSide').textContent = 'FLAT';
    $('tradeTrackSide').className = 'trade-track-badge';
    $('tradeTrackProtection').textContent = 'POZİSYON YOK';
    $('tradeTrackProtection').className = 'trade-track-badge';
    $('tradeTrackBody').className = 'trade-track-flat';
    $('tradeTrackBody').innerHTML = `<b>${esc(message)}</b><br><span>Bot yeni pozisyon açtığında giriş, canlı PnL, stop ve hedefler burada görünecek.</span>`;
  }

  function renderAccount(account) {
    const positions = Array.isArray(account.positions) ? account.positions : [];
    const position = positions.find(row => String(row.symbol || '').toUpperCase() === 'BTCUSDT') || positions[0];
    $('tradeTrackUpdated').textContent = new Date().toLocaleTimeString('tr-TR');

    if (!account.connected || String(account.status || '').toUpperCase() !== 'CONNECTED') {
      renderFlat(`Hesap verisi alınamadı · ${account.error_category || account.status || 'ACCOUNT_UNAVAILABLE'}`);
      $('tradeTrackBody').classList.add('trade-track-error');
      return;
    }
    if (!position) {
      renderFlat();
      return;
    }

    const side = String(position.side || (num(position.position_amount) >= 0 ? 'LONG' : 'SHORT')).toUpperCase();
    const entry = num(position.entry_price);
    const mark = num(position.mark_price);
    const pnl = num(position.unrealized_pnl);
    const size = num(position.size) ?? Math.abs(num(position.position_amount) || 0);
    const leverage = num(position.leverage);
    const liquidation = num(position.liquidation_price);
    const notional = Math.abs(num(position.notional) || 0);
    const movePct = entry && mark ? (side === 'LONG' ? ((mark - entry) / entry) : ((entry - mark) / entry)) * 100 : null;

    const protection = protectiveOrders(account.open_orders || [], String(position.symbol || 'BTCUSDT').toUpperCase());
    const stopPrice = orderTrigger(protection.stop);
    const targets = sortTargets(protection.targets, entry || mark || 0);
    const tp1 = orderTrigger(targets[0]);
    const tp2 = orderTrigger(targets[1]);
    const toTp1 = distancePct(side, mark, tp1, 'target');
    const toStop = distancePct(side, mark, stopPrice, 'stop');
    const progress = progressPct(side, mark, stopPrice, tp1);

    $('tradeTrackSide').textContent = `${side} · ${position.symbol || 'BTCUSDT'}`;
    $('tradeTrackSide').className = `trade-track-badge ${side === 'LONG' ? 'long' : 'short'}`;
    const protectionReady = Boolean(stopPrice && tp1);
    const fullProtection = Boolean(stopPrice && tp1 && tp2);
    $('tradeTrackProtection').textContent = fullProtection ? 'SL + TP1 + TP2 AKTİF' : protectionReady ? 'SL + TP AKTİF' : 'KORUMA EKSİK';
    $('tradeTrackProtection').className = `trade-track-badge ${protectionReady ? 'good' : 'warn'}`;

    const pnlTone = pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : '';
    $('tradeTrackBody').className = '';
    $('tradeTrackBody').innerHTML = `
      <div class="trade-track-grid">
        <div class="trade-track-stat"><span>GİRİŞ</span><strong>${price(entry)}</strong><small>Entry price</small></div>
        <div class="trade-track-stat"><span>MARK</span><strong>${price(mark)}</strong><small>Canlı Binance mark</small></div>
        <div class="trade-track-stat pnl ${pnlTone}"><span>CANLI PNL</span><strong>${signed(pnl)} USDT</strong><small>${movePct === null ? '—' : `${signed(movePct)}% fiyat hareketi`}</small></div>
        <div class="trade-track-stat"><span>MİKTAR</span><strong>${fmt(size, 6)} BTC</strong><small>${notional ? `${fmt(notional, 2)} USDT notional` : '—'}</small></div>
        <div class="trade-track-stat"><span>KALDIRAÇ</span><strong>${leverage === null ? '—' : `${fmt(leverage, 0)}×`}</strong><small>TESTNET</small></div>
        <div class="trade-track-stat stop"><span>STOP LOSS</span><strong>${price(stopPrice)}</strong><small>${toStop === null ? 'Koruma emri bulunamadı' : `${fmt(Math.max(0, toStop), 2)}% mesafe`}</small></div>
        <div class="trade-track-stat target"><span>TP1</span><strong>${price(tp1)}</strong><small>${toTp1 === null ? 'Hedef bulunamadı' : `${fmt(Math.max(0, toTp1), 2)}% kaldı`}</small></div>
        <div class="trade-track-stat target"><span>TP2</span><strong>${price(tp2)}</strong><small>${targets.length >= 2 ? 'İkinci hedef' : '—'}</small></div>
        <div class="trade-track-stat"><span>LİKİDASYON</span><strong>${price(liquidation)}</strong><small>${side}</small></div>
        <div class="trade-track-stat"><span>KORUMA EMRİ</span><strong>${protection.relevant.length}</strong><small>${fullProtection ? 'Tam koruma' : protectionReady ? 'Koruma aktif' : 'Kontrol gerekli'}</small></div>
      </div>
      <div class="trade-track-progress-wrap">
        <div class="trade-track-progress" title="Stop → TP1 ilerlemesi"><i style="width:${progress === null ? 0 : progress}%"></i></div>
        <b>${progress === null ? 'STOP / TP1 verisi bekleniyor' : `STOP → TP1 ${fmt(progress, 1)}%`}</b>
        <small>${toTp1 === null ? 'TP1 —' : `TP1'e ${fmt(Math.max(0, toTp1), 2)}%`} · ${toStop === null ? 'SL —' : `SL'e ${fmt(Math.max(0, toStop), 2)}%`}</small>
      </div>
    `;
  }

  async function loadAccount() {
    try {
      const response = await fetch('/api/account', {cache: 'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP_${response.status}`);
      renderAccount(data);
    } catch (error) {
      $('tradeTrackUpdated').textContent = 'API HATASI';
      renderFlat(`İşlem takibi verisi alınamadı · ${error?.message || 'ACCOUNT_UNAVAILABLE'}`);
      $('tradeTrackBody').classList.add('trade-track-error');
    }
  }

  function start() {
    const board = document.querySelector('.dashboard-board');
    if (!board) {
      setTimeout(start, 100);
      return;
    }
    buildTracker(board);
    loadAccount();
    setInterval(loadAccount, ACCOUNT_POLL_MS);
  }

  start();
})();
