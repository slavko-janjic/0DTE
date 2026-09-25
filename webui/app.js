/* 0DTE Console frontend.

   The DOM is the mockup's; this file only fills it from /api/*. Every page
   fetches on show and the visible page re-polls on a ~12s cadence (the same
   rhythm the Streamlit fragments used), while the header polls always.
*/
(function () {
  'use strict';

  var A = window.API;
  var POLL_MS = 12000;
  var SLOW_POLL_MS = 60000;   // the fallback cadence once the SSE stream is live

  var state = {
    page: 'signals',
    ticker: null,
    range: 'session',
    calendar: { year: null, month: null },
    autopilotMode: null,
    exitOptions: [null, 5, 10, 20, 30, 40, 50],
    pendingAmountEdit: false
  };

  function $(id) { return document.getElementById(id); }
  function q(selector, root) { return (root || document).querySelector(selector); }
  function qa(selector, root) { return Array.prototype.slice.call((root || document).querySelectorAll(selector)); }

  function toast(message, kind) {
    var el = document.createElement('div');
    el.className = 'toast' + (kind ? ' ' + kind : '');
    el.innerHTML = message;
    $('toasts').appendChild(el);
    setTimeout(function () { el.remove(); }, 6000);
  }

  function fail(target, error) {
    if (target) {
      target.innerHTML = '<div class="empty">Couldn\'t load this right now — ' +
        A.escapeHtml(error && error.message ? error.message : 'connection lost') + '.</div>';
    }
  }

  /* ---- theme (3-way: system / light / dark) ------------------------- */

  var SUN = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4.2"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
  var MOON = '<svg viewBox="0 0 24 24"><path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z"/></svg>';
  var SYS = '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg>';
  var THEMES = [{ k: 'system', l: 'System', i: SYS }, { k: 'light', l: 'Light', i: SUN }, { k: 'dark', l: 'Dark', i: MOON }];

  function initTheme() {
    var root = document.documentElement;
    var index = 0;
    try {
      var saved = localStorage.getItem('0dte-theme');
      if (saved) {
        var found = THEMES.findIndex(function (t) { return t.k === saved; });
        if (found >= 0) { index = found; }
      }
    } catch (e) { /* private mode: fall back to system */ }
    var button = $('theme');
    function apply() {
      var theme = THEMES[index];
      if (theme.k === 'system') { root.removeAttribute('data-theme'); }
      else { root.setAttribute('data-theme', theme.k); }
      button.innerHTML = theme.i + '<span>' + theme.l + '</span>';
    }
    button.addEventListener('click', function () {
      index = (index + 1) % THEMES.length;
      try { localStorage.setItem('0dte-theme', THEMES[index].k); } catch (e) { /* ignore */ }
      apply();
    });
    apply();
  }

  /* ---- routing ------------------------------------------------------- */

  function showPage(page, skipLoad) {
    state.page = page;
    qa('#nav a').forEach(function (a) { a.classList.toggle('on', a.dataset.p === page); });
    qa('.page').forEach(function (section) { section.hidden = section.dataset.page !== page; });
    try { localStorage.setItem('0dte-page', page); } catch (e) { /* ignore */ }
    if (!skipLoad) { loadPage(); }
  }

  function loadPage() {
    if (state.page === 'signals') { loadSignal(); loadHistory(); }
    else if (state.page === 'lab') { loadLab(); }
    else if (state.page === 'cost') { loadCost(); }
    else if (state.page === 'trades') { loadPositions(); loadTradeHistory(); loadCalendar(); }
    else if (state.page === 'autopilot') { loadAutopilot(); }
    else if (state.page === 'tuning') { loadCalibration(); }
  }

  /* ---- header: strip, clock, rail, worker health --------------------- */

  function renderStrip(tickers) {
    var strip = $('senti');
    if (!state.ticker && tickers.length) { selectTicker(tickers[0].ticker, true); }
    strip.innerHTML = tickers.map(function (row) {
      var tone = row.tone === 'up' ? 'up' : (row.tone === 'down' ? 'down' : '');
      return '<button class="senti ' + tone + (row.ticker === state.ticker ? ' sel' : '') +
        '" data-tk="' + A.escapeHtml(row.ticker) + '">' +
        '<span class="t">' + A.escapeHtml(row.ticker) + '</span>' +
        '<span class="v num">' + (A.isNum(row.confidence) ? A.pct(row.confidence) : '—') + '</span></button>';
    }).join('');
  }

  function renderWallets(wallet, autopilot) {
    function tiles(extra) {
      return '<div class="stat"><div class="n num">' + A.money(wallet.balance) + '</div><div class="l">Balance</div></div>' +
        '<div class="stat"><div class="n num ' + A.tone(wallet.realized_today) + '">' + A.signedMoney(wallet.realized_today) + '</div><div class="l">Today</div></div>' +
        extra +
        '<div class="stat"><div class="n num">' + A.money(wallet.open_exposure) + '</div><div class="l">Open exposure</div></div>';
    }
    var total = '<div class="stat"><div class="n num ' + A.tone(wallet.realized_total) + '">' +
      A.signedMoney(wallet.realized_total) + '</div><div class="l">Total P&amp;L</div></div>';
    var auto = '<div class="stat"><div class="n num ' + A.tone(autopilot.pnl_today) + '">' +
      A.signedMoney(autopilot.pnl_today) + '</div><div class="l">Auto P&amp;L today</div></div>';
    $('wallet-signals').innerHTML = tiles(total);
    $('wallet-trades').innerHTML = tiles(total);
    $('wallet-auto').innerHTML = tiles(auto);
  }

  function renderHealth(worker, autopilot, market) {
    var banner = $('worker-banner');
    var down = worker.status !== 'up';
    banner.hidden = !down;
    if (down) { banner.innerHTML = '<b>Worker</b> ' + A.escapeHtml(worker.message); }

    var rows = [];
    if (down) { rows.push('<div class="sd"><span class="dot"></span>worker down</div>'); }
    if (autopilot.mode === 'off') { rows.push('<div class="sd"><span class="dot"></span>autopilot off</div>'); }
    var sysdown = $('sysdown');
    sysdown.innerHTML = rows.join('');
    sysdown.hidden = rows.length === 0;
    q('#nav a[data-p="signals"]').classList.toggle('alert', down);
    q('#nav a[data-p="autopilot"]').classList.toggle('alert', autopilot.mode === 'off');

    var badge = $('nav-ap-badge');
    badge.hidden = !autopilot.trades_today;
    badge.textContent = autopilot.trades_today || '';

    $('rail-fill').style.width = (market.progress * 100).toFixed(2) + '%';
    $('rail-fill').className = market.urgency === 'calm' ? '' : market.urgency;
    $('rail').title = market.label;
    $('clock').innerHTML = '<span class="dot ' + (down ? 'off' : (market.open ? 'ok' : 'warn')) + '"></span>' +
      A.escapeHtml(market.open ? market.now_et + ' ET · ' + market.label : 'Market closed');
  }

  function loadOverview() {
    return A.get('/api/overview').then(function (data) {
      renderStrip(data.tickers);
      renderWallets(data.wallet, data.autopilot);
      renderHealth(data.worker, data.autopilot, data.market);
      state.autopilotMode = data.autopilot.mode;
      state.balance = data.wallet.balance;
      state.startingBalance = data.wallet.starting_balance;
      checkAlerts(data.autopilot);
    }).catch(function (error) {
      $('clock').innerHTML = '<span class="dot off"></span>API unreachable';
      console.error(error);
    });
  }

  /* ---- signals page --------------------------------------------------- */

  function selectTicker(ticker, quiet) {
    state.ticker = ticker;
    qa('.tkname').forEach(function (node) { node.textContent = ticker; });
    qa('#senti .senti').forEach(function (b) { b.classList.toggle('sel', b.dataset.tk === ticker); });
    try { localStorage.setItem('0dte-ticker', ticker); } catch (e) { /* ignore */ }
    if (quiet) { return; }
    loadSignal();
    loadHistory();
    if (state.page === 'cost') { loadCost(); }
    if (state.page === 'tuning') { loadCalibration(); }
  }

  function optionMarkup(options, selected, sign) {
    return options.map(function (value) {
      var label = value === null ? 'Off' : (sign < 0 ? A.MINUS : '+') + value + '%';
      var chosen = (value === null && selected === null) || (value !== null && selected === value);
      return '<option value="' + (value === null ? '' : value) + '"' + (chosen ? ' selected' : '') +
        '>' + label + '</option>';
    }).join('');
  }

  function renderSignal(signal) {
    state.exitOptions = signal.exit_pct_options || state.exitOptions;
    var body = $('sig-body');
    if (!signal.available) {
      $('sig-live').textContent = '';
      body.innerHTML = '<div class="empty">' + A.escapeHtml(signal.message || 'No signal yet.') + '</div>';
      $('subscores').innerHTML = '<div class="empty">No subscores yet.</div>';
    } else {
      $('sig-live').innerHTML = '<span class="num">' + (A.isNum(signal.spot_price) ? '$' + A.num(signal.spot_price) : '—') +
        '</span> · ' + A.escapeHtml(signal.stale ? signal.day_et : signal.time_et + ' ET');
      $('sig-live').className = 'sig-live' + (signal.stale ? ' stale' : '');
      body.innerHTML =
        '<div class="sig-big"><span class="d sig-dir ' + signal.tone + '">' +
          A.escapeHtml(signal.direction.toUpperCase()) + '</span>' +
        '<span class="c sig-conf num">' + A.pct(signal.confidence) + '</span></div>' +
        '<div class="sub sig-raw" style="margin-top:6px">' +
          (A.isNum(signal.raw_confidence) ? 'calibrated · raw ' + A.pct(signal.raw_confidence) :
            A.escapeHtml(signal.recommendation || '')) + '</div>' +
        '<div style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap">' +
          (signal.gamma ? '<span class="chip" title="' + A.escapeHtml(signal.gamma.blurb) + '">' +
            A.escapeHtml(signal.gamma.label) + '</span>' : '') +
          (signal.streak ? '<span class="chip">' + A.escapeHtml(signal.streak.label) + '</span>' : '') +
          '<span class="chip' + (signal.stale ? ' stale' : '') + '">' + A.escapeHtml(signal.age_text) + '</span>' +
        '</div>';

      $('subscores').innerHTML = signal.subscores.map(function (score) {
        var tip = score.name + ' · weight ' + A.pct(score.weight_pct) + ' · reading ' +
          A.signedNum(score.value) + ' — ' + score.description;
        return '<div class="ss" title="' + A.escapeHtml(tip) + '"><span>' +
          A.escapeHtml(score.key) + '</span><div class="meter"><i class="' + score.tone +
          '" style="width:' + (score.meter_pct || 50).toFixed(0) + '%"></i></div></div>';
      }).join('');
    }

    var setup = signal.day_setup;
    if (!setup) {
      $('setup-body').innerHTML = '<div class="empty">No pre-market setup yet — it\'s assembled in the ~90 min before the open.</div>';
    } else {
      var gapTone = A.isNum(setup.gap_pct) ? A.tone(setup.gap_pct) : '';
      var levels = [];
      if (A.isNum(setup.overnight_low) || A.isNum(setup.overnight_high)) {
        levels.push('O/N range ' + A.num(setup.overnight_low, 1) + ' – ' + A.num(setup.overnight_high, 1));
      }
      levels.push(setup.catalysts.length
        ? 'today: ' + setup.catalysts.map(function (c) {
            return c.label + (c.time ? ' @ ' + c.time + ' ET' : '');
          }).join(', ')
        : 'no catalyst today');
      $('setup-body').innerHTML =
        '<div class="stat-row" style="margin-top:12px;gap:22px">' +
          '<div class="stat"><div class="n ' + gapTone + '">' + A.signedPct(setup.gap_pct, 2) + '</div><div class="l">Gap</div></div>' +
          '<div class="stat"><div class="n num">' + A.num(setup.prior_close, 1) + '</div><div class="l">Prior close</div></div>' +
        '</div>' +
        '<div class="sub" style="margin-top:12px">' + A.escapeHtml(levels.join(' · ')) + '</div>' +
        (setup.catalysts.length ? '<div class="banner warn" style="margin-top:12px">Autopilot stands down near today\'s catalyst.</div>' : '');
    }

    // trade form: pre-filled from the signal, still fully editable
    if (signal.suggested_type && !state.pendingAmountEdit) {
      $('pt-type').value = signal.suggested_type;
    }
    $('pt-strike').value = A.isNum(signal.suggested_strike) ? signal.suggested_strike : '';
    if (!state.pendingAmountEdit && !$('pt-amount').value) {
      $('pt-amount').value = signal.default_amount;
    }
    if (!$('pt-target').options.length) {
      var targets = signal.autopilot_targets || { profit_target_pct: 50, stop_loss_pct: -35 };
      $('pt-target').innerHTML = optionMarkup(state.exitOptions, targets.profit_target_pct, 1);
      $('pt-stop').innerHTML = optionMarkup(state.exitOptions, Math.abs(targets.stop_loss_pct), -1);
    }
    refreshQuote();
  }

  function loadSignal() {
    if (!state.ticker) { return Promise.resolve(); }
    return A.get('/api/signal/' + state.ticker).then(renderSignal)
      .catch(function (error) { fail($('sig-body'), error); });
  }

  /* ---- price + score chart ------------------------------------------- */

  /* composite accuracy only counts calls since its current definition */
  function sinceText(data) {
    return data.composite_since ? ' since ' + A.escapeHtml(data.composite_since) : '';
  }

  function buildChart(data) {
    var points = data.points || [];
    if (!points.length) {
      $('chart').innerHTML = '<div class="empty">' + A.escapeHtml(data.message || 'No data in this range yet.') + '</div>';
      $('chart-legend').innerHTML = '';
      return;
    }
    var W = 720, H = 220, BASE = 150, TOP = 20, BOTTOM = 140;
    var prices = points.map(function (p) { return p.price; }).filter(A.isNum);
    var lo = Math.min.apply(null, prices), hi = Math.max.apply(null, prices);
    if (hi === lo) { hi = lo + 0.5; lo = lo - 0.5; }

    // Levels share the price scale but never widen it: a level outside the
    // traded range would flatten the price line, which is exactly what the
    // server-side visible_levels filter exists to prevent.
    var levels = (data.levels || []).filter(function (level) {
      return A.isNum(level.value) && level.value >= lo && level.value <= hi;
    });

    function x(i) { return points.length === 1 ? W : (i / (points.length - 1)) * W; }
    function yPrice(v) { return BOTTOM - ((v - lo) / (hi - lo)) * (BOTTOM - TOP); }
    function yScore(v) { return BASE - Math.max(-1, Math.min(1, v || 0)) * 62; }

    var priceLine = points.map(function (p, i) { return x(i).toFixed(1) + ',' + yPrice(p.price).toFixed(1); }).join(' ');
    var scorePts = points.map(function (p, i) { return x(i).toFixed(1) + ',' + yScore(p.score).toFixed(1); });
    var area = '0,' + BASE + ' ' + scorePts.join(' ') + ' ' + W + ',' + BASE;
    var last = points[points.length - 1];

    var timeAt = function (iso) {
      var index = points.findIndex(function (p) { return p.iso >= iso; });
      return x(index < 0 ? points.length - 1 : index);
    };

    var levelLines = levels.map(function (level) {
      var y = yPrice(level.value).toFixed(1);
      return '<line x1="0" y1="' + y + '" x2="' + W + '" y2="' + y + '" stroke="var(--faint)" ' +
        'stroke-width="1" stroke-dasharray="6 3" opacity=".55"><title>' +
        A.escapeHtml(level.label + ' ' + A.num(level.value)) + '</title></line>';
    }).join('');

    var eventLines = (data.events || []).map(function (event) {
      var ex = timeAt(event.iso).toFixed(1);
      var color = event.kind === 'Entry' ? 'var(--up)' : 'var(--down)';
      return '<line x1="' + ex + '" y1="' + TOP + '" x2="' + ex + '" y2="' + (BASE + 62) +
        '" stroke="' + color + '" stroke-width="1.5" opacity=".7"><title>' +
        A.escapeHtml(event.label) + '</title></line>';
    }).join('');

    $('chart').innerHTML =
      '<svg class="chart" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" role="img" ' +
        'aria-label="Price and composite score over the selected range">' +
        '<line x1="0" y1="' + BASE + '" x2="' + W + '" y2="' + BASE + '" stroke="var(--border)"/>' +
        levelLines +
        '<polygon fill="var(--accent-soft)" stroke="none" points="' + area + '"/>' +
        '<polyline fill="none" stroke="var(--accent)" stroke-width="2" points="' + scorePts.join(' ') + '"/>' +
        '<polyline fill="none" stroke="var(--faint)" stroke-width="1.5" opacity=".85" points="' + priceLine + '"/>' +
        eventLines +
        '<circle cx="' + x(points.length - 1).toFixed(1) + '" cy="' + yPrice(last.price).toFixed(1) +
          '" r="3.5" fill="var(--faint)"/>' +
        '<circle cx="' + x(points.length - 1).toFixed(1) + '" cy="' + yScore(last.score).toFixed(1) +
          '" r="3.5" fill="var(--accent)"/>' +
      '</svg>';

    var accuracy = A.isNum(data.accuracy_pct)
      ? 'Overall accuracy (' + data.horizon_minutes + ' min): ' + A.pct(data.accuracy_pct) +
        ' · ' + data.graded_count + ' graded' + sinceText(data)
      : 'Signals are graded ' + data.horizon_minutes + ' min later — none graded yet';
    $('chart-legend').innerHTML =
      '<span><i style="background:var(--faint)"></i>Price</span>' +
      '<span><i style="background:var(--accent)"></i>Composite score</span>' +
      '<span>' + A.escapeHtml(points[0].t + ' – ' + last.t + ' ET') + '</span>' +
      '<span>' + A.escapeHtml(accuracy) + '</span>';

    var daily = data.daily || [];
    $('daily-drawer').hidden = !daily.length;
    if (daily.length) {
      $('daily-table').innerHTML =
        '<table><thead><tr><th>Date</th><th class="num">Graded</th><th class="num">Correct</th>' +
        '<th class="num">Accuracy</th></tr></thead><tbody>' +
        daily.map(function (day) {
          return '<tr><td class="num">' + A.escapeHtml(day.date) + '</td>' +
            '<td class="num">' + day.total + '</td>' +
            '<td class="num">' + day.hits + '</td>' +
            '<td class="num">' + A.pct(day.accuracy_pct) + '</td></tr>';
        }).join('') + '</tbody></table>';
    }

    // hover readout: nearest point by x
    var wrap = $('chart-wrap'), tip = $('chart-tip');
    wrap.onmousemove = function (event) {
      var rect = wrap.getBoundingClientRect();
      var ratio = (event.clientX - rect.left) / rect.width;
      var index = Math.max(0, Math.min(points.length - 1, Math.round(ratio * (points.length - 1))));
      var point = points[index];
      tip.hidden = false;
      tip.style.left = (ratio * 100).toFixed(2) + '%';
      tip.style.top = '30px';
      tip.innerHTML = point.t + ' · $' + A.num(point.price) + ' · score ' + A.signedNum(point.score) +
        ' · ' + A.escapeHtml(point.direction) + ' ' + A.pct(point.confidence);
    };
    wrap.onmouseleave = function () { tip.hidden = true; };
  }

  function loadHistory() {
    if (!state.ticker) { return Promise.resolve(); }
    return A.get('/api/signal/' + state.ticker + '/history?range=' + state.range)
      .then(buildChart).catch(function (error) { fail($('chart'), error); });
  }

  /* ---- place a trade -------------------------------------------------- */

  function selectedTargets() {
    var target = $('pt-target').value === '' ? null : Number($('pt-target').value);
    var stop = $('pt-stop').value === '' ? null : -Math.abs(Number($('pt-stop').value));
    return { profit_target_pct: target, stop_loss_pct: stop };
  }

  var quoteTimer = null;
  var lastQuote = { key: null, at: 0 };
  function refreshQuote(force) {
    clearTimeout(quoteTimer);
    quoteTimer = setTimeout(function () {
      var amount = Number($('pt-amount').value || 0);
      if (!state.ticker || !amount) { $('pt-quote').textContent = ''; return; }
      var key = state.ticker + $('pt-type').value + amount;
      if (!force && key === lastQuote.key && Date.now() - lastQuote.at < 60000) { return; }
      lastQuote = { key: key, at: Date.now() };
      A.get('/api/quote/' + state.ticker + '?option_type=' + $('pt-type').value + '&amount=' + amount)
        .then(function (quote) {
          if (!quote.available) { $('pt-quote').textContent = quote.message || ''; return; }
          $('pt-strike').value = quote.strike;
          $('pt-quote').innerHTML = quote.contracts
            ? A.escapeHtml(quote.contracts + ' × ' + state.ticker + ' ' + quote.strike + ' ' +
                quote.option_type + ' @ $' + A.num(quote.entry_price) + ' ask = ' + A.money(quote.cost) +
                ' · spread ' + A.pct(quote.spread_pct, 1) + ' · exp ' + quote.expiration)
            : 'Not enough for one contract at $' + A.num(quote.entry_price) + '.';
        }).catch(function () { $('pt-quote').textContent = ''; });
    }, 250);
  }

  function placeTrade() {
    var amount = Number($('pt-amount').value || 0);
    if (!amount) { toast('Enter an amount to risk.', 'bad'); return; }
    var targets = selectedTargets();
    var button = $('pt-buy');
    button.disabled = true;
    A.post('/api/trade', {
      ticker: state.ticker, option_type: $('pt-type').value, amount: amount,
      profit_target_pct: targets.profit_target_pct, stop_loss_pct: targets.stop_loss_pct
    }).then(function (result) {
      toast(A.escapeHtml(result.message), 'good');
      loadOverview();
      if (state.page === 'trades') { renderPositions(result.positions); }
    }).catch(function (error) {
      toast(A.escapeHtml(error.message), 'bad');
    }).then(function () { button.disabled = false; });
  }

  /* ---- trades page ---------------------------------------------------- */

  function renderPositions(payload) {
    state.exitOptions = payload.exit_pct_options || state.exitOptions;
    (payload.auto_closed || []).forEach(function (closed) {
      toast('Auto-closed ' + A.escapeHtml(closed.ticker + ' ' + closed.option_type) +
        ' (' + A.escapeHtml(closed.reason) + ')', 'good');
    });
    var rows = payload.positions || [];
    if (!rows.length) {
      $('positions').innerHTML = '<div class="empty" style="padding:0 17px 16px">No open positions.</div>';
      return;
    }
    $('positions').innerHTML =
      '<table><thead><tr><th>Ticker</th><th>Type</th><th class="num">Strike</th><th class="num">Entry</th>' +
      '<th class="num">Now</th><th class="num">P&amp;L</th><th>Targets</th><th>Source</th><th></th></tr></thead><tbody>' +
      rows.map(function (row) {
        var stop = A.isNum(row.stop_loss_pct) ? Math.abs(row.stop_loss_pct) : null;
        var extras = [];
        if (A.isNum(row.spread_pct)) { extras.push('spread ' + A.pct(row.spread_pct, 1)); }
        if (A.isNum(row.peak_pct)) { extras.push('peak +' + A.pct(row.peak_pct)); }
        if (row.suggested_exit_reason) { extras.push('suggested exit: ' + row.suggested_exit_reason.replace(/_/g, ' ')); }
        return '<tr data-id="' + row.id + '">' +
          '<td class="tkcell">' + A.escapeHtml(row.ticker) + '<div class="sub">x' + row.contracts +
            ' · exp ' + A.escapeHtml(row.expiration) + (extras.length ? ' · ' + A.escapeHtml(extras.join(' · ')) : '') + '</div></td>' +
          '<td>' + A.escapeHtml(row.option_type) + '</td>' +
          '<td class="num">' + A.num(row.strike, 0) + '</td>' +
          '<td class="num">' + A.num(row.entry_price) + '</td>' +
          '<td class="num">' + A.num(row.current_price) + '</td>' +
          '<td class="num ' + A.tone(row.pnl) + '">' + A.signedMoney(row.pnl) +
            (A.isNum(row.pnl_pct) ? '<div class="sub">' + A.signedPct(row.pnl_pct) + '</div>' : '') + '</td>' +
          '<td><div class="tgt">' +
            '<select data-target="profit" class="' + (A.isNum(row.profit_target_pct) ? 'set' : '') + '">' +
              optionMarkup(state.exitOptions, row.profit_target_pct, 1) + '</select>' +
            '<select data-target="stop" class="' + (stop !== null ? 'set' : '') + '">' +
              optionMarkup(state.exitOptions, stop, -1) + '</select>' +
          '</div></td>' +
          '<td><span class="src ' + (row.opened_by === 'auto' ? 'auto">auto' : 'you">you') + '</span></td>' +
          '<td style="text-align:right"><button class="closebtn">Close</button></td></tr>';
      }).join('') + '</tbody></table>';
  }

  function loadPositions() {
    return A.get('/api/positions').then(function (payload) {
      renderPositions(payload);
      if (payload.auto_closed && payload.auto_closed.length) { loadOverview(); loadTradeHistory(); }
    }).catch(function (error) { fail($('positions'), error); });
  }

  function loadTradeHistory() {
    return A.get('/api/history').then(function (payload) {
      var rows = payload.trades || [];
      if (!rows.length) {
        $('history').innerHTML = '<div class="empty" style="padding:0 17px 16px">No closed trades yet.</div>';
        return;
      }
      $('history').innerHTML =
        '<table><thead><tr><th>Date</th><th>Ticker</th><th>Type</th><th class="num">Score</th>' +
        '<th>Exit</th><th class="num">P&amp;L</th><th>Source</th></tr></thead><tbody>' +
        rows.map(function (row) {
          return '<tr><td class="num">' + A.escapeHtml(row.date) + '</td>' +
            '<td class="tkcell">' + A.escapeHtml(row.ticker) + '</td>' +
            '<td>' + A.escapeHtml(row.option_type) + '</td>' +
            '<td class="num">' + A.signedNum(row.score) + '</td>' +
            '<td>' + A.escapeHtml(row.exit_reason) + '</td>' +
            '<td class="num ' + A.tone(row.pnl) + '">' + A.signedMoney(row.pnl) + '</td>' +
            '<td><span class="src ' + (row.opened_by === 'auto' ? 'auto">auto' : 'you">you') + '</span></td></tr>';
        }).join('') + '</tbody></table>';
    }).catch(function (error) { fail($('history'), error); });
  }

  function loadCalendar() {
    var query = state.calendar.year
      ? '?year=' + state.calendar.year + '&month=' + state.calendar.month : '';
    return A.get('/api/calendar' + query).then(function (payload) {
      state.calendar = { year: payload.year, month: payload.month };
      $('cal-title').textContent = payload.label;
      var cells = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'].map(function (day) {
        return '<div class="wd">' + day + '</div>';
      });
      payload.weeks.forEach(function (week) {
        week.forEach(function (cell) {
          if (!cell.in_month) { cells.push('<div class="cell empty"></div>'); return; }
          var klass = 'cell' + (A.isNum(cell.pnl) ? (cell.pnl >= 0 ? ' pos' : ' neg') : '') +
            (cell.today ? ' big' : '');
          cells.push('<div class="' + klass + '"><span class="dn">' + cell.day + '</span>' +
            (A.isNum(cell.pnl) ? '<span class="pl">' + A.signedMoney(cell.pnl) + '</span>' : '') + '</div>');
        });
      });
      $('calendar').innerHTML = cells.join('');
      $('cal-total').innerHTML = A.escapeHtml(payload.short_label) + ' so far: <b class="' +
        A.tone(payload.month_total) + '">' + A.signedMoney(payload.month_total) + '</b>';
    }).catch(function (error) { fail($('calendar'), error); });
  }

  function shiftMonth(delta) {
    var index = state.calendar.year * 12 + (state.calendar.month - 1) + delta;
    state.calendar = { year: Math.floor(index / 12), month: (index % 12) + 1 };
    loadCalendar();
  }

  /* ---- strategy lab ---------------------------------------------------- */

  function loadLab() {
    return A.get('/api/lab').then(function (payload) {
      var rows = payload.rows || [];
      if (!rows.length) {
        $('lab-table').innerHTML = '<div class="empty">' + A.escapeHtml(payload.message || 'No strategies yet.') + '</div>';
        return;
      }
      $('lab-table').innerHTML =
        '<table><thead><tr><th>Strategy</th><th class="num">Trades</th><th class="num">Win</th>' +
        '<th class="num">Total P&amp;L</th><th class="num">Profit factor</th><th class="num">t</th>' +
        '<th class="num">Need</th><th>Verdict</th></tr></thead><tbody>' +
        rows.map(function (row) {
          return '<tr><td class="tkcell">' + A.escapeHtml(row.name) +
              (row.open ? '<div class="sub">' + row.open + ' open</div>' : '') + '</td>' +
            '<td class="num">' + row.trades + '</td>' +
            '<td class="num">' + (A.isNum(row.win_rate_pct) ? A.pct(row.win_rate_pct) : '—') + '</td>' +
            '<td class="num ' + A.tone(row.total_pnl) + '">' + A.signedMoney(row.total_pnl) + '</td>' +
            '<td class="num">' + (A.isNum(row.profit_factor) ? row.profit_factor.toFixed(2) : '—') + '</td>' +
            '<td class="num">' + (A.isNum(row.t_stat) ? A.signedNum(row.t_stat) : '—') + '</td>' +
            '<td class="num">' + (row.trades_needed ? row.trades_needed.toLocaleString('en-US') : '—') + '</td>' +
            '<td><span class="badge b-' + row.verdict_tone + '">' + A.escapeHtml(row.verdict) + '</span></td></tr>';
        }).join('') + '</tbody></table>';
    }).catch(function (error) { fail($('lab-table'), error); });
  }

  /* ---- cost of trading -------------------------------------------------- */

  function buildCostChart(payload) {
    var curve = (payload.curve || []).filter(function (point) { return A.isNum(point.median_pct); });
    if (!curve.length) {
      $('cost-chart').innerHTML = '<div class="empty">' + A.escapeHtml(payload.message || 'No quotes yet.') + '</div>';
      return;
    }
    var W = 720, H = 240, L = 40, R = 710, T = 30, B = 200;
    var max = Math.max.apply(null, curve.map(function (point) { return point.median_pct; }));
    var top = Math.max(3, Math.ceil(max / 3) * 3);
    function x(i) { return curve.length === 1 ? (L + R) / 2 : L + (i / (curve.length - 1)) * (R - L); }
    function y(v) { return B - (v / top) * (B - T); }

    var line = curve.map(function (point, i) { return x(i).toFixed(1) + ',' + y(point.median_pct).toFixed(1); });
    var cheapest = curve.reduce(function (best, point, i) {
      return point.median_pct < curve[best].median_pct ? i : best;
    }, 0);

    var gridValues = [0, top / 3, (2 * top) / 3, top];
    var grid = gridValues.map(function (value) {
      return '<line x1="' + L + '" y1="' + y(value).toFixed(1) + '" x2="' + R + '" y2="' + y(value).toFixed(1) +
        '" opacity="' + (value === 0 ? 1 : 0.5) + '"/>';
    }).join('');
    var yLabels = gridValues.map(function (value) {
      return '<text x="' + (L - 6) + '" y="' + (y(value) + 3).toFixed(1) + '" text-anchor="end">' +
        value.toFixed(0) + '%</text>';
    }).join('');
    var ticks = [0, Math.floor(curve.length / 3), Math.floor((2 * curve.length) / 3), curve.length - 1];
    var xLabels = ticks.filter(function (i, pos, all) { return all.indexOf(i) === pos; }).map(function (i) {
      var anchor = i === 0 ? 'start' : (i === curve.length - 1 ? 'end' : 'middle');
      return '<text x="' + x(i).toFixed(1) + '" y="216" text-anchor="' + anchor + '">' +
        A.escapeHtml(curve[i].clock) + '</text>';
    }).join('');

    $('cost-chart').innerHTML =
      '<svg class="chart" viewBox="0 0 ' + W + ' ' + H + '" role="img" ' +
        'aria-label="Median round-trip spread cost by time of day">' +
        '<g stroke="var(--border)" stroke-width="1">' + grid +
          '<line x1="' + L + '" y1="' + T + '" x2="' + L + '" y2="' + B + '"/></g>' +
        '<g font-family="var(--mono)" font-size="10" fill="var(--faint)">' + yLabels + xLabels + '</g>' +
        '<polygon fill="var(--accent-soft)" points="' + L + ',' + B + ' ' + line.join(' ') + ' ' + R + ',' + B + '"/>' +
        '<polyline fill="none" stroke="var(--accent)" stroke-width="2.5" points="' + line.join(' ') + '"/>' +
        '<circle cx="' + x(cheapest).toFixed(1) + '" cy="' + y(curve[cheapest].median_pct).toFixed(1) +
          '" r="4" fill="var(--accent)"/>' +
        '<text x="' + x(cheapest).toFixed(1) + '" y="' + (y(curve[cheapest].median_pct) + 20).toFixed(1) +
          '" text-anchor="middle" font-family="var(--mono)" font-size="10.5" fill="var(--up)" ' +
          'font-weight="600">cheapest ~' + A.escapeHtml(curve[cheapest].clock) + '</text>' +
      '</svg>';

    $('cost-cheapest').textContent = payload.cheapest.length
      ? 'Cheapest: ' + payload.cheapest.map(function (window) {
          return window.label + ' (' + A.pct(window.median_pct, 1) + ')';
        }).join(', ')
      : '';
    $('cost-stats').innerHTML =
      '<div class="stat"><div class="n">' + A.pct(payload.at_open, 1) + '</div><div class="l">At the open</div></div>' +
      '<div class="stat"><div class="n up">' + A.pct(payload.midday, 1) + '</div><div class="l">' +
        A.escapeHtml(payload.midday_label ? 'Cheapest ' + payload.midday_label : 'Mid-day') + '</div></div>';
  }

  function loadCost() {
    if (!state.ticker) { return Promise.resolve(); }
    return A.get('/api/cost/' + state.ticker).then(function (payload) {
      buildCostChart(payload);
      $('cost-table').innerHTML =
        '<table><thead><tr><th>Ticker</th><th class="num">Now</th><th class="num">Median</th>' +
        '<th class="num">Best</th><th class="num">Worst</th><th class="num">Quotes</th></tr></thead><tbody>' +
        payload.rows.map(function (row) {
          return '<tr><td class="tkcell">' + A.escapeHtml(row.ticker) + '</td>' +
            '<td class="num">' + A.pct(row.now_pct, 1) + '</td>' +
            '<td class="num">' + A.pct(row.median_pct, 1) + '</td>' +
            '<td class="num up">' + A.pct(row.best_pct, 1) + '</td>' +
            '<td class="num down">' + A.pct(row.worst_pct, 1) + '</td>' +
            '<td class="num">' + row.samples + '</td></tr>';
        }).join('') + '</tbody></table>';
    }).catch(function (error) { fail($('cost-chart'), error); });
  }

  /* ---- autopilot -------------------------------------------------------- */

  function renderAutopilot(payload) {
    state.autopilotMode = payload.mode;
    qa('#ap-mode button').forEach(function (button) {
      var on = button.dataset.mode === payload.mode;
      button.classList.toggle('on', on);
      button.classList.toggle('off', on && payload.mode === 'off');
    });
    $('ap-mode-sub').textContent = payload.armed_date
      ? 'armed for ' + payload.armed_date + ' · decides ' + payload.window.label
      : (payload.mode === 'continuous' ? 'may enter any day, whenever the guard rails pass' : '');
    $('ap-window').textContent = payload.market.open
      ? (payload.window.open ? 'window open · ' + payload.market.now_et : 'window ' + payload.window.label)
      : 'market closed';
    $('ap-window').className = 'badge num ' + (payload.window.open ? 'b-acc' : 'b-mut');

    var intents = payload.intents || [];
    $('ap-intents').innerHTML = intents.length
      ? intents.map(function (intent) {
          var badge = A.isNum(intent.confidence_pct)
            ? '<span class="badge ' + (intent.would_enter ? 'b-acc' : 'b-mut') + '">' +
              A.pct(intent.confidence_pct) + (intent.would_enter && A.isNum(intent.min_confidence_pct)
                ? ' ≥ ' + A.pct(intent.min_confidence_pct) : '') + '</span>'
            : '';
          return '<div class="intent' + (intent.would_enter ? ' armed' : '') + '">' +
            '<span class="tk">' + A.escapeHtml(intent.ticker) + '</span>' +
            '<div class="msg">' + intent.message + '</div>' +
            '<div class="rt">' + badge + '</div></div>';
        }).join('')
      : '<div class="empty">' + (payload.mode === 'off'
          ? 'Autopilot is off — no entries will be made.'
          : 'Market closed — autopilot stands down until the next session.') + '</div>';

    $('ap-config').innerHTML = payload.config.map(function (row) {
      return '<div class="cfg"><span>' + A.escapeHtml(row.label) + '</span><b class="num">' +
        A.escapeHtml(row.value) + '</b></div>';
    }).join('');

    var record = payload.record;
    if (!record.trades) {
      $('ap-record').innerHTML = '<div class="empty">No auto trades yet.</div>';
      $('ap-record-sub').textContent = '';
    } else {
      $('ap-record').innerHTML =
        '<div class="stat"><div class="n ' + A.tone(record.total_pnl) + '">' + A.signedMoney(record.total_pnl) + '</div><div class="l">Total</div></div>' +
        '<div class="stat"><div class="n">' + A.pct(record.win_rate_pct) + '</div><div class="l">Win rate</div></div>' +
        '<div class="stat"><div class="n">' + record.trades + '</div><div class="l">Trades</div></div>';
      $('ap-record-sub').textContent = 'Best ' + A.signedMoney(record.best) + ' · worst ' +
        A.signedMoney(record.worst) + ' · avg ' + A.signedMoney(record.avg) + '/trade · today ' +
        payload.today.trades_today + '/' + payload.today.trades_cap +
        (payload.today.circuit_breaker ? ' · circuit breaker TRIPPED' : '');
    }
  }

  function loadAutopilot() {
    return A.get('/api/autopilot').then(renderAutopilot)
      .catch(function (error) { fail($('ap-intents'), error); });
  }


  /* ---- ARMED / OPENED alerts ------------------------------------------ */

  /* Edge-triggered from the header poll, so an alert fires on whichever page
     is open - the whole point is catching it in time to mirror the trade. */
  var alerts = { sound: false, notify: false, push: false, ready: false, armed: [], openIds: [] };

  function loadAlertPrefs() {
    try {
      alerts.sound = localStorage.getItem('0dte-alert-sound') === '1';
      alerts.notify = localStorage.getItem('0dte-alert-notify') === '1';
    } catch (e) { /* ignore */ }
    renderAlertButtons();
  }

  function renderAlertButtons() {
    $('alert-sound').textContent = 'Sound: ' + (alerts.sound ? 'on' : 'off');
    $('alert-sound').classList.toggle('ghost', !alerts.sound);
    $('alert-notify').textContent = 'Notifications: ' +
      (alerts.notify ? (alerts.push ? 'on · push' : 'on · while open') : 'off');
    $('alert-notify').classList.toggle('ghost', !alerts.notify);
  }

  function beep() {
    if (!alerts.sound) { return; }
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) { return; }
      var ctx = new Ctx();
      [0, 0.18].forEach(function (offset) {
        var osc = ctx.createOscillator(), gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.value = 880;
        gain.gain.setValueAtTime(0.0001, ctx.currentTime + offset);
        gain.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + offset + 0.01);
        gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + offset + 0.14);
        osc.connect(gain); gain.connect(ctx.destination);
        osc.start(ctx.currentTime + offset);
        osc.stop(ctx.currentTime + offset + 0.16);
      });
      setTimeout(function () { ctx.close(); }, 800);
    } catch (e) { /* audio blocked until the page has been interacted with */ }
  }

  function systemNotification(title, message) {
    if (!alerts.notify || !window.Notification || Notification.permission !== 'granted') { return; }
    if (alerts.push && title !== 'Test alert') { return; }   // the server's push shows this one
    var options = { body: message, tag: title + message,
                    icon: '/icons/icon-192.png', badge: '/icons/icon-192.png' };
    function direct() { try { new Notification(title, options); } catch (e) { /* no-op */ } }
    // Android only shows notifications through the service worker; desktop
    // browsers accept either, so the worker is tried first everywhere.
    if (navigator.serviceWorker && navigator.serviceWorker.getRegistration) {
      navigator.serviceWorker.getRegistration().then(function (registration) {
        if (registration) { registration.showNotification(title, options); } else { direct(); }
      }).catch(direct);
    } else {
      direct();
    }
  }

  function fireAlert(title, message) {
    toast('<b>' + A.escapeHtml(title) + '</b> — ' + A.escapeHtml(message), 'good');
    beep();
    systemNotification(title, message);
  }

  function base64UrlToBytes(value) {
    var padded = value + '==='.slice((value.length + 3) % 4);
    var raw = atob(padded.replace(/-/g, '+').replace(/_/g, '/'));
    var bytes = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i += 1) { bytes[i] = raw.charCodeAt(i); }
    return bytes;
  }

  function sameKey(buffer, bytes) {
    if (!buffer) { return false; }
    var existing = new Uint8Array(buffer);
    if (existing.length !== bytes.length) { return false; }
    for (var i = 0; i < bytes.length; i += 1) { if (existing[i] !== bytes[i]) { return false; } }
    return true;
  }

  /* Subscribe this device to the server's Web Push, so ARMED / OPENED arrive
     with the app closed. Resolves true when push is live, false when it isn't
     available here (then notifications still work while the app is open). A
     subscription made under an old server key is replaced, not reused. */
  var PUSH_TIMEOUT_MS = 15000;

  function enablePush() {
    if (!navigator.serviceWorker) { return Promise.resolve(false); }
    // Registering with the push service can stall indefinitely when it's
    // unreachable; give up after a while and fall back to in-app alerts.
    var timeout = new Promise(function (resolve) {
      setTimeout(function () { resolve(false); }, PUSH_TIMEOUT_MS);
    });
    return Promise.race([subscribePush(), timeout]);
  }

  function subscribePush() {
    return A.get('/api/push/key').then(function (info) {
      if (!info.available) { return false; }
      var key = base64UrlToBytes(info.public_key);
      return navigator.serviceWorker.ready.then(function (registration) {
        if (!registration.pushManager) { return false; }
        return registration.pushManager.getSubscription().then(function (existing) {
          if (existing && sameKey(existing.options && existing.options.applicationServerKey, key)) {
            return existing;
          }
          var cleared = existing ? existing.unsubscribe() : Promise.resolve();
          return cleared.then(function () {
            return registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
          });
        }).then(function (subscription) {
          return A.post('/api/push/subscribe', { subscription: subscription.toJSON() })
            .then(function () { return true; });
        });
      });
    }).catch(function (error) {
      console.warn('push subscription failed', error);
      return false;
    });
  }

  function disablePush() {
    if (!navigator.serviceWorker) { return Promise.resolve(); }
    return navigator.serviceWorker.ready.then(function (registration) {
      if (!registration.pushManager) { return null; }
      return registration.pushManager.getSubscription();
    }).then(function (subscription) {
      if (!subscription) { return null; }
      var endpoint = subscription.endpoint;
      return subscription.unsubscribe().then(function () {
        return A.post('/api/push/unsubscribe', { endpoint: endpoint });
      });
    }).catch(function (error) { console.warn('push unsubscribe failed', error); });
  }

  function isIOS() {
    return /iPad|iPhone|iPod/.test(navigator.userAgent) ||
      (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  }

  function isInstalled() {
    return window.navigator.standalone === true ||
      (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches);
  }

  /* Why notifications can't be turned on here, or null if they can. The two
     phone-specific causes get their own message, since the fix differs. */
  function notificationBlocker() {
    if (!window.isSecureContext) {
      return 'Notifications need HTTPS. Open the app at its https://….ts.net address ' +
        '(see SETUP.md, "tailscale serve"). The sound alert works without it.';
    }
    if (isIOS() && !isInstalled()) {
      return 'On iPhone, first tap Share → Add to Home Screen, then turn notifications on ' +
        'from the installed app.';
    }
    if (!window.Notification) { return 'This browser has no notification support.'; }
    return null;
  }

  function checkAlerts(autopilot) {
    var armed = (autopilot.armed || []).map(function (row) { return row.ticker; });
    var openIds = (autopilot.auto_open || []).map(function (row) { return row.id; });
    if (alerts.ready) {
      (autopilot.armed || []).forEach(function (row) {
        if (alerts.armed.indexOf(row.ticker) < 0) {
          fireAlert(row.ticker + ' ARMED',
            'would buy ' + row.lean + 's now (' + A.pct(row.confidence_pct) +
            ') — mirror ATM 0DTE ' + row.lean + '.');
        }
      });
      (autopilot.auto_open || []).forEach(function (row) {
        if (alerts.openIds.indexOf(row.id) < 0) {
          fireAlert(row.ticker + ' OPENED',
            row.option_type + ' ' + row.strike + ' just opened — copy now.');
        }
      });
    }
    alerts.armed = armed;
    alerts.openIds = openIds;
    alerts.ready = true;
  }

  /* ---- wallet settings -------------------------------------------------- */

  function wireWalletSettings() {
    $('wallet-settings-btn').addEventListener('click', function () {
      var panel = $('wallet-settings');
      panel.hidden = !panel.hidden;
      this.setAttribute('aria-expanded', String(!panel.hidden));
      if (!panel.hidden && !$('bal-input').value) { $('bal-input').value = state.balance || 0; }
    });

    function setBalance(value) {
      A.post('/api/wallet/balance', { balance: value }).then(function (wallet) {
        state.balance = wallet.balance;
        $('bal-input').value = wallet.balance;
        toast('Balance set to ' + A.money(wallet.balance, 2), 'good');
        loadOverview();
      }).catch(function (error) { toast(A.escapeHtml(error.message), 'bad'); });
    }

    $('bal-set').addEventListener('click', function () {
      setBalance(Number($('bal-input').value || 0));
    });
    $('bal-reset').addEventListener('click', function () {
      setBalance(state.startingBalance || 10000);
    });
    $('hist-clear').addEventListener('click', function () {
      if (!window.confirm('Delete every closed trade record? Open positions and the balance are untouched.')) { return; }
      A.post('/api/wallet/clear-history', {}).then(function (payload) {
        toast('Trade history cleared.', 'good');
        loadTradeHistory();
        loadCalendar();
      }).catch(function (error) { toast(A.escapeHtml(error.message), 'bad'); });
    });
  }

  /* ---- tuning page ------------------------------------------------------ */

  function accuracyTable(rows, firstHeader, cells) {
    if (!rows.length) { return '<div class="empty" style="padding:0 17px 16px">Not enough graded calls yet.</div>'; }
    return '<table><thead><tr><th>' + firstHeader + '</th>' +
      cells.map(function (cell) { return '<th class="num">' + cell.header + '</th>'; }).join('') +
      '</tr></thead><tbody>' + rows.map(function (row) {
        return '<tr><td>' + cells[0].first(row) + '</td>' +
          cells.map(function (cell) { return '<td class="num">' + cell.value(row) + '</td>'; }).join('') +
          '</tr>';
      }).join('') + '</tbody></table>';
  }

  function renderCalibration(payload) {
    state.calibrationEnabled = payload.enabled;
    qa('#cal-toggle button').forEach(function (button) {
      var on = (button.dataset.enabled === '1') === payload.enabled;
      button.classList.toggle('on', on);
      button.classList.toggle('no', on && !payload.enabled);
    });
    $('cal-status').innerHTML =
      'Last run: <b>' + A.escapeHtml(payload.last_run || 'never') + '</b> · learning rate ' +
      A.pct(payload.learning_rate_pct) + '/day · graded ' + payload.graded_count +
      ' calls at ' + payload.horizon_minutes + ' min' + sinceText(payload) +
      (A.isNum(payload.overall_accuracy_pct) ? ' (' + A.pct(payload.overall_accuracy_pct) + ' right)' : '') +
      (A.isNum(payload.window_days) ? ' · signals over ' + payload.window_days + ' days' : '') +
      ' · auto-inverted for ' + A.escapeHtml(payload.ticker) + ': ' +
      (payload.inversions.length
        ? payload.inversions.map(function (row) { return A.escapeHtml(row.name); }).join(', ')
        : 'none');

    $('cal-events').innerHTML = payload.events.length
      ? '<table><thead><tr><th>When</th><th>Ticker</th><th>Action</th><th>Detail</th></tr></thead><tbody>' +
        payload.events.map(function (event) {
          return '<tr><td class="num">' + A.escapeHtml(event.when) + '</td>' +
            '<td class="tkcell">' + A.escapeHtml(event.ticker) + '</td>' +
            '<td>' + A.escapeHtml(event.action) + '</td>' +
            '<td>' + A.escapeHtml(event.detail) + '</td></tr>';
        }).join('') + '</tbody></table>'
      : '<div class="empty" style="padding:0 17px 16px">No calibration adjustments yet.</div>';

    $('cal-bands').innerHTML = accuracyTable(payload.confidence_bands, 'Predicted', [
      { header: 'Graded', first: function (row) { return A.escapeHtml(row.band); },
        value: function (row) { return row.count; } },
      { header: 'Observed', value: function (row) { return A.pct(row.observed_accuracy_pct); } }
    ]);

    $('cal-categories').innerHTML = accuracyTable(payload.categories, 'Signal', [
      { header: 'Weight',
        first: function (row) {
          return '<span title="' + A.escapeHtml(row.description) + '">' + A.escapeHtml(row.name) +
            (row.inverted ? ' <span class="badge b-mut">inverted</span>' : '') + '</span>';
        },
        value: function (row) { return A.pct(row.weight_pct); } },
      { header: 'Graded', value: function (row) { return row.graded; } },
      { header: 'Independent', value: function (row) { return row.independent === null || row.independent === undefined ? '—' : row.independent; } },
      { header: 'Accuracy', value: function (row) { return A.pct(row.accuracy_pct); } }
    ]);

    var context = payload.context;
    $('cal-context').innerHTML = [
      ['By time of day (ET)', context.time_of_day],
      ['By volatility regime', context.volatility],
      ['By signal persistence', context.streak]
    ].map(function (pair) {
      var rows = pair[1];
      return '<div class="ctx"><h4>' + A.escapeHtml(pair[0]) + '</h4>' +
        (rows.length
          ? rows.map(function (row) {
              return '<div class="row"><span>' + A.escapeHtml(row.label) + ' · ' + row.graded +
                ' graded</span><b>' + A.pct(row.accuracy_pct) + '</b></div>';
            }).join('')
          : '<div class="row"><span>Not enough graded calls yet.</span></div>') + '</div>';
    }).join('');

    var suggestion = payload.suggestion;
    $('weights-apply').disabled = !suggestion;
    $('weights-revert').disabled = !(suggestion && suggestion.applied_at);
    if (!suggestion) {
      $('cal-weights').innerHTML = '<div class="empty">No category has ' + payload.min_graded +
        ' independent graded calls yet — weights stay at the config defaults until one does.</div>';
      $('cal-weights-sub').textContent = '';
    } else {
      $('cal-weights').innerHTML =
        '<table><thead><tr><th>Signal</th><th class="num">Current</th><th class="num">Suggested</th></tr></thead><tbody>' +
        suggestion.rows.map(function (row) {
          var delta = row.suggested_pct - row.current_pct;
          return '<tr><td>' + A.escapeHtml(row.name) + '</td>' +
            '<td class="num">' + A.pct(row.current_pct) + '</td>' +
            '<td class="num ' + A.tone(delta) + '">' + A.pct(row.suggested_pct) + '</td></tr>';
        }).join('') + '</tbody></table>';
      $('cal-weights-sub').textContent = 'Applies to ' + payload.ticker + ' only — each ticker ' +
        'keeps its own weights. Currently: ' + suggestion.source +
        (suggestion.applied_at ? ' (' + suggestion.applied_at.slice(0, 16).replace('T', ' ') + ' UTC)' : '') +
        '. The worker picks changes up on its next cycle.';
    }

    var candidates = payload.inversion_candidates || [];
    $('cal-candidates').hidden = !candidates.length;
    if (candidates.length) {
      $('cal-candidates').innerHTML = '<b>Consider inverting:</b> ' +
        candidates.map(function (row) { return A.escapeHtml(row.name); }).join(', ') +
        ' — reliably wrong over enough graded calls that the opposite of the call has been ' +
        'the better bet. Self-calibration does this on its own when it is on.';
    }
  }

  function loadCalibration() {
    if (!state.ticker) { return Promise.resolve(); }
    return A.get('/api/calibration/' + state.ticker).then(renderCalibration)
      .catch(function (error) { fail($('cal-events'), error); });
  }

  function wireTuning() {
    $('cal-toggle').addEventListener('click', function (event) {
      var button = event.target.closest('button[data-enabled]');
      if (!button) { return; }
      var enabled = button.dataset.enabled === '1';
      if (enabled === state.calibrationEnabled) { return; }
      A.post('/api/calibration/enabled', { enabled: enabled }).then(function () {
        toast('Auto-calibration ' + (enabled ? 'on' : 'off') + '.', 'good');
        loadCalibration();
      }).catch(function (error) { toast(A.escapeHtml(error.message), 'bad'); });
    });

    $('cal-revert').addEventListener('click', function () {
      if (!window.confirm('Clear every auto-applied weight override, inversion and confidence map, for all tickers?')) { return; }
      A.post('/api/calibration/revert', {}).then(function () {
        toast('Auto-calibration reverted for all tickers.', 'good');
        loadCalibration();
      }).catch(function (error) { toast(A.escapeHtml(error.message), 'bad'); });
    });

    function weights(action) {
      A.post('/api/weights/' + state.ticker, { action: action }).then(function (payload) {
        renderCalibration(payload);
        toast(action === 'apply' ? 'Weights applied to ' + state.ticker + '.'
          : state.ticker + ' reverted to config defaults.', 'good');
      }).catch(function (error) { toast(A.escapeHtml(error.message), 'bad'); });
    }
    $('weights-apply').addEventListener('click', function () { weights('apply'); });
    $('weights-revert').addEventListener('click', function () { weights('revert'); });
  }

  function wireAlerts() {
    $('alert-sound').addEventListener('click', function () {
      alerts.sound = !alerts.sound;
      try { localStorage.setItem('0dte-alert-sound', alerts.sound ? '1' : '0'); } catch (e) { /* ignore */ }
      renderAlertButtons();
      if (alerts.sound) { beep(); }   // also unlocks audio for later, unprompted alerts
    });

    $('alert-notify').addEventListener('click', function () {
      var blocker = notificationBlocker();
      if (blocker) { toast(A.escapeHtml(blocker), 'bad'); return; }
      if (alerts.notify) {
        alerts.notify = false;
        alerts.push = false;
        disablePush();
      } else {
        Notification.requestPermission().then(function (permission) {
          alerts.notify = permission === 'granted';
          if (!alerts.notify) { toast('Notifications were blocked in the browser.', 'bad'); }
          try { localStorage.setItem('0dte-alert-notify', alerts.notify ? '1' : '0'); } catch (e) { /* ignore */ }
          renderAlertButtons();
          if (!alerts.notify) { return; }
          enablePush().then(function (live) {
            alerts.push = live;
            renderAlertButtons();
            toast(live ? 'Notifications on — pushed to this device even with the app closed.'
              : 'Notifications on while the app is open (push isn\'t available here).', 'good');
          });
        });
        return;
      }
      try { localStorage.setItem('0dte-alert-notify', '0'); } catch (e) { /* ignore */ }
      renderAlertButtons();
    });

    $('alert-test').addEventListener('click', function () {
      if (!alerts.push) {
        fireAlert('Test alert', 'if you heard a sound (and saw a popup, if enabled), you are set.');
        return;
      }
      // local toast + sound, and the notification itself via the push service,
      // which proves the whole server -> phone chain
      toast('<b>Test alert</b> — sent through the push service…', 'good');
      beep();
      A.post('/api/push/test', {}).then(function (result) {
        toast('Pushed to ' + result.sent + ' device' + (result.sent === 1 ? '' : 's') +
          (result.failed ? ' · ' + result.failed + ' failed' : ''), result.sent ? 'good' : 'bad');
      }).catch(function (error) { toast(A.escapeHtml(error.message), 'bad'); });
    });
  }

  /* ---- live updates: SSE, with the poll as a fallback ------------------- */

  function connectStream() {
    if (!window.EventSource) { return; }
    var source;
    try { source = new EventSource('/api/stream'); } catch (e) { return; }
    source.addEventListener('changed', function () {
      state.streaming = true;
      loadOverview().then(loadPage);
    });
    source.onopen = function () { state.streaming = true; };
    source.onerror = function () {
      // EventSource retries on its own; the slow poll covers the gap meanwhile
      state.streaming = false;
    };
  }

  /* ---- wiring ----------------------------------------------------------- */

  function wire() {
    $('nav').addEventListener('click', function (event) {
      var link = event.target.closest('a[data-p]');
      if (!link) { return; }
      event.preventDefault();
      showPage(link.dataset.p);
      window.scrollTo({ top: 0, behavior: 'instant' });
    });

    $('senti').addEventListener('click', function (event) {
      var button = event.target.closest('button[data-tk]');
      if (!button) { return; }
      selectTicker(button.dataset.tk);
      if (state.page !== 'cost') {
        showPage('signals');
        window.scrollTo({ top: 0, behavior: 'instant' });
      }
    });

    $('range-pills').addEventListener('click', function (event) {
      var button = event.target.closest('button[data-range]');
      if (!button) { return; }
      state.range = button.dataset.range;
      qa('#range-pills button').forEach(function (b) { b.classList.toggle('on', b === button); });
      loadHistory();
    });

    $('pt-buy').addEventListener('click', placeTrade);
    $('pt-type').addEventListener('change', function () {
      state.pendingAmountEdit = true;   // stop the poll from re-picking the lean
      refreshQuote(true);
    });
    $('pt-amount').addEventListener('input', function () {
      state.pendingAmountEdit = true;
      refreshQuote(true);
    });
    $('pt-chips').addEventListener('click', function (event) {
      var button = event.target.closest('button');
      if (!button) { return; }
      var amount = button.dataset.amt
        ? Number(button.dataset.amt)
        : Math.round((state.balance || 0) * Number(button.dataset.pct) / 100);
      $('pt-amount').value = amount;
      state.pendingAmountEdit = true;
      refreshQuote(true);
    });

    $('positions').addEventListener('click', function (event) {
      var button = event.target.closest('.closebtn');
      if (!button) { return; }
      var id = button.closest('tr').dataset.id;
      button.disabled = true;
      A.post('/api/position/' + id + '/close', {}).then(function (result) {
        toast(A.escapeHtml(result.message), 'good');
        renderPositions(result.positions);
        loadOverview();
        loadTradeHistory();
        loadCalendar();
      }).catch(function (error) {
        toast(A.escapeHtml(error.message), 'bad');
        button.disabled = false;
      });
    });

    $('positions').addEventListener('change', function (event) {
      var select = event.target.closest('select[data-target]');
      if (!select) { return; }
      var row = select.closest('tr');
      var value = function (kind) {
        var node = q('select[data-target="' + kind + '"]', row);
        return node.value === '' ? null : Number(node.value);
      };
      A.post('/api/position/' + row.dataset.id + '/targets', {
        profit_target_pct: value('profit'), stop_loss_pct: value('stop')
      }).then(function () {
        select.classList.toggle('set', select.value !== '');
        toast('Exit targets updated.', 'good');
      }).catch(function (error) { toast(A.escapeHtml(error.message), 'bad'); });
    });

    $('ap-mode').addEventListener('click', function (event) {
      var button = event.target.closest('button[data-mode]');
      if (!button || button.dataset.mode === state.autopilotMode) { return; }
      A.post('/api/autopilot/mode', { mode: button.dataset.mode }).then(function (payload) {
        renderAutopilot(payload);
        loadOverview();
        toast('Autopilot: ' + A.escapeHtml(button.textContent), 'good');
      }).catch(function (error) { toast(A.escapeHtml(error.message), 'bad'); });
    });

    $('cal-prev').addEventListener('click', function () { shiftMonth(-1); });
    $('cal-next').addEventListener('click', function () { shiftMonth(1); });
    $('cal-now').addEventListener('click', function () {
      state.calendar = { year: null, month: null };
      loadCalendar();
    });
  }

  function boot() {
    initTheme();
    wire();
    wireWalletSettings();
    wireTuning();
    wireAlerts();
    loadAlertPrefs();
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/sw.js').catch(function (error) {
        console.warn('service worker not registered', error);
      });
      // a tapped notification asks an already-open window to switch page
      navigator.serviceWorker.addEventListener('message', function (event) {
        if (event.data && event.data.page && q('.page[data-page="' + event.data.page + '"]')) {
          showPage(event.data.page);
        }
      });
      if (alerts.notify && window.Notification && Notification.permission === 'granted') {
        enablePush().then(function (live) { alerts.push = live; renderAlertButtons(); });
      }
    }
    try {
      state.ticker = localStorage.getItem('0dte-ticker') || null;
      var page = localStorage.getItem('0dte-page');
      if (page && q('.page[data-page="' + page + '"]')) { state.page = page; }
    } catch (e) { /* ignore */ }
    var linked = window.location.hash.replace('#', '');
    if (linked && q('.page[data-page="' + linked + '"]')) {
      state.page = linked;
      history.replaceState(null, '', window.location.pathname + window.location.search);
    }
    if (state.ticker) { selectTicker(state.ticker, true); }
    showPage(state.page, true);
    connectStream();
    // With the stream connected this is only a safety net; without it, it is
    // the update mechanism.
    A.poll(function () {
      if (state.streaming && Date.now() - (state.lastPoll || 0) < SLOW_POLL_MS) { return; }
      state.lastPoll = Date.now();
      loadOverview().then(loadPage);
    }, POLL_MS);
  }

  boot();
})();
