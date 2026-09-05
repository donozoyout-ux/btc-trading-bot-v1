'use strict';

(() => {
  const shell = document.querySelector('.shell');
  if (!shell || document.querySelector('.dashboard-board')) return;

  const style = document.createElement('style');
  style.textContent = `
    .dashboard-board{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:9px;margin-top:9px;align-items:start}
    .dashboard-board>section{min-width:0;margin:0!important}
    .dashboard-board .zone-hero{grid-column:span 8}
    .dashboard-board .zone-runtime{grid-column:span 4}
    .dashboard-board .zone-metrics{grid-column:1/-1}
    .dashboard-board .zone-chart{grid-column:span 8}
    .dashboard-board .zone-decision{grid-column:span 4}
    .dashboard-board .zone-account{grid-column:span 7}
    .dashboard-board .zone-execution{grid-column:span 5}
    .dashboard-board .zone-mtf{grid-column:span 8}
    .dashboard-board .zone-strategy{grid-column:span 4}
    .dashboard-board .zone-news,.dashboard-board .zone-plan,.dashboard-board .zone-logs{grid-column:span 4}
    .dashboard-board .zone-misc{grid-column:1/-1}

    .dashboard-board .zone-decision.dual-grid,
    .dashboard-board .zone-strategy.dual-grid{grid-template-columns:1fr}
    .dashboard-board .zone-news.dual-grid,
    .dashboard-board .zone-plan.dual-grid{grid-template-columns:1fr}

    .dashboard-board .panel,.dashboard-board .account-console,.dashboard-board .workbench{padding:13px;border-radius:15px}
    .dashboard-board .account-console{margin:0}
    .dashboard-board .account-head{margin-bottom:5px}
    .dashboard-board .account-message{margin-top:7px;padding:7px 9px;font-size:9px}
    .dashboard-board .account-balance-row{margin-top:8px;grid-template-columns:repeat(5,minmax(0,1fr));border-radius:11px}
    .dashboard-board .wallet-balance,.dashboard-board .account-stat{padding:9px 8px}
    .dashboard-board .wallet-balance strong{font-size:20px;margin:4px 4px 2px 0}
    .dashboard-board .account-stat strong{font-size:12px;margin:4px 3px 2px 0}
    .dashboard-board .wallet-balance span,.dashboard-board .account-stat span{font-size:8px}
    .dashboard-board .wallet-balance small,.dashboard-board .account-stat small{font-size:8px}
    .dashboard-board .account-table-wrap{margin-top:7px;max-height:132px;overflow:auto;border:1px solid rgba(255,255,255,.04);border-radius:9px}
    .dashboard-board .orders-table-wrap{margin-top:7px}
    .dashboard-board .account-table-title{position:sticky;top:0;z-index:2;margin:0;padding:6px 8px;background:rgba(5,13,23,.96)}
    .dashboard-board .account-table th,.dashboard-board .account-table td{padding:6px 7px;font-size:8px}
    .dashboard-board .account-table{min-width:760px}

    .dashboard-board .zone-execution .account-head{align-items:flex-start;gap:8px}
    .dashboard-board .zone-execution .account-head>div:first-child{min-width:0}
    .dashboard-board .zone-execution .account-head h2{white-space:nowrap}
    .dashboard-board .zone-execution .account-badges{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:5px}
    .dashboard-board .zone-execution .account-balance-row{grid-template-columns:repeat(2,minmax(0,1fr))}
    .dashboard-board .zone-execution .account-stat{min-height:52px}
    .dashboard-board .zone-execution .account-stat strong{font-size:13px}

    .dashboard-board .metrics-grid{gap:6px;margin:0}
    .dashboard-board .metric{min-height:60px;padding:8px 10px;border-radius:11px}
    .dashboard-board .metric strong{font-size:14px;margin:2px 0}
    .dashboard-board .metric span,.dashboard-board .metric small{font-size:8px}

    .dashboard-board .workbench-head{gap:10px}
    .dashboard-board .chart-wrap{height:330px}
    .dashboard-board .rsi-wrap{height:78px;margin-top:6px}
    .dashboard-board .legend{margin:8px 0 3px;gap:9px}
    .dashboard-board .tf-tabs button{padding:5px 9px;font-size:9px}

    .dashboard-board .section-head{margin-bottom:8px;font-size:11px}
    .dashboard-board .intelligence-grid,.dashboard-board .trade-plan{gap:5px;grid-template-columns:repeat(2,minmax(0,1fr))}
    .dashboard-board .intelligence-grid div,.dashboard-board .trade-plan div{padding:7px}
    .dashboard-board .intelligence-grid span,.dashboard-board .trade-plan span{font-size:8px}
    .dashboard-board .intelligence-grid strong,.dashboard-board .trade-plan strong{font-size:10px}
    .dashboard-board .reason-box{margin-top:6px;padding:7px 8px}
    .dashboard-board .reason-box p{font-size:9px;line-height:1.35;margin-top:3px}
    .dashboard-board .reason-box span{font-size:8px}
    .dashboard-board .pipeline{grid-template-columns:repeat(2,minmax(0,1fr));gap:5px}
    .dashboard-board .pipe-node{padding:6px;font-size:8px}
    .dashboard-board .pipe-node b{font-size:9px}
    .dashboard-board .source-row{padding:7px 8px}
    .dashboard-board .source-row b{font-size:9px}.dashboard-board .source-row small{font-size:8px}
    .dashboard-board .event-log{max-height:180px}
    .dashboard-board .event{grid-template-columns:68px 80px 1fr;padding:6px 7px;font-size:8px}

    .dashboard-board .zone-mtf .tf-intelligence,
    .dashboard-board .zone-mtf .ci-tf-grid{grid-template-columns:repeat(4,minmax(0,1fr))!important;gap:6px!important}
    .dashboard-board .zone-mtf .ci-tf-card{padding:8px;border-radius:10px}
    .dashboard-board .zone-mtf .ci-trend-line{padding:5px 6px;margin-bottom:4px}
    .dashboard-board .zone-mtf .ci-level-grid,
    .dashboard-board .zone-mtf .ci-momentum-grid{gap:4px;margin-bottom:4px}
    .dashboard-board .zone-mtf .ci-level-grid>div,
    .dashboard-board .zone-mtf .ci-momentum-grid>div{padding:5px}
    .dashboard-board .zone-mtf .ci-ema-block,
    .dashboard-board .zone-mtf .ci-bb-block,
    .dashboard-board .zone-mtf .ci-volume-block{padding:5px 6px;margin-bottom:4px}
    .dashboard-board .zone-mtf .ci-patterns{min-height:24px;padding:4px}

    @media(min-width:1720px){
      .dashboard-board{gap:8px}
      .dashboard-board .zone-chart{grid-column:span 9}
      .dashboard-board .zone-decision{grid-column:span 3}
      .dashboard-board .zone-account{grid-column:span 7}
      .dashboard-board .zone-execution{grid-column:span 5}
      .dashboard-board .chart-wrap{height:350px}
      .dashboard-board .zone-account .account-balance-row{grid-template-columns:repeat(5,minmax(0,1fr))}
      .dashboard-board .zone-execution .account-balance-row{grid-template-columns:repeat(3,minmax(0,1fr))}
    }
    @media(max-width:1439px){
      .dashboard-board{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
      .dashboard-board>section{grid-column:span 1!important}
      .dashboard-board .zone-metrics,.dashboard-board .zone-chart,.dashboard-board .zone-account,.dashboard-board .zone-mtf{grid-column:1/-1!important}
      .dashboard-board .zone-mtf .tf-intelligence,.dashboard-board .zone-mtf .ci-tf-grid{grid-template-columns:repeat(2,minmax(0,1fr))!important}
    }
    @media(max-width:820px){
      .dashboard-board{display:block;margin-top:8px}
      .dashboard-board>section{margin-bottom:9px!important}
      .dashboard-board .account-balance-row{grid-template-columns:repeat(2,minmax(0,1fr))}
      .dashboard-board .zone-mtf .tf-intelligence,.dashboard-board .zone-mtf .ci-tf-grid{grid-template-columns:1fr!important}
      .dashboard-board .chart-wrap{height:300px}
    }
  `;
  document.head.appendChild(style);

  const sections = Array.from(shell.children).filter(el => el.tagName === 'SECTION');
  const classify = section => {
    const text = (section.textContent || '').toUpperCase();
    if (section.id === 'renderRuntimePanel') return 'runtime';
    if (section.classList.contains('hero-grid')) return 'hero';
    if (section.classList.contains('metrics-grid')) return 'metrics';
    if (section.classList.contains('workbench')) return 'chart';
    if (text.includes('BINANCE DEMO ACCOUNT')) return 'account';
    if (text.includes('AUTOMATIC EXECUTION')) return 'execution';
    if (text.includes('DECISION PIPELINE') || text.includes('SIGNAL INTELLIGENCE')) return 'decision';
    if (text.includes('CHART READING V3')) return 'mtf';
    if (text.includes('STRATEGY ORCHESTRATOR') || text.includes('SYSTEM STATE')) return 'strategy';
    if (text.includes('NEWS INTELLIGENCE') || text.includes('AI ANALYST')) return 'news';
    if (text.includes('TRADE PLAN') || text.includes('DATA SOURCES')) return 'plan';
    if (text.includes('SYSTEM EVENTS')) return 'logs';
    return 'misc';
  };

  const board = document.createElement('div');
  board.className = 'dashboard-board';
  const topbar = shell.querySelector('.topbar');
  if (topbar) topbar.insertAdjacentElement('afterend', board);
  else shell.prepend(board);

  sections.forEach(section => {
    const zone = classify(section);
    section.classList.add(`zone-${zone}`);
    section.hidden = false;
    section.removeAttribute('data-dashboard-tab');
    board.appendChild(section);
  });

  document.querySelectorAll('.dashboard-tabs').forEach(el => el.remove());
  requestAnimationFrame(() => {
    window.dispatchEvent(new Event('resize'));
    setTimeout(() => window.dispatchEvent(new Event('resize')), 140);
  });
})();
