'use strict';

(() => {
  const shell = document.querySelector('.shell');
  if (!shell || document.querySelector('.dashboard-tabs')) return;

  const style = document.createElement('style');
  style.textContent = `
    .dashboard-tabs{display:flex;gap:7px;align-items:center;margin:9px 0;padding:7px;position:sticky;top:76px;z-index:9;border-radius:15px;overflow-x:auto;scrollbar-width:none}
    .dashboard-tabs::-webkit-scrollbar{display:none}
    .dashboard-tab{flex:0 0 auto;border:1px solid rgba(120,184,255,.11);background:rgba(255,255,255,.018);color:#7f93aa;border-radius:10px;padding:9px 16px;font:800 11px/1 Inter,ui-sans-serif,system-ui;letter-spacing:.035em;cursor:pointer;transition:.18s}
    .dashboard-tab:hover{color:#cdefff;border-color:rgba(61,224,255,.24);transform:translateY(-1px)}
    .dashboard-tab.active{color:#dffaff;background:linear-gradient(135deg,rgba(61,224,255,.16),rgba(156,108,255,.10));border-color:rgba(61,224,255,.28);box-shadow:inset 0 0 0 1px rgba(61,224,255,.05),0 8px 24px rgba(0,0,0,.14)}
    section[data-dashboard-tab][hidden]{display:none!important}
    @media(min-width:1440px){
      .dashboard-tabs{top:70px;margin:7px 0;padding:5px;gap:5px}
      .dashboard-tab{padding:8px 18px;font-size:10px}
      section[data-dashboard-tab]:not([hidden]){animation:dashTabIn .18s ease-out}
      @keyframes dashTabIn{from{opacity:.55;transform:translateY(3px)}to{opacity:1;transform:none}}
    }
    @media(max-width:760px){.dashboard-tabs{top:82px}.dashboard-tab{padding:9px 13px}}
  `;
  document.head.appendChild(style);

  const sections = Array.from(shell.children).filter(el => el.tagName === 'SECTION');

  const classify = section => {
    const text = (section.textContent || '').toUpperCase();
    if (section.id === 'renderRuntimePanel') return 'system';
    if (section.classList.contains('hero-grid') || section.classList.contains('metrics-grid')) return 'overview';
    if (section.classList.contains('workbench') || text.includes('CHART READING V3')) return 'chart';
    if (text.includes('BINANCE DEMO ACCOUNT') || text.includes('AUTOMATIC EXECUTION') || text.includes('TRADE PLAN')) return 'execution';
    if (text.includes('DECISION PIPELINE') || text.includes('SIGNAL INTELLIGENCE') || text.includes('STRATEGY ORCHESTRATOR') || text.includes('SYSTEM STATE')) return 'strategy';
    if (text.includes('NEWS INTELLIGENCE') || text.includes('AI ANALYST') || text.includes('DATA SOURCES') || text.includes('SYSTEM LOG')) return 'system';
    return 'overview';
  };

  sections.forEach(section => { section.dataset.dashboardTab = classify(section); });

  const tabs = [
    ['overview', 'Genel'],
    ['chart', 'Grafik'],
    ['strategy', 'Strateji'],
    ['execution', 'İşlemler'],
    ['system', 'Sistem'],
  ];

  const nav = document.createElement('nav');
  nav.className = 'dashboard-tabs glass';
  nav.setAttribute('aria-label', 'Dashboard bölümleri');
  nav.innerHTML = tabs.map(([key, label], index) =>
    `<button type="button" data-tab-key="${key}" class="dashboard-tab${index === 0 ? ' active' : ''}" aria-selected="${index === 0 ? 'true' : 'false'}">${label}</button>`
  ).join('');

  const topbar = shell.querySelector('.topbar');
  if (topbar) topbar.insertAdjacentElement('afterend', nav);
  else shell.prepend(nav);

  const activate = key => {
    sections.forEach(section => { section.hidden = section.dataset.dashboardTab !== key; });
    nav.querySelectorAll('.dashboard-tab').forEach(button => {
      const active = button.dataset.tabKey === key;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    try { sessionStorage.setItem('btc-dashboard-tab', key); } catch (_) {}
    requestAnimationFrame(() => {
      window.dispatchEvent(new Event('resize'));
      setTimeout(() => window.dispatchEvent(new Event('resize')), 120);
    });
  };

  nav.addEventListener('click', event => {
    const button = event.target.closest('.dashboard-tab');
    if (button) activate(button.dataset.tabKey);
  });

  let initial = 'overview';
  try {
    const saved = sessionStorage.getItem('btc-dashboard-tab');
    if (tabs.some(([key]) => key === saved)) initial = saved;
  } catch (_) {}
  activate(initial);
})();
