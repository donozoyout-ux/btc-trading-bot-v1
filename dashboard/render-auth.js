'use strict';

(() => {
  const TOKEN_KEY = 'btc_dashboard_admin_token';
  const nativeFetch = window.fetch.bind(window);

  const currentToken = () => sessionStorage.getItem(TOKEN_KEY) || '';
  const isPrivateApi = url => {
    const value = typeof url === 'string' ? url : (url && url.url) || '';
    return value.startsWith('/api/account') ||
      value.startsWith('/api/telegram') ||
      value.startsWith('/api/ai/status') ||
      value.startsWith('/api/ai/analyze');
  };

  function ensureGate() {
    if (document.getElementById('renderAuthGate')) return;
    const gate = document.createElement('div');
    gate.id = 'renderAuthGate';
    gate.className = 'auth-gate hidden';
    gate.innerHTML = `
      <div class="auth-card glass">
        <div class="logo-orbit"><span>₿</span></div>
        <div>
          <div class="eyebrow">RENDER PRIVATE DATA</div>
          <h2>Dashboard Admin Token</h2>
        </div>
        <p>Render'da DASHBOARD_ADMIN_TOKEN tanımlı olduğu için Binance Testnet hesap verisi korunuyor. Render Environment'a koyduğun aynı tokenı buraya gir.</p>
        <input id="renderAuthInput" type="password" autocomplete="off" placeholder="Dashboard admin token" />
        <div id="renderAuthError" class="error-text"></div>
        <button id="renderAuthSubmit" class="action-btn primary">Bağlan</button>
        <button id="renderAuthClear" class="small-btn">Kayıtlı tokenı temizle</button>
      </div>`;
    document.body.appendChild(gate);

    const input = document.getElementById('renderAuthInput');
    const submit = document.getElementById('renderAuthSubmit');
    const clear = document.getElementById('renderAuthClear');
    const error = document.getElementById('renderAuthError');

    submit.addEventListener('click', async () => {
      const token = String(input.value || '').trim();
      if (!token) {
        error.textContent = 'Token gerekli.';
        return;
      }
      sessionStorage.setItem(TOKEN_KEY, token);
      error.textContent = 'Doğrulanıyor…';
      try {
        const response = await nativeFetch('/api/account', {
          cache: 'no-store',
          headers: { Authorization: `Bearer ${token}` },
        });
        if (response.status === 401) {
          sessionStorage.removeItem(TOKEN_KEY);
          error.textContent = 'Token yanlış.';
          return;
        }
        gate.classList.add('hidden');
        error.textContent = '';
        window.location.reload();
      } catch (_) {
        error.textContent = 'Backend bağlantısı kurulamadı.';
      }
    });

    input.addEventListener('keydown', event => {
      if (event.key === 'Enter') submit.click();
    });

    clear.addEventListener('click', () => {
      sessionStorage.removeItem(TOKEN_KEY);
      input.value = '';
      error.textContent = 'Token temizlendi.';
    });
  }

  function showGate(message = '') {
    ensureGate();
    const gate = document.getElementById('renderAuthGate');
    const error = document.getElementById('renderAuthError');
    if (message && error) error.textContent = message;
    if (gate) gate.classList.remove('hidden');
  }

  window.fetch = async function(input, init = {}) {
    const options = { ...init };
    if (isPrivateApi(input)) {
      const token = currentToken();
      if (token) {
        const headers = new Headers(options.headers || {});
        if (!headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`);
        options.headers = headers;
      }
    }

    const response = await nativeFetch(input, options);
    if (isPrivateApi(input) && response.status === 401) {
      showGate(currentToken() ? 'Token geçersiz veya değişti.' : 'Hesap verisini görmek için admin token gerekli.');
    }
    return response;
  };

  window.addEventListener('DOMContentLoaded', async () => {
    ensureGate();
    try {
      const response = await nativeFetch('/api/bootstrap', { cache: 'no-store' });
      const data = await response.json();
      if (data.dashboard_admin_token_configured && !currentToken()) showGate();
    } catch (_) {
      // Render bridge will display backend status separately.
    }
  });
})();
