'use strict';

(() => {
  const ISTANBUL_TIMEZONE = 'Europe/Istanbul';
  const asDate = value => value instanceof Date ? value : new Date(value ?? Date.now());
  const format = (value, options) => new Intl.DateTimeFormat('tr-TR', {
    timeZone: ISTANBUL_TIMEZONE,
    ...options,
  }).format(asDate(value));

  window.ISTANBUL_TIMEZONE = ISTANBUL_TIMEZONE;
  window.formatIstanbulTime = value => format(value, {hour: '2-digit', minute: '2-digit', second: '2-digit'});
  window.formatIstanbulDateTime = value => format(value, {
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
  window.formatIstanbulChartTime = value => format(value, {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
})();
