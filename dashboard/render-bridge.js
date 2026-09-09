'use strict';

(() => {
  const byId = id => document.getElementById(id);
  const setText = (id, value) => {
    const el = byId(id);
    if (el) el.textContent = value;
  };
  const badge = (text, tone) => {
    const el = byId('renderBootBadge');
    if (!el) return;
    el.textContent = text;
    el.className = `badge ${tone}`;
  };
  const setMessage = text => setText('renderRuntimeMessage', text);
  let marketTradingSafe = null;
  let marketSource = 'UNKNOWN';
  let marketBasis = 'UNKNOWN';

  function applyMarketAuthority() {
    if (marketTradingSafe === false) {
      setText('renderMarketState', 'TESTNET YEDEK');
      badge('GİRİŞ ENGELLİ', 'warning');
      setMessage('Market data TESTNET fallback üzerinden geliyor. Dashboard ve mevcut pozisyon yönetimi çalışır; yeni girişler MARKET_DATA_NOT_TRADING_SAFE nedeniyle bloklanır.');
      return true;
    }
    if (marketSource === 'BINANCE_SPOT_PUBLIC_PROXY') {
      setText('renderMarketState', 'GERÇEK SPOT VEKİLİ');
      badge('İLERİ TEST', 'good');
      setMessage('Binance Futures public REST Render üzerinde kısıtlı. Strateji fiyat/mum verisi gerçek Binance Spot BTCUSDT proxy üzerinden geliyor; execution hâlâ yalnızca Futures TESTNET.');
      return true;
    }
    if (marketSource === 'PRODUCTION_FUTURES_PUBLIC') {
      setText('renderMarketState', 'FUTURES CANLI');
      badge('ÇEVRİMİÇİ', 'good');
      setMessage('Binance production Futures public market feed aktif. TESTNET otomatik execution canlı strateji verisini kullanabilir.');
      return true;
    }
    return false;
  }

  function statusFromConnection() {
    if (applyMarketAuthority()) return;
    const pill = byId('connectionPill');
    if (!pill) return;
    const text = String(pill.textContent || '').toUpperCase();
    if (text.includes('CANLI VERİ')) {
      setText('renderMarketState', 'CANLI');
      badge('ÇEVRİMİÇİ', 'good');
      setMessage('Render backend ve public market feed aktif. Dashboard canlı veriyi gösteriyor.');
      return;
    }
    if (text.includes('BAĞLANTI HATASI') || text.includes('ERROR')) {
      setText('renderMarketState', 'HATA');
      badge('KISITLI', 'bad');
      setMessage('Web arayüzü çalışıyor fakat canlı snapshot alınamadı. Render loglarında market bağlantı hatasını kontrol et.');
      return;
    }
    if (text.includes('KISITLI')) {
      setText('renderMarketState', 'KISITLI');
      badge('KISITLI', 'warning');
      setMessage('Arayüz çalışıyor; bazı dış veri kaynakları kullanılamıyor. Sistem sahte veri üretmeden eksik alanları UNAVAILABLE bırakıyor.');
    }
  }

  async function loadBootstrap() {
    try {
      const response = await fetch('/api/bootstrap', { cache: 'no-store' });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error('BOOTSTRAP_FAILED');
      marketTradingSafe = data.market_data_trading_safe !== false;
      marketSource = String(data.market_data_source || 'UNKNOWN');
      marketBasis = String(data.market_basis || 'UNKNOWN');
      setText('renderBackendState', 'ÇEVRİMİÇİ');
      setText('renderUiState', data.ui === 'READY' ? 'HAZIR' : safeText(data.ui));
      setText(
        'renderAccountState',
        data.orders_enabled
          ? 'TESTNET OTOMATİK'
          : data.binance_credentials_configured ? 'SALT OKUNUR' : 'YAPILANDIRILMADI'
      );
      if (applyMarketAuthority()) return;
      badge('ÇEVRİMİÇİ', 'good');
      const account = data.orders_enabled
        ? 'Testnet execution bayrakları doğrulandı; otomatik döngü aktif.'
        : data.binance_credentials_configured
        ? 'Testnet credentials bulundu; yürütme bayrakları kapalı olduğu için signed erişim salt okunur.'
        : 'Testnet credentials yok; public piyasa ekranı çalışır, hesap alanları UNAVAILABLE kalır.';
      setMessage(`Render sunucusu çevrimiçi. ${account} Piyasa temeli: ${marketBasis}.`);
    } catch (_) {
      setText('renderBackendState', 'ÇEVRİMDIŞI');
      setText('renderMarketState', 'BİLİNMİYOR');
      badge('SUNUCU HATASI', 'bad');
      setMessage('HTML yüklendi ancak /api/bootstrap erişilemiyor. Render servisinin Web Service olarak çalıştığını ve Start Command değerini kontrol et.');
    }
  }

  window.addEventListener('error', event => {
    if (!byId('renderRuntimePanel')) return;
    badge('ARAYÜZ HATASI', 'bad');
    setMessage(`Tarayıcı arayüz hatası: ${event.message || 'UNKNOWN_UI_ERROR'}`);
  });

  window.addEventListener('unhandledrejection', () => {
    if (!byId('renderRuntimePanel')) return;
    badge('API HATASI', 'bad');
    setMessage('Bir API isteği tamamlanamadı. Web arayüzü açık kalacak ve otomatik yeniden deneyecek.');
  });

  const loadScript = (src, onload) => {
    if (document.querySelector(`script[src="${src}"]`)) {
      if (onload) onload();
      return;
    }
    const script = document.createElement('script');
    script.src = src;
    script.defer = true;
    if (onload) script.addEventListener('load', onload, {once: true});
    document.head.appendChild(script);
  };

  // The compact Turkish V2 dashboard already owns its layout and active-position
  // panel. Loading the legacy board/tracker scripts on top of V2 re-parented every
  // section into a 12-column grid, squeezed Render status into one third of the
  // page and injected a duplicate active-trade panel. Keep those legacy helpers
  // only for older dashboard markup.
  const compactV2 = Boolean(document.querySelector('.console-v2'));
  if (compactV2) {
    const panel = byId('renderRuntimePanel');
    const flat = byId('flatStatePanel');
    const active = byId('activeTradePanel');
    const anchor = flat || active;
    if (panel && anchor && anchor.parentElement) {
      anchor.insertAdjacentElement('afterend', panel);
    }
  } else {
    loadScript('/dashboard-tabs.js', () => loadScript('/trade-tracker.js'));
  }

  loadBootstrap();
  setTimeout(statusFromConnection, 2500);
  setInterval(statusFromConnection, 3000);
})();
