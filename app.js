(() => {
  'use strict';

  /* ==========================================================================
     Core helpers: pure functions with no DOM access.
     All money is integer cents. The server is the only source of balances.
     ========================================================================== */
  const MAX_CENTS = 100000000000; // $1,000,000,000.00, same bound as the API
  const MIN_MONTH = '2000-01';
  const MAX_MONTH = '2100-12';
  const currency = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });

  const formatCents = cents => currency.format((Number(cents) || 0) / 100);

  function parseMoneyToCents(raw) {
    const text = String(raw == null ? '' : raw).trim().replace(/^\$/, '').replace(/,/g, '');
    const match = /^(\d{1,10})?(?:\.(\d{1,2}))?$/.exec(text);
    if (!match || (match[1] === undefined && match[2] === undefined)) return null;
    const cents = Number(match[1] || '0') * 100 + Number((match[2] || '').padEnd(2, '0') || '0');
    return cents <= MAX_CENTS ? cents : null;
  }

  function centsToInput(cents) {
    const n = Math.abs(Number(cents) || 0);
    return `${cents < 0 ? '-' : ''}${Math.floor(n / 100)}.${String(n % 100).padStart(2, '0')}`;
  }

  const isMonthKey = value => /^\d{4}-(0[1-9]|1[0-2])$/.test(value || '');

  function currentMonthKey(now = new Date()) {
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  }

  function shiftMonth(key, delta) {
    const [y, m] = key.split('-').map(Number);
    const index = y * 12 + (m - 1) + delta;
    return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}`;
  }

  function clampMonth(key) {
    if (!isMonthKey(key)) return MIN_MONTH;
    return key < MIN_MONTH ? MIN_MONTH : key > MAX_MONTH ? MAX_MONTH : key;
  }

  function monthLabel(key) {
    const [y, m] = key.split('-').map(Number);
    return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });
  }

  function defaultTransactionDate(monthKey, now = new Date()) {
    const today = `${currentMonthKey(now)}-${String(now.getDate()).padStart(2, '0')}`;
    return today.startsWith(monthKey) ? today : `${monthKey}-01`;
  }

  function formatDay(iso) {
    return new Date(`${iso}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }

  function targetLine(category) {
    const parts = [];
    if (category.target_text) parts.push(category.target_text);
    if (category.carry_in_cents > 0) parts.push(`${formatCents(category.carry_in_cents)} carried over`);
    return parts.join(' · ');
  }

  function readyMessage(summary) {
    const ready = summary.ready_to_assign_cents;
    if (ready > 0) return 'Give this money a job.';
    if (ready < 0) {
      return summary.prior_overspent_absorbed_cents > 0
        ? 'Earlier overspending is reducing this amount. Add income or reduce an assignment.'
        : 'Reduce an assignment to balance the plan.';
    }
    return summary.cumulative_income_cents === 0 ? 'Add income to start budgeting.' : 'Every dollar has a job.';
  }

  function flatCategories(monthData) {
    return monthData ? monthData.groups.flatMap(g => g.categories.map(c => ({ ...c, group_name: g.name }))) : [];
  }

  // Suggest a source (most money) and destination (least money) for the Move dialog.
  function pickMoveDefaults(categories) {
    if (categories.length < 2) return { from: categories[0]?.id, to: undefined };
    const byAvailable = [...categories].sort((a, b) => b.available_cents - a.available_cents);
    const from = byAvailable[0];
    const to = [...categories].filter(c => c.id !== from.id).sort((a, b) => a.available_cents - b.available_cents)[0];
    return { from: from.id, to: to.id };
  }

  class ApiError extends Error {
    constructor(status, code, message, details, retryAfter) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
      this.code = code;
      this.details = details;
      this.retryAfter = retryAfter;
    }
  }

  function describeError(err, ctx = {}) {
    if (!err || !(err instanceof ApiError)) return 'Something unexpected happened. Please try again.';
    const d = err.details;
    switch (err.code) {
      case 'network': return "Can't reach ClearPocket. Check your connection and try again.";
      case 'rate_limited': return 'Too many attempts. Wait a few minutes, then try again.';
      case 'over_allocation':
        return `Only ${formatCents(Math.max(0, d && d.ready_to_assign_cents || 0))} is ready to assign.`;
      case 'insufficient_funds':
        return `${ctx.name || 'That category'} only has ${formatCents(Math.max(0, d && d.available_cents || 0))} available.`;
      case 'version_conflict':
        return 'This amount was changed somewhere else. The latest numbers are shown; please review and try again.';
      case 'csrf_failed': return 'Your session needed a refresh. Please try again.';
      case 'validation_error':
        if (Array.isArray(d) && d.length) return d.map(x => x.message).filter(Boolean).join(' ');
        return err.message;
      case 'internal_error': return 'Something went wrong on our side. Please try again.';
      default: return err.message || 'Something went wrong. Please try again.';
    }
  }

  // Thin fetch wrapper: same-origin cookies, CSRF header on writes, safe error envelope.
  function createApiClient({ fetchImpl, getCsrf, getEpoch = () => 0, onUnauthorized }) {
    async function request(method, path, body, { auth = true } = {}) {
      const epoch = getEpoch();
      const headers = { Accept: 'application/json' };
      if (body !== undefined) headers['Content-Type'] = 'application/json';
      if (method !== 'GET') {
        const token = getCsrf();
        if (token) headers['X-CSRF-Token'] = token;
      }
      let response;
      try {
        response = await fetchImpl(path, {
          method, headers, credentials: 'same-origin', cache: 'no-store',
          body: body === undefined ? undefined : JSON.stringify(body)
        });
      } catch (_) {
        throw new ApiError(0, 'network', 'Network error');
      }
      let data = null;
      if (response.status !== 204) {
        try { data = await response.json(); } catch (_) { data = null; }
      }
      if (response.ok) return data;
      const e = (data && data.error) || {};
      const retry = response.headers && response.headers.get ? response.headers.get('Retry-After') : undefined;
      const err = new ApiError(response.status, e.code || 'http_error', e.message || 'Request failed.', e.details, retry);
      if (response.status === 401 && err.code === 'unauthenticated' && auth && onUnauthorized) onUnauthorized(err, epoch);
      throw err;
    }
    return {
      get: (path, opts) => request('GET', path, undefined, opts),
      post: (path, body, opts) => request('POST', path, body, opts),
      put: (path, body, opts) => request('PUT', path, body, opts),
      patch: (path, body, opts) => request('PATCH', path, body, opts),
      del: (path, opts) => request('DELETE', path, undefined, opts)
    };
  }

  const Core = {
    MAX_CENTS, formatCents, parseMoneyToCents, centsToInput, isMonthKey, currentMonthKey, shiftMonth,
    clampMonth, monthLabel, defaultTransactionDate, formatDay, targetLine, readyMessage, flatCategories,
    pickMoveDefaults, ApiError, describeError, createApiClient
  };

  if (typeof document === 'undefined') { globalThis.ClearPocketCore = Core; return; }

  /* ==========================================================================
     Browser application
     ========================================================================== */
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  // Safe element builder: user content only ever goes through textContent / attributes.
  function h(tag, props = {}, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value == null || value === false) continue;
      if (key === 'class') el.className = value;
      else if (key === 'text') el.textContent = value;
      else if (key === 'dataset') Object.assign(el.dataset, value);
      else el.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of children.flat()) {
      if (child == null || child === false) continue;
      el.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return el;
  }

  const APP_VIEWS = ['budget', 'transactions', 'guide'];
  const PAGE_SIZE = 50;
  const freshTx = (query = '') => ({ items: [], total: 0, status: 'idle', error: '', query, monthTotal: null, loadingMore: false });

  const state = {
    screen: 'marketing',
    view: 'budget',
    user: null,
    csrf: null,
    epoch: 0,
    month: currentMonthKey(),
    monthData: null,
    monthStatus: 'idle', // idle | loading | refreshing | ready | error
    monthError: '',
    tx: freshTx(),
    collapsed: new Set(),
    afterAuth: null,
    lastSessionCheck: 0,
    focusAfterRender: null
  };
  let monthSeq = 0;
  let txSeq = 0;
  let toastTimer;
  let searchTimer;
  let theme = 'light';

  const api = createApiClient({
    fetchImpl: (...args) => window.fetch(...args),
    getCsrf: () => state.csrf,
    getEpoch: () => state.epoch,
    onUnauthorized: (_err, startedEpoch) => { if (startedEpoch === state.epoch) expireSession(); }
  });

  /* ---------- theme & toast ---------- */
  function applyTheme(next, persist = false) {
    theme = next;
    document.documentElement.dataset.theme = theme;
    $$('[data-theme-toggle]').forEach(btn => {
      btn.setAttribute('aria-label', `Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`);
      btn.querySelector('span').textContent = theme === 'dark' ? '☀' : '◐';
    });
    if (persist) { try { localStorage.setItem('clearpocket-theme', theme); } catch (_) { /* optional preference only */ } }
  }

  function initialTheme() {
    try { const saved = localStorage.getItem('clearpocket-theme'); if (saved === 'dark' || saved === 'light') return saved; } catch (_) { /* ignore */ }
    return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function showToast(message) {
    const toast = $('#toast');
    toast.textContent = message;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('show'), 4200);
  }

  /* ---------- screens & routing ---------- */
  function setScreen(name) {
    state.screen = name;
    $('#marketingView').hidden = name !== 'marketing';
    $('#authView').hidden = name !== 'auth';
    $('#appView').hidden = name !== 'app';
    const skip = $('.skip-link');
    skip.setAttribute('href', name === 'app' ? '#appMain' : name === 'auth' ? '#authMain' : '#main');
    document.title = name === 'app' ? 'ClearPocket — Your budget' : name === 'auth' ? 'ClearPocket — Sign in' : 'ClearPocket — Give every dollar a direction';
    closeSidebar();
    window.scrollTo(0, 0);
  }

  function refreshAuthUi() {
    const user = state.user;
    document.body.classList.toggle('is-signed-in', Boolean(user));
    $$('[data-signed-out-only]').forEach(el => { el.hidden = Boolean(user); });
    $$('[data-auth-cta]').forEach(el => {
      if (!el.dataset.label) el.dataset.label = el.textContent;
      el.textContent = user ? 'Open your budget' : el.dataset.label;
    });
    $('#userName').textContent = user ? (user.display_name || user.email.split('@')[0]) : '';
    $('#userEmail').textContent = user ? user.email : '';
  }

  function showMarketing() {
    setScreen('marketing');
    history.replaceState(null, '', '#home');
  }

  function showAuth(mode, { notice = '', info = false } = {}) {
    setScreen('auth');
    $('#loginForm').hidden = mode !== 'login';
    $('#registerForm').hidden = mode !== 'register';
    $('#authKicker').textContent = mode === 'login' ? 'Welcome back' : 'Start budgeting';
    $('#authHeading').textContent = mode === 'login' ? 'Sign in' : 'Create your account';
    const note = $('#authNotice');
    note.textContent = notice;
    note.hidden = !notice;
    note.classList.toggle('is-info', info);
    $('#loginError').textContent = '';
    $('#registerError').textContent = '';
    history.replaceState(null, '', mode === 'login' ? '#signin' : '#register');
    (mode === 'login' ? $('#loginEmail') : $('#registerName')).focus();
  }

  function enterApp(view = 'budget') {
    setScreen('app');
    switchView(view);
    if (state.monthStatus === 'idle') loadBudget();
  }

  function route() {
    const hash = location.hash.slice(1);
    if (APP_VIEWS.includes(hash)) {
      if (!state.user) { state.afterAuth = hash; showAuth('login', { notice: 'Sign in to open your budget.', info: true }); return; }
      enterApp(hash);
    } else if (hash === 'signin' || hash === 'register') {
      if (state.user) enterApp('budget'); else showAuth(hash === 'signin' ? 'login' : 'register');
    } else if (hash === 'home' && state.screen !== 'marketing') {
      showMarketing();
    }
  }

  function switchView(view) {
    state.view = view;
    const views = { budget: $('#budgetView'), transactions: $('#transactionsView'), guide: $('#guideView') };
    Object.entries(views).forEach(([key, element]) => { element.hidden = key !== view; });
    $$('.nav-item[data-view]').forEach(item => {
      item.classList.toggle('active', item.dataset.view === view);
      if (item.dataset.view === view) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current');
    });
    renderTitle();
    history.replaceState(null, '', `#${view}`);
    closeSidebar();
  }

  function renderTitle() {
    const titles = { budget: monthLabel(state.month), transactions: 'Transactions', guide: 'Quick guide' };
    $('#viewTitle').textContent = titles[state.view];
  }

  /* ---------- private-state lifecycle ---------- */
  function clearPrivateState() {
    state.epoch += 1; // invalidates every in-flight response from the previous session
    state.user = null;
    state.csrf = null;
    state.monthData = null;
    state.monthStatus = 'idle';
    state.monthError = '';
    state.tx = freshTx();
    state.collapsed.clear();
    state.month = currentMonthKey();
    state.focusAfterRender = null;
    monthSeq += 1;
    txSeq += 1;
    clearTimeout(searchTimer);
    $$('dialog').forEach(d => { if (d.open) d.close(); });
    $$('dialog form, .auth-form').forEach(f => f.reset());
    $$('.form-error, .form-hint').forEach(el => { if (!el.closest('#registerForm')) el.textContent = ''; });
    ['#moveFrom', '#moveTo', '#assignCategory', '#transactionCategory', '#categoryGroup'].forEach(sel => { $(sel).replaceChildren(); });
    $('#categoryGroups').replaceChildren();
    $('#transactionRows').replaceChildren();
    $('#categorySearch').value = '';
    $('#transactionSearch').value = '';
    $('#navTxCount').hidden = true;
    $('#navTxCount').textContent = '';
    refreshAuthUi();
    renderAll();
  }

  function expireSession() {
    if (!state.user) return;
    clearPrivateState();
    state.afterAuth = null;
    showAuth('login', { notice: 'Your session expired. Sign in again to continue.' });
  }

  async function revalidateSession() {
    if (!state.user) return;
    state.lastSessionCheck = Date.now();
    const epoch = state.epoch;
    try {
      const session = await api.get('/api/auth/session', { auth: false });
      if (epoch !== state.epoch) return;
      if (!session.authenticated) { expireSession(); return; }
      if (session.user.id !== state.user.id) {
        const view = state.view;
        clearPrivateState();
        state.user = session.user;
        state.csrf = session.csrf_token;
        refreshAuthUi();
        enterApp(view);
        showToast(`Your account changed in another tab. Now showing ${session.user.email}.`);
      } else {
        state.csrf = session.csrf_token;
      }
    } catch (_) { /* offline: keep current view; the next request will report */ }
  }

  async function completeAuth(data) {
    if (state.user && state.user.id !== data.user.id) clearPrivateState();
    state.user = data.user;
    state.csrf = data.csrf_token;
    refreshAuthUi();
    const target = state.afterAuth || 'budget';
    state.afterAuth = null;
    enterApp(target);
    $('#viewTitle').focus({ preventScroll: true });
  }

  async function logout() {
    const button = $('[data-logout]');
    if (button.dataset.busy) return;
    button.dataset.busy = '1';
    let serverNote = '';
    try { await api.post('/api/auth/logout', undefined, { auth: false }); }
    catch (err) { if (err.code === 'network') serverNote = " We couldn't reach the server, so you may still be signed in after a refresh."; }
    finally { delete button.dataset.busy; }
    clearPrivateState();
    showMarketing();
    showToast(`You've been signed out.${serverNote}`);
  }

  /* ---------- generic submit/busy handling ---------- */
  function setBusy(form, busy) {
    $$('button', form).forEach(btn => { btn.disabled = busy; if (btn.type === 'submit') btn.setAttribute('aria-busy', String(busy)); });
  }

  async function submitForm(form, errorEl, action, describe = describeError) {
    if (form.dataset.busy) return; // blocks double clicks / repeated Enter
    form.dataset.busy = '1';
    setBusy(form, true);
    errorEl.textContent = '';
    const epoch = state.epoch;
    try {
      await action();
    } catch (err) {
      if (epoch !== state.epoch) return;
      errorEl.textContent = describe(err);
      if (err.code === 'csrf_failed') revalidateSession();
    } finally {
      delete form.dataset.busy;
      setBusy(form, false);
    }
  }

  async function runOnce(button, action) {
    if (button.dataset.busy) return;
    button.dataset.busy = '1';
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    const epoch = state.epoch;
    try { await action(); }
    catch (err) { if (epoch === state.epoch) showToast(describeError(err)); }
    finally { delete button.dataset.busy; button.disabled = false; button.removeAttribute('aria-busy'); }
  }

  /* ---------- data loading ---------- */
  const txQuery = (offset = 0) => {
    const params = new URLSearchParams({ month: state.month, limit: String(PAGE_SIZE), offset: String(offset) });
    if (state.tx.query.trim()) params.set('q', state.tx.query.trim());
    return params.toString();
  };

  async function loadBudget() {
    const epoch = state.epoch;
    const seq = ++monthSeq;
    const month = state.month;
    state.monthStatus = state.monthData && state.monthData.month === month ? 'refreshing' : 'loading';
    state.tx.status = state.tx.items.length ? 'refreshing' : 'loading';
    renderAll();
    const txSeqAtStart = ++txSeq;
    try {
      const [monthData, tx] = await Promise.all([api.get(`/api/months/${month}`), api.get(`/api/transactions?${txQuery(0)}`)]);
      if (epoch !== state.epoch || seq !== monthSeq) return;
      state.monthData = monthData;
      state.monthStatus = 'ready';
      if (txSeqAtStart === txSeq) { // a newer search response must not be overwritten
        state.tx = { ...state.tx, items: tx.items, total: tx.total, status: 'ready', error: '', loadingMore: false };
        if (!state.tx.query.trim()) state.tx.monthTotal = tx.total;
      }
    } catch (err) {
      if (epoch !== state.epoch || seq !== monthSeq) return;
      state.monthStatus = 'error';
      state.monthError = describeError(err);
      state.tx.status = 'error';
      state.tx.error = state.monthError;
    }
    renderAll();
  }

  async function loadTransactions({ append = false } = {}) {
    const epoch = state.epoch;
    const seq = ++txSeq;
    const offset = append ? state.tx.items.length : 0;
    if (append) state.tx.loadingMore = true; else state.tx.status = state.tx.items.length ? 'refreshing' : 'loading';
    renderTransactions();
    try {
      const tx = await api.get(`/api/transactions?${txQuery(offset)}`);
      if (epoch !== state.epoch || seq !== txSeq) return;
      state.tx.items = append ? [...state.tx.items, ...tx.items] : tx.items;
      state.tx.total = tx.total;
      state.tx.status = 'ready';
      state.tx.error = '';
      if (!state.tx.query.trim()) state.tx.monthTotal = tx.total;
    } catch (err) {
      if (epoch !== state.epoch || seq !== txSeq) return;
      state.tx.status = 'error';
      state.tx.error = describeError(err);
    }
    state.tx.loadingMore = false;
    renderTransactions();
  }

  function setMonth(key) {
    key = clampMonth(key);
    if (key === state.month || !state.user) return;
    state.month = key;
    state.monthData = null; // never show one month's numbers under another month's label
    state.tx.items = [];
    state.tx.monthTotal = null;
    renderTitle();
    loadBudget();
  }

  /* ---------- rendering ---------- */
  function renderAll() {
    renderMonthChrome();
    renderBudget();
    renderTransactions();
  }

  function renderMonthChrome() {
    $$('[data-month-label]').forEach(el => { el.textContent = monthLabel(state.month); });
    $$('[data-month-prev]').forEach(el => { el.disabled = state.month <= MIN_MONTH; });
    $$('[data-month-next]').forEach(el => { el.disabled = state.month >= MAX_MONTH; });
    $$('[data-month-today]').forEach(el => { el.hidden = state.month === currentMonthKey(); });
    if (state.screen === 'app') renderTitle();
  }

  function statePanel({ loading = false, error = false, title, text, action }) {
    return h('div', { class: `state-panel${error ? ' is-error' : ''}`, role: error ? 'alert' : 'status' },
      loading ? h('span', { class: 'spinner', 'aria-hidden': 'true' }) : null,
      h('strong', { text: title }),
      text ? h('p', { text }) : null,
      action || null);
  }

  function setSummary(summary) {
    const set = (id, cents, negativeIsBad = false) => {
      const el = $(id);
      el.textContent = summary ? formatCents(cents) : '—';
      el.classList.toggle('negative', Boolean(summary) && negativeIsBad && cents < 0);
    };
    set('#sumIncome', summary && summary.income_cents);
    set('#sumAssigned', summary && summary.assigned_cents);
    set('#sumActivity', summary && summary.activity_cents);
    set('#sumCarry', summary && summary.carry_in_cents);
    set('#sumAvailable', summary && summary.available_cents, true);
    const card = $('.ready-card');
    if (summary) {
      $('#readyAmount').textContent = formatCents(summary.ready_to_assign_cents);
      $('#readyMessage').textContent = readyMessage(summary);
      card.classList.toggle('is-negative', summary.ready_to_assign_cents < 0);
    } else {
      $('#readyAmount').textContent = '—';
      $('#readyMessage').textContent = state.monthStatus === 'error' ? 'Unable to load.' : 'Loading your budget…';
      card.classList.remove('is-negative');
    }
  }

  function renderBudget() {
    const container = $('#categoryGroups');
    const data = state.monthData;
    container.classList.toggle('is-refreshing', state.monthStatus === 'refreshing');
    container.setAttribute('aria-busy', String(state.monthStatus === 'loading' || state.monthStatus === 'refreshing'));
    setSummary(data ? data.summary : null);

    if (!data) {
      container.replaceChildren(state.monthStatus === 'error'
        ? statePanel({ error: true, title: "We couldn't load your budget.", text: state.monthError, action: h('button', { class: 'button button-secondary', type: 'button', 'data-retry-budget': true, text: 'Try again' }) })
        : statePanel({ loading: true, title: 'Loading your budget…' }));
      return;
    }
    if (!data.groups.length) { container.replaceChildren(buildOnboarding()); return; }

    const query = $('#categorySearch').value.trim().toLowerCase();
    const sections = [];
    data.groups.forEach(group => {
      const rows = group.categories.filter(c => !query || c.name.toLowerCase().includes(query) || group.name.toLowerCase().includes(query));
      if (query && !rows.length) return;
      const isCollapsed = state.collapsed.has(group.id);
      const body = h('div', { class: 'group-rows' });
      body.hidden = isCollapsed;
      rows.forEach(c => body.append(buildCategoryRow(c)));
      if (!group.categories.length && !query) body.append(h('p', { class: 'empty-row', text: 'No categories in this group yet.' }));
      if (!query) body.append(h('div', { class: 'add-row' }, h('button', { class: 'link-button', type: 'button', dataset: { addCategory: group.id }, text: '+ Add category' })));
      sections.push(h('section', { class: 'category-group' },
        h('div', { class: 'group-header' },
          h('div', { class: 'group-title' },
            h('button', { type: 'button', dataset: { group: group.id }, 'aria-expanded': String(!isCollapsed), text: `${isCollapsed ? '▸' : '▾'} ${group.name}` }),
            h('button', { class: 'row-action', type: 'button', dataset: { editGroup: group.id }, 'aria-label': `Edit group ${group.name}`, text: '✎' })),
          h('span', { class: 'group-total', text: formatCents(group.assigned_cents) }),
          h('span', { class: 'group-total', text: formatCents(group.activity_cents) }),
          h('span', { class: 'group-total', text: formatCents(group.available_cents) })),
        body));
    });
    container.replaceChildren(...(sections.length ? sections : [h('div', { class: 'empty-results' }, h('strong', { text: 'No categories found.' }), h('p', { text: 'Try a broader search.' }))]));

    if (state.focusAfterRender) {
      const target = $(state.focusAfterRender);
      if (target) target.focus();
      state.focusAfterRender = null;
    }
  }

  function buildCategoryRow(c) {
    return h('div', { class: 'category-row' },
      h('div', { class: 'category-name' },
        h('div', { class: 'category-name-text' }, h('strong', { text: c.name }), h('small', { text: targetLine(c) })),
        h('button', { class: 'row-action', type: 'button', dataset: { editCategoryMeta: c.id }, 'aria-label': `Edit category ${c.name}`, text: '✎' })),
      h('button', { class: 'edit-amount', type: 'button', dataset: { editCategory: c.id }, 'aria-label': `Edit assigned amount for ${c.name}`, text: formatCents(c.assigned_cents) }),
      h('span', { class: 'money-cell', text: formatCents(c.activity_cents) }),
      h('span', { class: `available-pill${c.available_cents < 0 ? ' negative' : ''}`, text: formatCents(c.available_cents) }));
  }

  function buildOnboarding() {
    const step = (n, title, text, button) => h('li', {}, h('span', { text: n }), h('div', {}, h('h3', { text: title }), h('p', { text }), button || null));
    return h('div', { class: 'onboarding' },
      h('div', {},
        h('p', { class: 'app-kicker', text: 'Welcome to ClearPocket' }),
        h('h3', { class: 'onboarding-title', text: 'Your budget is empty. Let’s set it up.' }),
        h('p', { class: 'dialog-copy', text: 'Everything you add is saved to your private account.' })),
      h('ol', { class: 'guide-steps' },
        step('1', 'Create a group and categories', 'For example “Home & bills” with Rent and Utilities.',
          h('button', { class: 'button button-primary button-small', type: 'button', 'data-add-group': true, text: 'Create your first group' })),
        step('2', 'Add your income', 'Record money you already have. It becomes ready to assign.',
          h('button', { class: 'button button-secondary button-small', type: 'button', 'data-add-income': true, text: 'Add income' })),
        step('3', 'Assign money to categories', 'Give every dollar a job, then record spending as you go.')),
      h('div', { class: 'onboarding-sample' },
        h('strong', { text: 'Just exploring?' }),
        h('p', { text: 'Add an example budget (September 2026) to your account. It is only available while your budget is empty, and you can edit or delete anything afterward.' }),
        h('button', { class: 'button button-secondary button-small', type: 'button', 'data-load-sample': true, text: 'Load sample data' })));
  }

  function renderTransactions() {
    const tx = state.tx;
    const tbody = $('#transactionRows');
    const count = $('#transactionCount');
    const more = $('#txLoadMore');
    $('.transaction-table-wrap').classList.toggle('is-refreshing', tx.status === 'refreshing');
    const nav = $('#navTxCount');
    if (tx.monthTotal != null) { nav.textContent = String(tx.monthTotal); nav.hidden = false; } else { nav.hidden = true; }

    const message = (text, extra) => h('tr', {}, h('td', { colspan: '4', class: 'empty-results' }, text, extra || null));
    if (tx.status === 'loading' || (tx.status === 'idle' && !tx.items.length)) {
      tbody.replaceChildren(message('Loading transactions…'));
      count.textContent = 'Loading…';
      more.hidden = true;
      return;
    }
    if (tx.status === 'error' && !tx.items.length) {
      tbody.replaceChildren(message(tx.error || "Couldn't load transactions.", h('div', {}, h('button', { class: 'button button-secondary button-small', type: 'button', 'data-retry-tx': true, text: 'Try again' }))));
      count.textContent = 'Unavailable';
      more.hidden = true;
      return;
    }
    if (!tx.items.length) {
      tbody.replaceChildren(message(tx.query.trim() ? 'No matching transactions.' : `No transactions in ${monthLabel(state.month)} yet.`));
    } else {
      tbody.replaceChildren(...tx.items.map(t => h('tr', {},
        h('td', { text: formatDay(t.occurred_on) }),
        h('td', {}, h('strong', { text: t.payee }), t.memo ? h('span', { class: 'payee-memo', text: t.memo }) : null),
        h('td', { text: t.category_name || 'Ready to assign' }),
        h('td', { class: `amount-cell ${t.amount_cents < 0 ? 'outflow' : 'inflow'}`, text: `${t.amount_cents < 0 ? '−' : '+'}${formatCents(Math.abs(t.amount_cents))}` }))));
    }
    count.textContent = tx.items.length < tx.total ? `Showing ${tx.items.length} of ${tx.total}` : `${tx.total} transaction${tx.total === 1 ? '' : 's'}`;
    more.hidden = tx.items.length >= tx.total;
    const moreBtn = $('[data-tx-more]');
    moreBtn.disabled = tx.loadingMore;
    moreBtn.textContent = tx.loadingMore ? 'Loading…' : 'Load more';
  }

  /* ---------- dialogs ---------- */
  function categoryOptions(select, { withAvailable = false } = {}) {
    const data = state.monthData;
    select.replaceChildren(...(data ? data.groups : []).filter(g => g.categories.length).map(g =>
      h('optgroup', { label: g.name }, ...g.categories.map(c => h('option', { value: c.id, text: withAvailable ? `${c.name} — ${formatCents(c.available_cents)} available` : c.name })))));
  }

  const findCategory = id => flatCategories(state.monthData).find(c => c.id === id);
  const ready = () => state.monthData && state.monthStatus !== 'loading' && state.monthStatus !== 'error';

  function guardReady() {
    if (!state.user) return false;
    if (!ready()) { showToast('Your budget is still loading. Try again in a moment.'); return false; }
    return true;
  }

  function guardCategories() {
    if (!state.monthData.groups.length) { openGroupDialog(); showToast('Create a group and a category first.'); return false; }
    if (!flatCategories(state.monthData).length) { openCategoryDialog(null, state.monthData.groups[0].id); showToast('Add a category first.'); return false; }
    return true;
  }

  function updateAssignHint() {
    const c = findCategory($('#assignCategory').value);
    if (!c) return;
    $('#assignAmount').value = centsToInput(c.assigned_cents);
    $('#assignHint').textContent = `Currently ${formatCents(c.assigned_cents)} assigned; ${formatCents(state.monthData.summary.ready_to_assign_cents)} ready to assign.`;
  }

  function openAssignDialog(categoryId) {
    if (!guardReady() || !guardCategories()) return;
    categoryOptions($('#assignCategory'));
    const first = flatCategories(state.monthData)[0];
    $('#assignCategory').value = categoryId && findCategory(categoryId) ? categoryId : first.id;
    updateAssignHint();
    $('#assignError').textContent = '';
    $('#assignDialog').showModal();
    $('#assignAmount').focus();
    $('#assignAmount').select();
  }

  function openMoveDialog() {
    if (!guardReady() || !guardCategories()) return;
    const cats = flatCategories(state.monthData);
    if (cats.length < 2) { showToast('Add at least two categories before moving money.'); openCategoryDialog(null, cats[0].group_id); return; }
    categoryOptions($('#moveFrom'), { withAvailable: true });
    categoryOptions($('#moveTo'), { withAvailable: true });
    const pick = pickMoveDefaults(cats);
    $('#moveFrom').value = pick.from;
    $('#moveTo').value = pick.to;
    $('#moveAmount').value = '';
    $('#moveError').textContent = '';
    $('#moveDialog').showModal();
    $('#moveAmount').focus();
  }

  function syncTransactionType() {
    const income = $('#transactionType').value === 'income';
    $('#transactionCategoryField').hidden = income;
    $('#transactionCategory').required = !income;
  }

  function openTransactionDialog({ income = false } = {}) {
    if (!guardReady()) return;
    categoryOptions($('#transactionCategory'));
    const hasCategories = flatCategories(state.monthData).length > 0;
    $('#transactionType').value = income || !hasCategories ? 'income' : 'spending';
    $('#transactionType').querySelector('option[value="spending"]').disabled = !hasCategories;
    syncTransactionType();
    $('#transactionDate').value = defaultTransactionDate(state.month);
    $('#transactionPayee').value = '';
    $('#transactionAmount').value = '';
    $('#transactionError').textContent = hasCategories ? '' : 'Add a category to record spending. Income can be recorded now.';
    $('#transactionDialog').showModal();
    $('#transactionPayee').focus();
  }

  function openGroupDialog(groupId) {
    if (!guardReady()) return;
    const group = groupId ? state.monthData.groups.find(g => g.id === groupId) : null;
    const dialog = $('#groupDialog');
    dialog.dataset.groupId = group ? group.id : '';
    $('#groupTitle').textContent = group ? 'Edit group' : 'Add group';
    $('#groupName').value = group ? group.name : '';
    $('#groupDelete').hidden = !group;
    $('#groupError').textContent = '';
    dialog.showModal();
    $('#groupName').focus();
  }

  function openCategoryDialog(categoryId, groupId) {
    if (!guardReady()) return;
    if (!state.monthData.groups.length) { openGroupDialog(); showToast('Create a group first.'); return; }
    const category = categoryId ? findCategory(categoryId) : null;
    const dialog = $('#categoryDialog');
    dialog.dataset.categoryId = category ? category.id : '';
    $('#categoryTitle').textContent = category ? 'Edit category' : 'Add category';
    $('#categoryGroup').replaceChildren(...state.monthData.groups.map(g => h('option', { value: g.id, text: g.name })));
    $('#categoryGroup').value = category ? category.group_id : (groupId || state.monthData.groups[0].id);
    $('#categoryName').value = category ? category.name : '';
    $('#categoryTarget').value = category && category.target_text ? category.target_text : '';
    $('#categoryDelete').hidden = !category;
    $('#categoryError').textContent = '';
    dialog.showModal();
    $('#categoryName').focus();
  }

  let confirmAction = null;
  function askConfirm({ title, message, label = 'Delete', action }) {
    $('#confirmTitle').textContent = title;
    $('#confirmMessage').textContent = message;
    $('#confirmOk').textContent = label;
    $('#confirmError').textContent = '';
    confirmAction = action;
    $('#confirmDialog').showModal();
    $('#confirmOk').focus();
  }

  /* ---------- sidebar ---------- */
  function openSidebar() {
    $('#appSidebar').classList.add('open');
    $('.sidebar-scrim').hidden = false;
    $('[data-sidebar-close]', $('#appSidebar')).focus();
  }
  function closeSidebar() {
    $('#appSidebar').classList.remove('open');
    $('.sidebar-scrim').hidden = true;
  }

  /* ---------- form handlers ---------- */
  function bindForms() {
    $('#loginForm').addEventListener('submit', event => {
      event.preventDefault();
      const form = event.currentTarget;
      submitForm(form, $('#loginError'), async () => {
        const data = await api.post('/api/auth/login', { email: $('#loginEmail').value.trim(), password: $('#loginPassword').value }, { auth: false });
        form.reset();
        await completeAuth(data);
      });
    });

    $('#registerForm').addEventListener('submit', event => {
      event.preventDefault();
      const form = event.currentTarget;
      submitForm(form, $('#registerError'), async () => {
        const body = { email: $('#registerEmail').value.trim(), password: $('#registerPassword').value };
        const name = $('#registerName').value.trim();
        if (name) body.display_name = name;
        const data = await api.post('/api/auth/register', body, { auth: false });
        form.reset();
        await completeAuth(data);
      });
    });

    $('#assignForm').addEventListener('submit', event => {
      event.preventDefault();
      const form = event.currentTarget;
      const category = findCategory($('#assignCategory').value);
      submitForm(form, $('#assignError'), async () => {
        const cents = parseMoneyToCents($('#assignAmount').value);
        if (cents === null) throw new ApiError(0, 'client', 'Enter zero or a positive amount, like 250 or 250.50.');
        const month = state.month;
        const epoch = state.epoch;
        try {
          const updated = await api.put(`/api/months/${month}/assignments/${category.id}`, { assigned_cents: cents, expected_version: category.assignment_version });
          if (epoch !== state.epoch) return;
          if (state.month === month) { state.monthData = updated; state.monthStatus = 'ready'; }
          state.focusAfterRender = `[data-edit-category="${category.id}"]`;
          $('#assignDialog').close();
          renderAll();
          showToast(`${category.name} updated to ${formatCents(cents)}.`);
        } catch (err) {
          if (err.code === 'version_conflict') {
            await loadBudget(); // show the latest server numbers, keep the dialog open
            if (epoch === state.epoch && findCategory(category.id)) updateAssignHint();
          }
          throw err;
        }
      });
    });
    $('#assignCategory').addEventListener('change', updateAssignHint);

    $('#moveForm').addEventListener('submit', event => {
      event.preventDefault();
      const form = event.currentTarget;
      const from = findCategory($('#moveFrom').value);
      const to = findCategory($('#moveTo').value);
      submitForm(form, $('#moveError'), async () => {
        if (!from || !to || from.id === to.id) throw new ApiError(0, 'client', 'Choose two different categories.');
        const cents = parseMoneyToCents($('#moveAmount').value);
        if (cents === null || cents <= 0) throw new ApiError(0, 'client', 'Enter an amount greater than zero, like 40 or 40.00.');
        const month = state.month;
        const epoch = state.epoch;
        const updated = await api.post(`/api/months/${month}/moves`, { from_category_id: from.id, to_category_id: to.id, amount_cents: cents });
        if (epoch !== state.epoch) return;
        if (state.month === month) { state.monthData = updated; state.monthStatus = 'ready'; }
        $('#moveDialog').close();
        renderAll();
        showToast(`${formatCents(cents)} moved from ${from.name} to ${to.name}.`);
      }, err => describeError(err, { name: from && from.name }));
    });

    $('#transactionType').addEventListener('change', syncTransactionType);
    $('#transactionForm').addEventListener('submit', event => {
      event.preventDefault();
      const form = event.currentTarget;
      submitForm(form, $('#transactionError'), async () => {
        const income = $('#transactionType').value === 'income';
        const cents = parseMoneyToCents($('#transactionAmount').value);
        if (cents === null || cents <= 0) throw new ApiError(0, 'client', 'Enter an amount greater than zero, like 12.50.');
        const payee = $('#transactionPayee').value.trim();
        if (!payee) throw new ApiError(0, 'client', 'Enter a payee or description.');
        const date = $('#transactionDate').value;
        if (!date) throw new ApiError(0, 'client', 'Choose a date.');
        const category = income ? null : findCategory($('#transactionCategory').value);
        if (!income && !category) throw new ApiError(0, 'client', 'Choose a category.');
        const body = { occurred_on: date, payee, amount_cents: income ? cents : -cents };
        if (category) body.category_id = category.id;
        const epoch = state.epoch;
        await api.post('/api/transactions', body);
        if (epoch !== state.epoch) return;
        $('#transactionDialog').close();
        const elsewhere = date.slice(0, 7) !== state.month ? ` for ${monthLabel(date.slice(0, 7))}` : '';
        showToast(income ? `${formatCents(cents)} income added${elsewhere}.` : `${formatCents(cents)} recorded in ${category.name}${elsewhere}.`);
        await loadBudget();
      });
    });

    $('#groupForm').addEventListener('submit', event => {
      event.preventDefault();
      const dialog = $('#groupDialog');
      submitForm(event.currentTarget, $('#groupError'), async () => {
        const name = $('#groupName').value.trim();
        if (!name) throw new ApiError(0, 'client', 'Enter a group name.');
        const id = dialog.dataset.groupId;
        const epoch = state.epoch;
        if (id) await api.patch(`/api/category-groups/${id}`, { name }); else await api.post('/api/category-groups', { name });
        if (epoch !== state.epoch) return;
        dialog.close();
        showToast(id ? `Group renamed to ${name}.` : `Group ${name} added.`);
        await loadBudget();
      });
    });

    $('#categoryForm').addEventListener('submit', event => {
      event.preventDefault();
      const dialog = $('#categoryDialog');
      submitForm(event.currentTarget, $('#categoryError'), async () => {
        const name = $('#categoryName').value.trim();
        if (!name) throw new ApiError(0, 'client', 'Enter a category name.');
        const target = $('#categoryTarget').value.trim();
        const id = dialog.dataset.categoryId;
        const epoch = state.epoch;
        if (id) await api.patch(`/api/categories/${id}`, { group_id: $('#categoryGroup').value, name, target_text: target || null });
        else await api.post('/api/categories', { group_id: $('#categoryGroup').value, name, ...(target ? { target_text: target } : {}) });
        if (epoch !== state.epoch) return;
        dialog.close();
        showToast(id ? `${name} saved.` : `${name} added.`);
        await loadBudget();
      });
    });

    $('#groupDelete').addEventListener('click', () => {
      const id = $('#groupDialog').dataset.groupId;
      const group = state.monthData.groups.find(g => g.id === id);
      $('#groupDialog').close();
      askConfirm({
        title: `Delete “${group.name}”?`,
        message: 'Only empty groups can be deleted. Move or delete its categories first.',
        action: async () => { await api.del(`/api/category-groups/${id}`); showToast(`Group ${group.name} deleted.`); }
      });
    });
    $('#categoryDelete').addEventListener('click', () => {
      const id = $('#categoryDialog').dataset.categoryId;
      const category = findCategory(id);
      $('#categoryDialog').close();
      askConfirm({
        title: `Delete “${category.name}”?`,
        message: 'Categories with transactions or assigned money can’t be deleted, so your history stays accurate.',
        action: async () => { await api.del(`/api/categories/${id}`); showToast(`${category.name} deleted.`); }
      });
    });
    $('#confirmForm').addEventListener('submit', event => {
      event.preventDefault();
      submitForm(event.currentTarget, $('#confirmError'), async () => {
        const epoch = state.epoch;
        await confirmAction();
        if (epoch !== state.epoch) return;
        $('#confirmDialog').close();
        await loadBudget();
      }, err => (err.code === 'group_not_empty' || err.code === 'category_in_use') ? err.message : describeError(err));
    });
  }

  /* ---------- events ---------- */
  function bindEvents() {
    $$('[data-theme-toggle]').forEach(btn => btn.addEventListener('click', () => applyTheme(theme === 'dark' ? 'light' : 'dark', true)));

    $$('[data-open-auth]').forEach(btn => btn.addEventListener('click', () => {
      if (state.user) enterApp('budget'); else showAuth(btn.dataset.openAuth);
    }));
    $$('[data-auth-switch]').forEach(btn => btn.addEventListener('click', () => showAuth(btn.dataset.authSwitch)));
    $('[data-back-home]').addEventListener('click', showMarketing);
    $('[data-logout]').addEventListener('click', logout);
    $$('.nav-item[data-view]').forEach(btn => btn.addEventListener('click', () => { switchView(btn.dataset.view); }));
    $('[data-view-jump]').addEventListener('click', () => switchView('budget'));

    $$('[data-month-prev]').forEach(btn => btn.addEventListener('click', () => setMonth(shiftMonth(state.month, -1))));
    $$('[data-month-next]').forEach(btn => btn.addEventListener('click', () => setMonth(shiftMonth(state.month, 1))));
    $$('[data-month-today]').forEach(btn => btn.addEventListener('click', () => setMonth(currentMonthKey())));

    $$('[data-move]').forEach(btn => btn.addEventListener('click', openMoveDialog));
    $$('[data-assign]').forEach(btn => btn.addEventListener('click', () => openAssignDialog()));
    $$('[data-add-transaction]').forEach(btn => btn.addEventListener('click', () => openTransactionDialog()));
    $('[data-sidebar-open]').addEventListener('click', openSidebar);
    $$('[data-sidebar-close]').forEach(btn => btn.addEventListener('click', closeSidebar));
    $('[data-tx-more]').addEventListener('click', () => loadTransactions({ append: true }));

    // Delegated: the budget area is re-rendered after each refresh.
    $('#appView').addEventListener('click', event => {
      const t = event.target;
      const pick = sel => t.closest(sel);
      let el;
      if ((el = pick('[data-edit-category]'))) openAssignDialog(el.dataset.editCategory);
      else if ((el = pick('[data-edit-category-meta]'))) openCategoryDialog(el.dataset.editCategoryMeta);
      else if ((el = pick('[data-edit-group]'))) openGroupDialog(el.dataset.editGroup);
      else if ((el = pick('[data-add-category]'))) openCategoryDialog(null, el.dataset.addCategory);
      else if ((el = pick('[data-add-group]'))) openGroupDialog();
      else if (pick('[data-add-income]')) openTransactionDialog({ income: true });
      else if ((el = pick('[data-load-sample]'))) runOnce(el, loadSampleData);
      else if (pick('[data-retry-budget]')) loadBudget();
      else if (pick('[data-retry-tx]')) loadTransactions();
      else if ((el = pick('[data-group]'))) {
        const id = el.dataset.group;
        if (state.collapsed.has(id)) state.collapsed.delete(id); else state.collapsed.add(id);
        renderBudget();
        const again = $(`[data-group="${id}"]`);
        if (again) again.focus();
      }
    });

    $('#categorySearch').addEventListener('input', () => renderBudget());
    $('#transactionSearch').addEventListener('input', event => {
      state.tx.query = event.target.value;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { if (state.user) loadTransactions(); }, 250);
    });

    $$('dialog').forEach(dialog => {
      dialog.addEventListener('click', event => { if (event.target.closest('[data-dialog-close]')) dialog.close(); });
      dialog.addEventListener('cancel', event => { if ($('form', dialog).dataset.busy) event.preventDefault(); });
    });

    window.addEventListener('hashchange', route);
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && $('#appSidebar').classList.contains('open')) { closeSidebar(); $('[data-sidebar-open]').focus(); } });
    document.addEventListener('visibilitychange', () => { if (!document.hidden && state.user && Date.now() - state.lastSessionCheck > 15000) revalidateSession(); });
    window.addEventListener('focus', () => { if (state.user && Date.now() - state.lastSessionCheck > 15000) revalidateSession(); });
  }

  async function loadSampleData() {
    const epoch = state.epoch;
    try {
      await api.post('/api/sample-data');
    } catch (err) {
      if (err.code === 'budget_not_empty') { showToast('Sample data can only be added to a completely empty budget.'); return; }
      throw err;
    }
    if (epoch !== state.epoch) return;
    showToast('Sample budget added for September 2026.');
    state.month = '2026-09';
    state.monthData = null;
    state.tx = freshTx(state.tx.query);
    await loadBudget();
  }

  /* ---------- start ---------- */
  async function bootstrap() {
    applyTheme(initialTheme());
    bindEvents();
    bindForms();
    renderAll();
    try {
      const session = await api.get('/api/auth/session', { auth: false });
      if (session.authenticated) { state.user = session.user; state.csrf = session.csrf_token; state.lastSessionCheck = Date.now(); }
    } catch (_) {
      showToast("Can't reach ClearPocket right now. Check your connection and refresh.");
    }
    refreshAuthUi();
    route();
  }

  bootstrap();
})();
