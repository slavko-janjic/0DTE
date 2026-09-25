/* Same-origin JSON client + the formatting helpers every renderer shares.
   Nothing app-specific lives here beyond that: app.js owns the DOM. */
(function (global) {
  'use strict';

  function request(path, options) {
    return fetch(path, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (body) {
        if (!response.ok) {
          var error = new Error(body.detail || response.statusText || 'Request failed');
          error.status = response.status;
          throw error;
        }
        return body;
      });
    });
  }

  function get(path) { return request(path, { headers: { 'Accept': 'application/json' } }); }

  function post(path, body) {
    return request(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify(body || {})
    });
  }

  /* Poll on an interval, but only while the tab is visible - a backgrounded
     phone shouldn't keep hitting the option chain. Runs once immediately. */
  function poll(fn, ms) {
    var timer = null;
    function tick() {
      if (!document.hidden) { fn(); }
      timer = setTimeout(tick, ms);
    }
    tick();
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) { fn(); }
    });
    return function stop() { clearTimeout(timer); };
  }

  /* --- formatting ------------------------------------------------------ */

  var MINUS = '−';  // a real minus sign, not a hyphen, for tabular numbers

  function isNum(value) { return typeof value === 'number' && isFinite(value); }

  function money(value, digits) {
    if (!isNum(value)) { return '—'; }
    var abs = Math.abs(value).toLocaleString('en-US', {
      minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 0
    });
    return (value < 0 ? MINUS + '$' : '$') + abs;
  }

  function signedMoney(value, digits) {
    if (!isNum(value)) { return '—'; }
    var abs = Math.abs(value).toLocaleString('en-US', {
      minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 0
    });
    return (value < 0 ? MINUS : '+') + '$' + abs;
  }

  function pct(value, digits) {
    if (!isNum(value)) { return '—'; }
    return value.toFixed(digits === undefined ? 0 : digits) + '%';
  }

  function signedPct(value, digits) {
    if (!isNum(value)) { return '—'; }
    var d = digits === undefined ? 1 : digits;
    return (value < 0 ? MINUS : '+') + Math.abs(value).toFixed(d) + '%';
  }

  function num(value, digits) {
    if (!isNum(value)) { return '—'; }
    var d = digits === undefined ? 2 : digits;
    return (value < 0 ? MINUS : '') + Math.abs(value).toLocaleString('en-US', {
      minimumFractionDigits: d, maximumFractionDigits: d
    });
  }

  function signedNum(value, digits) {
    if (!isNum(value)) { return '—'; }
    var d = digits === undefined ? 2 : digits;
    return (value < 0 ? MINUS : '+') + Math.abs(value).toFixed(d);
  }

  function tone(value) { return isNum(value) && value !== 0 ? (value > 0 ? 'up' : 'down') : ''; }

  function escapeHtml(text) {
    return String(text === null || text === undefined ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  global.API = {
    get: get, post: post, poll: poll,
    money: money, signedMoney: signedMoney, pct: pct, signedPct: signedPct,
    num: num, signedNum: signedNum, tone: tone, isNum: isNum,
    escapeHtml: escapeHtml, MINUS: MINUS
  };
})(window);
