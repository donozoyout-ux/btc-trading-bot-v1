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

  function statusFromConnection() {
    const pill = byId('connectionPill');
    if (!pill) return;
    const text = String(pill.textContent || '').toUpperCase();
    if (text.includes('LIVE DATA')) {
      setText('renderMarketState', 'LIVE');
      badge('ONLINE', 'good');
      setMessage('Render backend ve Binance public market feed aktif. Dashboard canlı veriyi gösteriyor.');
      return;
    }
    if (text.includes('BAĞLANTI HATASI') || text.includes('ERROR')) {
      setText('renderMarketState', 'ERROR');
      badge('DEGRADED', 'bad');
      setMessage('Web arayüzü çalışıyor fakat canlı snapshot alınamadı. Render loglarında DASHBOARD_UNAVAILABLE / Binance bağlantı hatasını kontrol et.');
      return;
    }
    if (text.includes('DEGRADED')) {
      setText('renderMarketState', 'DEGRADED');
      badge('DEGRADED', 'warning');
      setMessage('Arayüz çalışıyor; bazı dış veri kaynakları kullanılamıyor. Sistem sahte veri üretmeden eksik alanları UNAVAILABLE bırakıyor.');
    }
  }

  async function loadBootstrap() {
    try {
      const response = await fetch('/api/bootstrap', { cache: 'no-store' });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error('BOOTSTRAP_FAILED');
      setText('renderBackendState', 'ONLINE');
      setText('renderUiState', data.ui || 'READY');
      setText(
        'renderAccountState',
        data.orders_enabled
          ? 'TESTNET AUTO'
          : data.binance_credentials_configured ? 'READ ONLY' : 'NOT CONFIGURED'
      );
      badge('ONLINE', 'good');
      const account = data.orders_enabled
        ? 'Testnet execution bayrakları doğrulandı; otomatik döngü aktif.'
        : data.binance_credentials_configured
        ? 'Testnet credentials bulundu; yürütme bayrakları kapalı olduğu için signed erişim salt okunur.'
        : 'Testnet credentials yok; public piyasa ekranı çalışır, hesap alanları UNAVAILABLE kalır.';
      setMessage(`Render backend online. ${account}`);
    } catch (_) {
      setText('renderBackendState', 'OFFLINE');
      setText('renderMarketState', 'UNKNOWN');
      badge('BACKEND ERROR', 'bad');
      setMessage('HTML yüklendi ancak /api/bootstrap erişilemiyor. Render servisinin Web Service olarak çalıştığını ve Start Command değerini kontrol et.');
    }
  }

  window.addEventListener('error', event => {
    if (!byId('renderRuntimePanel')) return;
    badge('UI ERROR', 'bad');
    setMessage(`Tarayıcı arayüz hatası: ${event.message || 'UNKNOWN_UI_ERROR'}`);
  });

  window.addEventListener('unhandledrejection', () => {
    if (!byId('renderRuntimePanel')) return;
    badge('API ERROR', 'bad');
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

  loadScript('/dashboard-tabs.js', () => loadScript('/trade-tracker.js'));

  loadBootstrap();
  setTimeout(statusFromConnection, 2500);
  setInterval(statusFromConnection, 3000);
})();
