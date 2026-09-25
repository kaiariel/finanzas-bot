/* Shared pure helpers are also exercised by Node regression tests. */
function previousMonthKey(month) {
  const [year, number] = month.split('-').map(Number);
  return (number === 1 ? year - 1 : year) + '-' + String(number === 1 ? 12 : number - 1).padStart(2, '0');
}
function normalizeText(text) { return String(text || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim(); }
function filterRows(rows, filters, monthOverride) {
  const month = monthOverride === undefined ? filters.month : monthOverride;
  const query = normalizeText(filters.search);
  return rows.filter(row => (!month || row.monthKey === month)
    && (!filters.category || row.category === filters.category)
    && (!filters.type || row.kind === filters.type)
    && (!filters.user || row.user === filters.user)
    && (!filters.store || normalizeText(row.store) === normalizeText(filters.store))
    && (!filters.fixed || String(row.isFixed) === filters.fixed)
    && (!filters.receipt || (filters.receipt === 'none' ? !row.hasReceipt : filters.receipt === 'missing' ? row.hasReceipt && !row.fileAvailable : row.attachmentType === filters.receipt))
    && (!query || normalizeText([row.description, row.store, row.user, row.category, '#' + row.id].join(' ')).includes(query)));
}
function totals(rows) {
  return rows.reduce((result, row) => { result[row.kind] += row.amountCents; if (row.kind === 'expense' && row.isFixed) result.fixed += row.amountCents; return result; }, { income: 0, expense: 0, fixed: 0 });
}
function groupPurchases(rows) {
  const groups = new Map();
  rows.forEach(row => {
    const key = row.receipt ? [row.receipt, row.dateIso, row.kind, row.user].join('|') : 'row-' + row.id;
    if (!groups.has(key)) groups.set(key, { ...row, children: [], amountCents: 0 });
    const group = groups.get(key); group.children.push(row); group.amountCents += row.amountCents;
  });
  return [...groups.values()];
}
function projectionEstimate(summary) {
  return summary.actualIncomeCents - summary.actualExpenseCents + summary.pendingIncomeCents - summary.pendingExpenseCents;
}
if (typeof module !== 'undefined') module.exports = { previousMonthKey, filterRows, totals, groupPurchases, projectionEstimate };

if (typeof document !== 'undefined') (() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let data = JSON.parse($('initialData').textContent);
  const money = cents => new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR' }).format(cents / 100);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[char]));
  const monthName = key => key ? new Intl.DateTimeFormat('es-ES', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(key + '-15T12:00:00Z')) : 'Todo el historial';
  const unique = list => [...new Set(list.filter(Boolean))].sort((a, b) => String(a).localeCompare(String(b), 'es'));
  const sum = rows => rows.reduce((total, row) => total + row.amountCents, 0);
  const groupTotals = (rows, key) => {
    const map = new Map(); rows.forEach(row => map.set(row[key] || 'Sin especificar', (map.get(row[key] || 'Sin especificar') || 0) + row.amountCents));
    return [...map].sort((a, b) => b[1] - a[1]);
  };
  const empty = text => '<p class="empty">' + esc(text) + '</p>';
  const shortMonth = key => new Intl.DateTimeFormat('es-ES', { month: 'short', timeZone: 'UTC' }).format(new Date(key + '-15T12:00:00Z')).replace('.', '');
  const compactMoney = cents => new Intl.NumberFormat('es-ES', { notation: 'compact', maximumFractionDigits: 1 }).format(cents / 100) + ' €';
  /* Cuadro de color de la fila: dos letras de la categoría bastan para reconocerla de un vistazo. */
  const initials = row => String(row.category || row.description || '?').trim().slice(0, 2).toUpperCase();
  const avatar = row => '<span class="avatar ' + row.kind + '" aria-hidden="true">' + esc(initials(row)) + '</span>';
  const swatch = index => 'var(--c' + (index % 8 + 1) + ')';
  const pending = row => ['nuevo', 'pending', 'voice_pending', 'dudoso', 'missing'].includes(row.status);
  const filterIds = { month: 'monthFilter', search: 'searchFilter', category: 'tableCategoryFilter', type: 'typeFilter', user: 'userFilter', fixed: 'fixedFilter', receipt: 'receiptFilter', store: 'storeFilter' };
  const addMonths = (key, count) => { const date = new Date(key + '-01T12:00:00Z'); date.setUTCMonth(date.getUTCMonth() + count); return date.toISOString().slice(0, 7); };
  let state = { tab: 'dashboard', page: 1, filesShown: 24, more: false, month: data.today.slice(0, 7), search: '', category: '', type: '', user: '', fixed: '', receipt: '', store: '', projectionMonth: data.today.slice(0, 7), analyticsMonth: data.today.slice(0, 7), savingsStartMonth: data.today.slice(0, 7), savingsEndMonth: addMonths(data.today.slice(0, 7), 2), requireReceipts: false, view: 'purchases', sort: 'date_desc', pageSize: '25', fileStatus: '', projectionStatus: '', columns: { category: true, store: true, user: true } };
  try { state = { ...state, ...JSON.parse(sessionStorage.getItem('finance-ui-v2') || '{}') }; } catch (_) { /* Storage may be disabled in local exports. */ }
  let toastTimer, busy = false;
  const focusOrigins = new Map();
  const snapshots = new Map();
  function applyTheme(theme) {
    const dark = theme === 'dark';
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    $('themeToggle').setAttribute('aria-pressed', String(dark));
    $('themeToggle').textContent = dark ? '☀ Modo claro' : '◐ Modo oscuro';
  }
  function preferredTheme() {
    try {
      const saved = localStorage.getItem('finance-theme');
      if (saved === 'dark' || saved === 'light') return saved;
    } catch (_) { /* The static report may not allow persistent storage. */ }
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  function saveState() { try { sessionStorage.setItem('finance-ui-v2', JSON.stringify(state)); } catch (_) {} }
  function notify(message, error = false, action = null) {
    clearTimeout(toastTimer); $('toast').textContent = message; $('toast').className = 'toast' + (error ? ' error' : ''); $('toast').hidden = false;
    if (action) {
      const button = document.createElement('button'); button.type = 'button'; button.textContent = action.label;
      button.addEventListener('click', async () => { $('toast').hidden = true; try { await action.run(); } catch (failure) { notify(failure.message, true); } });
      $('toast').append(' ', button);
    }
    if (!error) toastTimer = setTimeout(() => { $('toast').hidden = true; }, action ? 10000 : 5000);
  }
  function setOptions(select, values, first, selected) {
    select.innerHTML = (first === null ? '' : '<option value="">' + esc(first) + '</option>') + values.map(item => {
      const [value, label] = Array.isArray(item) ? item : [item, item]; return '<option value="' + esc(value) + '">' + esc(label) + '</option>';
    }).join('');
    if (selected !== undefined) select.value = selected;
  }
  function setup() {
    const months = unique([data.today.slice(0, 7), ...data.transactions.map(row => row.monthKey), ...data.receipts.map(row => row.monthKey)]).reverse();
    setOptions($('monthFilter'), months.map(key => [key, monthName(key)]), 'Todo el historial', state.month);
    setOptions($('tableCategoryFilter'), unique(data.transactions.map(row => row.category)), 'Todas', state.category);
    setOptions($('storeFilter'), unique(data.transactions.map(row => row.store)), 'Todos', state.store);
    setOptions($('userFilter'), unique([...data.transactions, ...data.receipts].map(row => row.user)), 'Todas', state.user);
    const projectionMonths = data.projections.months.map(row => [row.monthKey, monthName(row.monthKey)]);
    setOptions($('projectionMonth'), projectionMonths, null, state.projectionMonth);
    setOptions($('analyticsMonth'), unique([...months, ...projectionMonths.map(row => row[0])]).reverse().map(key => [key, monthName(key)]), null, state.analyticsMonth);
    if (!$('projectionMonth').value) { state.projectionMonth = data.projections.months[0]?.monthKey || ''; $('projectionMonth').value = state.projectionMonth; }
    if (!$('savingsEndMonth').value || !data.projections.months.some(row => row.monthKey === state.savingsEndMonth)) state.savingsEndMonth = addMonths(data.today.slice(0, 7), 2);
    if (!$('savingsStartMonth')) $('savingsEndMonth').parentElement.insertAdjacentHTML('beforebegin', '<label>Desde el mes<input id="savingsStartMonth" type="month"></label>');
    $('savingsStartMonth').value = state.savingsStartMonth || data.today.slice(0, 7);
    $('savingsEndMonth').value = state.savingsEndMonth;
    if (state.savingsEndMonth < state.savingsStartMonth) state.savingsEndMonth = state.savingsStartMonth;
    const monthlyPanel = $('savingsChart')?.closest('article.panel'); const savingsPanel = $('savingsPanel');
    if (monthlyPanel && savingsPanel) {
      let overview = savingsPanel.querySelector('.savings-overview');
      if (!overview) { overview = document.createElement('div'); overview.className = 'savings-overview'; const side = document.createElement('article'); side.className = 'panel savings-side'; const intro = savingsPanel.querySelector(':scope > p.muted'); side.appendChild(intro); side.appendChild($('savingsSummary')); overview.appendChild(monthlyPanel); overview.appendChild(side); const currentCards = savingsPanel.querySelector('.two-col'); currentCards ? savingsPanel.insertBefore(overview, currentCards) : savingsPanel.appendChild(overview); }
    }
    Object.entries(filterIds).forEach(([key, id]) => { $(id).value = state[key]; if ($(id).tagName === 'SELECT' && $(id).value !== state[key]) state[key] = $(id).value; });
    $('movementView').value = state.view; $('amountSort').value = state.sort; $('pageSize').value = state.pageSize;
    $('fileStatus').value = state.fileStatus; $('projectionStatusFilter').value = state.projectionStatus;
    $('requireReceipts').checked = state.requireReceipts;
    document.querySelectorAll('[data-column]').forEach(input => { input.checked = state.columns[input.dataset.column] !== false; $('transactionsBody').classList.toggle('hide-' + input.dataset.column, !input.checked); });
    $('addProjection').hidden = !data.editable; $('addTransaction').hidden = !data.editable; $('quickAddTransaction').hidden = !data.editable; $('refreshData').hidden = !data.editable;
    $('updatedAt').textContent = 'Datos al ' + data.generatedAt + (data.editable ? '' : ' · copia de consulta');
    $('storeNames').innerHTML = unique(data.transactions.map(row => row.store)).map(store => '<option value="' + esc(store) + '"></option>').join('');
    const goalForm = $('goalForm'); if (goalForm && !goalForm.elements.walletId) goalForm.querySelector('button[type=submit]').insertAdjacentHTML('beforebegin', '<select name="walletId" aria-label="Cartera vinculada"><option value="">Sin cartera</option></select>'); if (goalForm?.elements.walletId) setOptions(goalForm.elements.walletId, (data.savings?.wallets || []).filter(wallet => wallet.active).map(wallet => [String(wallet.id), wallet.name]), 'Sin cartera');
    if (!$('budgetForm').elements.month.value) $('budgetForm').elements.month.value = data.today.slice(0, 7);
    addPanelHelpButtons();
    showTab(state.tab, false);
  }
  function addPanelHelpButtons() {
    const help = {
      dashboard: ['Resumen', 'Aquí ves una fotografía rápida del periodo elegido: cuánto entró, cuánto salió y en qué se fue el dinero. Los gráficos ayudan a detectar cambios.'],
      transactions: ['Movimientos', 'Es la lista de ingresos y gastos registrados. Puedes buscar, filtrar, editar o crear un movimiento manual.'],
      projection: ['Proyección', 'Sirve para anotar lo que esperas cobrar o pagar. Marcar algo como pagado solo organiza el plan; no mueve dinero del banco.'],
      savings: ['Ahorro', 'Aquí ves el saldo que tienes, tus carteras de ahorro y lo que podrías acumular hasta el mes elegido.'],
      files: ['Archivos', 'Aquí están los tickets, fotos, documentos y audios. Puedes abrirlos, revisar sus líneas y confirmar qué movimientos representan.'],
      analytics: ['Diagnóstico', 'Resume avisos de calidad: tickets pendientes, gastos sin justificante, categorías dudosas y meses futuros con riesgo.']
    };
    const detailedHelp = {
      dashboard: { title: 'Resumen: entender tu mes', what: 'Es la pantalla principal. Resume el dinero que ha entrado y salido en el periodo seleccionado.', steps: ['Elige un mes con las flechas o el selector de Periodo.', 'Mira Resultado registrado para saber si entró más dinero del que salió.', 'Usa Gasto por día para detectar días con gastos altos.', 'Usa Gasto por categoría para descubrir en qué tipo de gasto se concentra el dinero.'], example: 'Si eliges septiembre y ves 1.200 € de ingresos y 950 € de gastos, el resultado registrado es 250 €. Solo suma lo que la app conoce.' },
      transactions: { title: 'Movimientos: tu lista de ingresos y gastos', what: 'Cada fila representa un ingreso o un gasto que la app ha registrado.', steps: ['Pulsa Nuevo movimiento para introducir uno manualmente.', 'Usa Buscar para encontrar “Mercadona” o “alquiler”.', 'Abre Más filtros para ver solo gastos, ingresos, una categoría o una persona.', 'Pulsa Editar para corregir una fecha, importe, cuenta o descripción.'], example: 'Una compra puede ser: 35,40 €, “Compra semanal”, categoría “Hogar y Alimentación” y tipo “Gasto”.' },
      projection: { title: 'Proyección: planificar lo que viene', what: 'Es una lista de cobros y pagos esperados. Sirve para anticipar si un mes puede quedar corto.', steps: ['Elige el mes que quieres planificar.', 'Añade alquiler, sueldo, cuotas o suscripciones.', 'Marca Pagar o Cobrar cuando ya lo hayas realizado.', 'Revisa la diferencia entre lo previsto y lo registrado.'], example: 'Si añades “Alquiler” por 700 € y aún no aparece un movimiento, seguirá pendiente hasta que registres el pago.' },
      savings: { title: 'Ahorro: saber cuánto podrías tener', what: 'Combina el saldo actual que escribas, las carteras que selecciones y el ahorro previsto de cada mes.', steps: ['Elige hasta qué mes quieres calcular.', 'Guarda un saldo actual si quieres partir de una cifra real corregida.', 'Crea carteras como Emergencias o Viaje y marca cuáles se incluyen.', 'Revisa el desglose mensual y prueba escenarios sin guardar cambios.'], example: 'Si incluyes 500 € actuales y de septiembre a noviembre prevés ahorrar 300 €, el dinero estimado al terminar será 800 €, antes de descontar la reserva mensual.' },
      files: { title: 'Archivos: tickets, fotos y audios', what: 'Aquí se guardan los justificantes que envías por Telegram o que están pendientes de revisar.', steps: ['Pulsa Ver para abrir una foto, documento o audio.', 'Pulsa Revisar y confirmar para corregir las líneas.', 'Comprueba que importes y categorías sean correctos.', 'Confirma el ticket para convertir sus líneas en movimientos.'], example: 'Un ticket puede tener leche 2,10 €, fruta 4,50 € y limpieza 6 €. Puedes corregir cada línea antes de confirmarlo.' },
      analytics: { title: 'Diagnóstico: qué necesita tu atención', what: 'Revisa la calidad de tus datos y los riesgos del plan mensual.', steps: ['Elige el mes que quieres revisar.', 'Lee los avisos sobre tickets, justificantes y categorías.', 'Abre el movimiento relacionado para corregirlo.', 'Mira Próximos meses para detectar pagos previstos altos.'], example: 'Si aparece “3 archivos pendientes”, entra en Archivos y confirma cada ticket. El aviso desaparecerá cuando quede registrado.' }
    };
    Object.assign(help, detailedHelp);
    window.financeHelp = detailedHelp;
    Object.entries(help).forEach(([panel, content]) => {
      const heading = document.querySelector('[data-panel="' + panel + '"] .section-heading');
      if (heading && !heading.querySelector('[data-help]')) { const button = document.createElement('button'); button.type = 'button'; button.className = 'help-button'; button.dataset.help = panel; button.textContent = '¿Qué es esto?'; heading.appendChild(button); }
    });
    const analyticsHeading = document.querySelector('[data-panel="analytics"] .section-heading');
    if (analyticsHeading && !analyticsHeading.querySelector('[data-month-step]')) {
      const label = analyticsHeading.querySelector('label');
      if (label) { const wrap = document.createElement('span'); wrap.className = 'inline period-inline'; [-1, 1].forEach(delta => { const button = document.createElement('button'); button.type = 'button'; button.className = 'secondary period-step'; button.dataset.monthStep = 'analytics:' + delta; button.setAttribute('aria-label', delta < 0 ? 'Mes analizado anterior' : 'Mes analizado siguiente'); button.textContent = delta < 0 ? '←' : '→'; wrap.appendChild(button); }); label.after(wrap); }
    }
    if (!$('helpModal')) { const dialog = document.createElement('dialog'); dialog.id = 'helpModal'; dialog.setAttribute('aria-labelledby', 'helpTitle'); dialog.innerHTML = '<div class="dialog-heading"><h2 id="helpTitle">Ayuda</h2><button type="button" class="quiet" data-close="helpModal" aria-label="Cerrar ayuda">✕</button></div><p id="helpWhat"></p><h3>Qué puedes hacer</h3><ul id="helpSteps"></ul><div class="help-example"><strong>Ejemplo</strong><p id="helpExample"></p></div><div class="form-actions"><button type="button" class="secondary" data-close="helpModal">Entendido</button></div>'; document.body.appendChild(dialog); }
  }
  function stepMonth(key, delta) {
    if (key === 'savings') { state.savingsEndMonth = addMonths(state.savingsEndMonth || data.today.slice(0, 7), delta); const min = data.today.slice(0, 7), max = addMonths(min, 35); if (state.savingsEndMonth < min) state.savingsEndMonth = min; if (state.savingsEndMonth > max) state.savingsEndMonth = max; $('savingsEndMonth').value = state.savingsEndMonth; renderSavings(); saveState(); return; }
    const select = $(key === 'month' ? 'monthFilter' : key === 'projection' ? 'projectionMonth' : 'analyticsMonth');
    const values = [...select.options].map(option => option.value).filter(Boolean).sort();
    if (!values.length) return;
    let current = select.value || values[values.length - 1];
    let index = values.indexOf(current); if (index < 0) index = values.length - 1;
    index = Math.max(0, Math.min(values.length - 1, index + delta));
    const value = values[index]; select.value = value;
    if (key === 'month') state.month = value; else if (key === 'projection') state.projectionMonth = value; else state.analyticsMonth = value;
    state.page = 1; render(); saveState();
  }
  function showTab(tab, scroll = true) {
    if (!['dashboard', 'transactions', 'projection', 'savings', 'files', 'analytics'].includes(tab)) tab = 'dashboard';
    state.tab = tab;
    document.querySelectorAll('[data-panel]').forEach(panel => { panel.hidden = panel.dataset.panel !== tab; });
    document.querySelectorAll('[data-tab]').forEach(button => { if (button.dataset.tab === tab) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current'); });
    $('viewTitle').textContent = { dashboard: 'Resumen', transactions: 'Movimientos', projection: 'Proyección', savings: 'Ahorro', files: 'Archivos', analytics: 'Diagnóstico' }[tab];
    $('globalFilters').hidden = ['projection', 'savings', 'analytics'].includes(tab);
    $('moreFilters').hidden = !state.more; $('toggleFilters').setAttribute('aria-expanded', String(state.more)); $('toggleFilters').textContent = state.more ? 'Menos filtros' : 'Más filtros';
    render(); saveState(); if (scroll) window.scrollTo({ top: 0 });
  }
  function activeFilterText() {
    const labels = Object.entries(filterIds).filter(([key]) => !['month', 'search'].includes(key) && state[key]).map(([, id]) => $(id).selectedOptions[0]?.textContent).filter(Boolean);
    if (state.search) labels.push('Búsqueda: ' + state.search);
    $('activeFilters').innerHTML = '<strong>' + esc(monthName(state.month)) + '</strong> · Filtros compartidos' + labels.map(label => ' <span class="chip">' + esc(label) + '</span>').join('');
  }
  function filteredFiles() {
    return data.receipts.filter(file => {
      if (state.month && file.monthKey !== state.month) return false;
      if (state.user && file.user !== state.user) return false;
      if (state.receipt && (state.receipt === 'none' || (state.receipt === 'missing' ? file.fileAvailable : file.attachmentType !== state.receipt))) return false;
      const linked = data.transactions.filter(row => row.receipt === file.path);
      if ([state.category, state.type, state.fixed, state.store].some(Boolean) && !filterRows(linked, { ...state, month: '', search: '', receipt: '', user: '' }).length) return false;
      if (state.search && !normalizeText([file.caption, file.reviewNotes, file.user, ...linked.map(row => row.description)].join(' ')).includes(normalizeText(state.search))) return false;
      if (state.fileStatus === 'pending' && !pending(file)) return false;
      if (state.fileStatus === 'missing' && file.fileAvailable) return false;
      if (['processed', 'duplicado'].includes(state.fileStatus) && file.status !== state.fileStatus) return false;
      return true;
    }).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }
  function render() {
    activeFilterText();
    const rows = filterRows(data.transactions, state);
    if (state.tab === 'dashboard') renderSummary(rows);
    if (state.tab === 'transactions') renderTable(rows);
    if (state.tab === 'projection') renderProjection();
    if (state.tab === 'analytics') renderAnalytics();
    if (state.tab === 'files') renderReceipts();
    if (state.tab === 'savings') renderSavings();
  }
  function renderSummary(rows) {
    const total = totals(rows), balance = total.income - total.expense;
    $('periodLabel').textContent = monthName(state.month);
    $('incomeTotal').textContent = money(total.income); $('expenseTotal').textContent = money(total.expense); $('balanceTotal').textContent = money(balance);
    $('balanceTotal').className = balance < 0 ? 'expense' : 'income';
    $('balanceMeta').textContent = 'Ingresos − gastos del periodo seleccionado';
    $('fixedTotal').textContent = money(total.fixed); $('variableTotal').textContent = money(total.expense - total.fixed);
    const expenses = rows.filter(row => row.kind === 'expense'), justified = expenses.filter(row => row.fileAvailable && ['imagen', 'documento'].includes(row.attachmentType));
    $('ticketCoverage').textContent = expenses.length ? Math.round(justified.length / expenses.length * 100) + '% · ' + justified.length + '/' + expenses.length + ' líneas' : 'Sin gastos';
    // Status is a separate files-view choice, not a hidden dashboard filter.
    const fileStatus = state.fileStatus; state.fileStatus = ''; const files = filteredFiles(); state.fileStatus = fileStatus;
    const pendingFiles = files.filter(pending).length;
    $('pendingCount').textContent = pendingFiles;
    $('quickPendingCount').textContent = pendingFiles ? '· ' + pendingFiles : '';
    const forecast = data.projections.months.find(row => row.monthKey === state.month);
    if (forecast) {
      const estimate = projectionEstimate(forecast);
      $('closingEstimate').textContent = money(estimate);
      $('closingEstimate').className = estimate < 0 ? 'expense' : 'income';
      $('closingEstimateMeta').textContent = 'Resultado registrado + cobros pendientes − pagos pendientes';
    } else {
      $('closingEstimate').textContent = 'Sin previsión disponible';
      $('closingEstimate').className = '';
      $('closingEstimateMeta').textContent = 'Añade cobros y pagos esperados en Proyección.';
    }
    for (const kind of ['income', 'expense']) {
      const node = $(kind + 'Delta'); node.textContent = '';
      if (!state.month) continue;
      const previous = previousMonthKey(state.month), priorRows = filterRows(data.transactions, state, previous);
      if (!priorRows.length) { node.textContent = 'Sin registros comparables en ' + monthName(previous); continue; }
      const before = totals(priorRows)[kind], difference = total[kind] - before;
      // El verde siempre significa "va a mejor": más ingresos o menos gastos.
      const better = kind === 'income' ? difference >= 0 : difference <= 0;
      const percent = before ? ' · ' + Math.round(Math.abs(difference) / before * 100) + '%' : '';
      node.innerHTML = '<span class="delta ' + (better ? 'up' : 'down') + '">' + (difference >= 0 ? '▲' : '▼') + ' ' + esc(money(Math.abs(difference)) + percent) + '</span><span>frente a ' + esc(monthName(previous)) + (state.month === data.today.slice(0, 7) ? ' · mes en curso' : '') + '</span>';
    }
    const alerts = [];
    if (balance < 0) alerts.push(notice('Resultado negativo', 'Los gastos registrados superan los ingresos en ' + money(-balance) + '.', 'danger'));
    if (files.some(pending)) alerts.push(notice('Archivos por revisar', files.filter(pending).length + ' archivos del filtro requieren atención.', 'warn', '<button class="secondary" data-go="files">Revisar archivos</button>'));
    if (state.requireReceipts) {
      const missing = expenses.length - justified.length;
      if (missing) alerts.push(notice('Gastos sin justificante disponible', missing + ' líneas no tienen una imagen o documento accesible. Comprueba cuáles necesitan justificante.', 'warn', '<button class="secondary" data-go="transactions">Ver movimientos</button>'));
    }
    const uncertain = rows.filter(row => row.inferenceNotes.length);
    if (uncertain.length) alerts.push(notice('Clasificaciones por revisar', uncertain.length + ' movimientos conservan avisos de interpretación.', 'warn', '<button class="secondary" data-go="analytics">Ver avisos</button>'));
    $('alertsList').innerHTML = alerts.join('') || empty('Sin avisos para este filtro.');
    const recent = [...rows].sort((a, b) => b.createdAt.localeCompare(a.createdAt) || b.id - a.id).slice(0, 5);
    $('recentList').innerHTML = recent.map(compactRow).join('') || empty('No hay movimientos. Cambia el periodo o limpia los filtros.');
    const incomes = rows.filter(row => row.kind === 'income').sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    $('incomeListTotal').textContent = money(total.income); $('incomeList').innerHTML = incomes.map(compactRow).join('') || empty('Sin ingresos registrados en este filtro.');
    drawDailyExpenseChart(expenses); drawCategoryChart(expenses);
  }
  function alertNotice(alert) {
    // El aviso cita el movimiento concreto (#575); abrir ese y no el primero vinculado.
    const cited = /#(\d+)/.exec(alert.text), id = cited ? cited[1] : alert.id;
    return notice(alert.title, alert.text, 'warn', id ? '<button class="secondary" data-edit="' + id + '">Revisar movimiento</button>' : '<button class="secondary" data-review-projection="' + alert.projection + '">Revisar concepto</button>');
  }
  function alertsHtml(alerts) {
    // Tres o mas avisos con el mismo mensaje se muestran como un grupo plegable.
    const groups = new Map();
    alerts.forEach(alert => { const key = alert.text.replace(/#\d+/g, '#'); if (!groups.has(key)) groups.set(key, []); groups.get(key).push(alert); });
    return [...groups.values()].map(items => items.length < 3 ? items.map(alertNotice).join('') : '<details class="alert-group"><summary><strong>' + items.length + ' avisos</strong> · ' + esc(items[0].text.replace(/\s*#\d+/g, '')) + '</summary>' + items.map(alertNotice).join('') + '</details>').join('');
  }
  function notice(title, text, tone = '', action = '') { return '<div class="notice ' + tone + '"><strong>' + esc(title) + '</strong><p>' + esc(text) + '</p>' + action + '</div>'; }
  function compactRow(row) { return '<div class="compact-row"><div>' + avatar(row) + '<div><button data-edit="' + row.id + '">' + esc(row.description) + '</button><small>' + esc(row.date + ' · ' + row.user) + '</small></div></div><strong class="' + row.kind + '">' + (row.kind === 'expense' ? '−' : '+') + esc(money(row.amountCents)) + '</strong></div>'; }
  function drawDailyExpenseChart(rows) {
    $('dailyExpenseMeta').textContent = money(sum(rows));
    if (!rows.length) { $('monthlyChart').innerHTML = empty('No hay gastos registrados en este periodo.'); return; }
    const map = new Map(groupTotals(rows, 'dateIso'));
    let days;
    if (state.month) {
      const [y, m] = state.month.split('-').map(Number); const count = new Date(Date.UTC(y, m, 0)).getUTCDate();
      days = Array.from({ length: count }, (_, i) => [state.month + '-' + String(i + 1).padStart(2, '0'), map.get(state.month + '-' + String(i + 1).padStart(2, '0')) || 0]);
    } else days = [...map].sort((a, b) => a[0].localeCompare(b[0]));
    const max = Math.max(1, ...days.map(([, value]) => value));
    // Curva de área: la silueta del mes se lee antes que cualquier tabla.
    const width = 660, height = 200, top = 16, bottom = 26, base = height - bottom;
    const px = index => days.length < 2 ? width / 2 : index * width / (days.length - 1);
    const py = value => top + (1 - value / max) * (base - top);
    const line = days.map(([, value], index) => px(index).toFixed(1) + ',' + py(value).toFixed(1)).join(' ');
    const grid = [0, 0.5, 1].map(ratio => '<line class="grid-line" x1="0" x2="' + width + '" y1="' + py(max * ratio).toFixed(1) + '" y2="' + py(max * ratio).toFixed(1) + '"/>').join('');
    const hits = days.map(([date, value], index) => '<rect x="' + Math.max(0, px(index) - width / days.length / 2).toFixed(1) + '" y="0" width="' + (width / days.length).toFixed(1) + '" height="' + height + '" fill="transparent"><title>' + esc(date.split('-').reverse().join('/') + ': ' + money(value)) + '</title></rect>').join('');
    const peak = days.reduce((best, [date, value], index) => value > best.value ? { value, index, date } : best, { value: 0, index: 0, date: days[0][0] });
    const marker = peak.value ? '<circle cx="' + px(peak.index).toFixed(1) + '" cy="' + py(peak.value).toFixed(1) + '" r="4.5" fill="var(--surface)" stroke="var(--accent)" stroke-width="2.5"/>' : '';
    const dayLabel = date => date.slice(8) + '/' + date.slice(5, 7);
    $('monthlyChart').innerHTML = '<svg class="chart-svg" viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="' + esc('Gasto por día. Máximo ' + money(peak.value) + ' el ' + dayLabel(peak.date)) + '">'
      + '<defs><linearGradient id="dailyFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="var(--accent)" stop-opacity=".32"/><stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>'
      + grid + '<polygon points="0,' + base + ' ' + line + ' ' + width + ',' + base + '" fill="url(#dailyFill)"/>'
      + '<polyline points="' + line + '" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>' + marker + hits + '</svg>'
      + '<div class="chart-caption"><span>' + esc(dayLabel(days[0][0])) + '</span><span>Día más alto: ' + esc(dayLabel(peak.date) + ' · ' + money(peak.value)) + '</span><span>' + esc(dayLabel(days.at(-1)[0])) + '</span></div>'
      + '<details><summary>Consultar importes por día</summary><table><thead><tr><th scope="col">Fecha</th><th scope="col">Gasto registrado</th></tr></thead><tbody>' + days.map(([date, value]) => '<tr><td>' + date.split('-').reverse().join('/') + '</td><td>' + esc(money(value)) + '</td></tr>').join('') + '</tbody></table></details>';
  }
  function drawCategoryChart(rows) {
    const groups = groupTotals(rows, 'category');
    if (!groups.length) { $('categoryChart').innerHTML = empty('Sin gastos para mostrar.'); return; }
    const total = groups.reduce((amount, [, value]) => amount + value, 0) || 1;
    // Siete arcos con color propio y el resto agrupado: más de eso deja de distinguirse.
    const head = groups.slice(0, 7), tail = groups.slice(7);
    const slices = tail.length ? [...head, ['Otras categorías', tail.reduce((amount, [, value]) => amount + value, 0)]] : head;
    const radius = 52, circumference = 2 * Math.PI * radius;
    let travelled = 0;
    const arcs = slices.map(([label, value], index) => {
      const length = Math.max(0, value / total * circumference - 2);
      const arc = '<circle cx="70" cy="70" r="' + radius + '" fill="none" stroke="' + swatch(index) + '" stroke-width="21" stroke-dasharray="' + length.toFixed(2) + ' ' + (circumference - length).toFixed(2) + '" stroke-dashoffset="' + (-travelled).toFixed(2) + '" transform="rotate(-90 70 70)"><title>' + esc(label + ': ' + money(value)) + '</title></circle>';
      travelled += value / total * circumference;
      return arc;
    }).join('');
    const legend = slices.map(([label, value], index) => '<div class="legend-item"><span class="legend-dot" style="background:' + swatch(index) + '"></span><span>' + esc(label) + '</span><strong>' + esc(money(value)) + '<em>' + Math.round(value / total * 100) + '%</em></strong></div>').join('');
    const rest = tail.length ? '<details><summary>' + (tail.length === 1 ? 'Ver la categoría agrupada' : 'Ver las ' + tail.length + ' categorías agrupadas') + '</summary><table><tbody>' + tail.map(([label, value]) => '<tr><td>' + esc(label) + '</td><td>' + esc(money(value)) + '</td></tr>').join('') + '</tbody></table></details>' : '';
    $('categoryChart').innerHTML = '<div class="donut-wrap"><svg class="donut" viewBox="0 0 140 140" role="img" aria-label="' + esc('Reparto del gasto por categoría, total ' + money(total)) + '">' + arcs
      + '<text class="donut-total" x="70" y="69" text-anchor="middle">' + esc(money(total)) + '</text><text class="donut-label" x="70" y="84" text-anchor="middle">GASTO TOTAL</text></svg>'
      + '<div class="chart-legend">' + legend + '</div></div>' + rest;
  }
  function renderTable(rows) {
    const total = totals(rows); let entries = state.view === 'purchases' ? groupPurchases(rows) : rows;
    entries = [...entries].sort((a, b) => state.sort.startsWith('amount') ? (a.amountCents - b.amountCents) * (state.sort.endsWith('asc') ? 1 : -1) || b.id - a.id : (a.createdAt.localeCompare(b.createdAt) || a.id - b.id) * (state.sort.endsWith('asc') ? 1 : -1));
    const size = Number(state.pageSize), pages = Math.max(1, Math.ceil(entries.length / size)); state.page = Math.min(pages, Math.max(1, state.page));
    $('transactionsTotals').textContent = rows.length + ' líneas · ' + (state.view === 'purchases' ? entries.length + ' compras/entradas · ' : '') + 'Resultado ' + money(total.income - total.expense);
    $('transactionsBody').innerHTML = entries.slice((state.page - 1) * size, state.page * size).map(movementRow).join('') || empty('No hay coincidencias. Prueba con otro periodo o limpia los filtros.');
    $('pageLabel').textContent = 'Página ' + state.page + ' de ' + pages; $('prevPage').disabled = state.page <= 1; $('nextPage').disabled = state.page >= pages;
  }
  function movementRow(row) {
    const grouped = row.children?.length > 1;
    const title = grouped ? (row.store || 'Compra / archivo') + ' · ' + row.children.length + (row.children.length === 1 ? ' producto' : ' productos') : row.description;
    const attachment = row.receipt ? '<button class="quiet" data-attachment="transactions:' + row.id + '">' + esc(row.fileAvailable ? 'Ver ' + row.attachmentType : 'Adjunto no disponible') + '</button>' : '<span>Sin adjunto</span>';
    const categories = grouped ? unique(row.children.map(r => r.category)).join(', ') : row.category;
    return '<article class="movement"><div class="movement-main">' + avatar(row) + '<time datetime="' + row.dateIso + '">' + esc(row.date) + '</time><div><div class="movement-title">' + esc(title) + '</div><div class="movement-meta"><span data-meta="category">' + esc(categories) + '</span><span data-meta="store">' + esc(row.store || 'Sin comercio') + '</span><span data-meta="user">' + esc(row.user) + '</span><span>' + (row.isFixed ? 'Fijo' : 'Variable') + '</span>' + attachment + (row.inferenceNotes.length ? '<span class="expense">Revisar clasificación</span>' : '') + '</div></div><div class="movement-amount ' + row.kind + '">' + (row.kind === 'expense' ? '−' : '+') + esc(money(row.amountCents)) + '</div>' + (!grouped ? '<button class="secondary" data-edit="' + row.id + '">' + (data.editable ? 'Editar' : 'Ver detalle') + '</button>' : '') + '</div>' + (grouped ? '<details><summary>Ver productos y editar líneas</summary>' + row.children.map(movementRow).join('') + '</details>' : '') + '</article>';
  }
  function metric(label, amount, detail = '') { return '<article class="metric"><span>' + esc(label) + '</span><strong class="' + (amount < 0 ? 'expense' : '') + '">' + esc(money(amount)) + '</strong><small>' + esc(detail) + '</small></article>'; }
  function renderProjection() {
    const month = state.projectionMonth, summary = data.projections.months.find(row => row.monthKey === month);
    if (!summary) { $('projectionBalance').innerHTML = empty('Sin proyección para este mes.'); $('projectionSummary').innerHTML = ''; $('cashflowGrid').innerHTML = ''; $('projectionBody').innerHTML = ''; return; }
    const monthRows = data.projections.rows.filter(row => row.month === month);
    const all = monthRows.filter(row => row.status !== 'skipped'), estimate = projectionEstimate(summary);
    const pendingIncome = projectionFlowEntries(all, 'income', 'pending');
    const pendingExpense = projectionFlowEntries(all, 'expense', 'pending');
    const completedIncome = projectionFlowEntries(all, 'income', 'completed');
    const completedExpense = projectionFlowEntries(all, 'expense', 'completed');
    const dueIncome = pendingIncome.reduce((total, entry) => total + entry.amount, 0);
    const dueExpense = pendingExpense.reduce((total, entry) => total + entry.amount, 0);
    const pendingDifference = dueIncome - dueExpense;
    const balanceTone = pendingDifference < 0 ? 'negative' : pendingDifference > 0 ? 'positive' : 'neutral';
    const balanceTitle = pendingDifference < 0
      ? 'Lo que falta cobrar no cubre todos los pagos: faltan ' + money(-pendingDifference)
      : pendingDifference > 0
        ? 'Después de cubrir los pagos pendientes, quedarían ' + money(pendingDifference)
        : 'Los cobros pendientes cubren exactamente los pagos pendientes';
    $('projectionBalance').className = 'pending-balance ' + balanceTone;
    $('projectionBalance').innerHTML = '<span class="balance-icon" aria-hidden="true">' + (pendingDifference < 0 ? '−' : pendingDifference > 0 ? '+' : '=') + '</span><div><strong>' + esc(balanceTitle) + '</strong><p>Por cobrar: ' + esc(money(dueIncome)) + ' · Por pagar: ' + esc(money(dueExpense)) + '. Compara solo lo pendiente de ' + esc(monthName(month)) + '; no es el saldo del banco.</p></div>';
    $('projectionSummary').innerHTML = metric('Resultado del plan completo', summary.projectedBalanceCents, 'Todo lo previsto para el mes') + metric('Resultado registrado hasta hoy', summary.actualBalanceCents, 'Solo movimientos que ya registraste') + metric('Resultado estimado al terminar' + (all.some(row => row.linkWarnings.length) ? ' · por revisar' : ''), estimate, 'Registrado + cobros pendientes − pagos pendientes');
    $('cashflowGrid').innerHTML = flowCard('Me falta cobrar', pendingIncome, 'pending-income', '+', 'Marcar como cobrado')
      + flowCard('Me falta pagar', pendingExpense, 'pending-expense', '−', 'Marcar como pagado')
      + flowCard('Ya cobrado', completedIncome, 'completed completed-income', '✓', 'Volver a pendiente')
      + flowCard('Ya pagado', completedExpense, 'completed completed-expense', '✓', 'Volver a pendiente');
    const rows = monthRows.filter(row => !state.projectionStatus || (state.projectionStatus === 'review' ? row.linkWarnings.length : row.status === state.projectionStatus));
    $('projectionBody').innerHTML = rows.map(row => {
      const actual = row.tracksActualCategory ? row.actualSpentCents : row.actualLinkedCents;
      const realLabel = row.tracksActualCategory || row.linkedTransactionIds.length ? money(actual) : 'Sin vínculo';
      const note = row.storedNote && !row.storedNote.startsWith('Marcado automaticamente') ? '<p class="muted small">' + esc(row.storedNote) + '</p>' : '';
      return '<article class="projection-row"><div class="projection-row-head"><div><h3>' + esc(row.name) + '</h3><span class="muted small">' + esc(row.category + (row.installmentTotal ? ' · cuota ' + row.installmentLabel + ' · ' + row.remainingLabel : ' · mensual') + (row.endMonth ? ' · hasta ' + monthName(row.endMonth) : '')) + '</span></div><span class="status-chip ' + row.status + '">' + esc(row.statusLabel) + '</span></div><div class="projection-values"><span>Importe previsto<strong>' + esc(money(row.amountCents)) + '</strong></span><span>Importe registrado<strong>' + esc(realLabel) + '</strong></span><span>Diferencia<strong>' + (row.tracksActualCategory || row.linkedTransactionIds.length ? esc(money(actual - row.amountCents)) : '—') + '</strong></span></div>' + (row.tracksActualCategory ? '<p class="muted small">Disponible dentro de este presupuesto: ' + esc(money(Math.max(0, row.remainingBudgetCents))) + (row.remainingBudgetCents < 0 ? ' · Te has pasado en ' + esc(money(-row.remainingBudgetCents)) : '') + '</p>' : '') + note + (row.linkWarnings.length ? notice('Revisar este dato', row.linkWarnings.join(' · '), 'warn') : '') + '<div class="projection-links">' + row.linkedTransactionIds.map(id => '<button class="quiet" data-edit="' + id + '">Revisar movimiento #' + id + '</button>').join('') + '</div><div class="row-actions">' + (data.editable ? quickStatusButton(row) + '<button class="secondary" data-projection="' + row.templateId + '">Editar concepto</button>' + (row.status !== 'skipped' ? '<button class="quiet" data-omit="' + row.templateId + '">Omitir este mes</button>' : '') + (!row.endMonth || row.endMonth >= row.month ? '<button class="quiet" data-end-projection="' + row.templateId + ':' + row.month + '">Finalizar desde este mes</button>' : '') : '') + '</div></article>';
    }).join('') || empty('No hay conceptos con este estado.');
    drawTrendChart();
  }
  function projectionFlowEntries(rows, kind, bucket) {
    const entries = [];
    rows.filter(row => row.kind === kind).forEach(row => {
      if (row.tracksActualCategory) {
        if (bucket === 'pending' && row.remainingBudgetCents > 0) entries.push({ row, amount: row.remainingBudgetCents, name: row.name + ' · por gastar' });
        if (bucket === 'completed' && row.actualSpentCents > 0) entries.push({ row, amount: row.actualSpentCents, name: row.name + ' · ya gastado' });
        return;
      }
      if (row.status !== bucket) return;
      const amount = bucket === 'pending' ? Math.max(0, row.amountCents - row.actualLinkedCents) : (row.linkedTransactionIds.length ? row.actualLinkedCents : row.amountCents);
      if (amount > 0) entries.push({ row, amount, name: row.name });
    });
    return entries.sort((a, b) => b.amount - a.amount || a.name.localeCompare(b.name, 'es'));
  }
  function flowCard(title, entries, classes, icon, actionLabel) {
    const total = entries.reduce((amount, entry) => amount + entry.amount, 0);
    const visible = entries.slice(0, 5), rest = entries.slice(5);
    const list = visible.map(entry => flowItem(entry, actionLabel)).join('') || empty(title.startsWith('Me falta') ? 'No hay nada pendiente en este bloque.' : 'Aún no hay conceptos en este bloque.');
    const more = rest.length ? '<details class="flow-more"><summary>Mostrar ' + rest.length + ' conceptos más</summary>' + rest.map(entry => flowItem(entry, actionLabel)).join('') + '</details>' : '';
    return '<article class="flow-card ' + classes + '"><header class="flow-head"><h2><span aria-hidden="true">' + icon + '</span>' + esc(title) + '</h2><strong class="flow-total">' + esc(money(total)) + '</strong></header><div class="flow-list">' + list + more + '</div></article>';
  }
  function flowItem(entry, actionLabel) {
    const row = entry.row;
    const linkedText = row.linkedTransactionIds.length ? 'Movimiento registrado: ' + money(row.actualLinkedCents) : (row.status === 'completed' && !row.tracksActualCategory ? 'Marcado manualmente, sin movimiento asociado.' : 'Sin movimiento registrado todavía.');
    const warnings = row.linkWarnings.length ? '<p class="expense">Revisar: ' + esc(row.linkWarnings.join(' · ')) + '</p>' : '';
    const details = '<details><summary>Ver detalles</summary><div class="flow-item-detail"><p>' + esc(row.category) + (row.installmentTotal ? ' · cuota ' + esc(row.installmentLabel) + ' · ' + esc(row.remainingLabel) : '') + '</p><p>Previsto: ' + esc(money(row.amountCents)) + '. ' + esc(linkedText) + '</p>' + warnings + '<div class="row-actions">' + row.linkedTransactionIds.map(id => '<button class="quiet" data-edit="' + id + '">Ver movimiento #' + id + '</button>').join('') + (data.editable ? '<button class="quiet" data-projection="' + row.templateId + '">Editar concepto</button>' : '') + '</div></div></details>';
    let action = '';
    if (data.editable && !row.tracksActualCategory) {
      const target = row.status === 'pending' ? 'completed' : 'pending';
      action = '<button class="secondary" data-status="' + row.templateId + ':' + target + '">' + esc(actionLabel) + '</button>';
    }
    return '<div class="flow-item"><div class="flow-item-main"><span class="flow-item-name">' + esc(entry.name) + '</span><strong class="flow-item-amount">' + esc(money(entry.amount)) + '</strong><div class="flow-item-action">' + action + details + '</div></div></div>';
  }
  function quickStatusButton(row) {
    if (row.tracksActualCategory) return '';
    const status = row.status === 'pending' ? 'completed' : 'pending';
    return '<button class="secondary" data-status="' + row.templateId + ':' + status + '">' + (status === 'completed' ? 'Marcar como ' + (row.kind === 'income' ? 'cobrado' : 'pagado') : 'Volver a pendiente') + '</button>';
  }
  function drawTrendChart() {
    const months = data.projections.months;
    if (!months.length) { $('trendChart').innerHTML = empty('Todavía no hay meses proyectados.'); return; }
    const max = Math.max(1, ...months.flatMap(row => [row.projectedIncomeCents, row.projectedExpenseCents]));
    // Barras enfrentadas por mes: se compara plan de ingresos contra plan de gastos.
    const width = 720, height = 240, top = 14, bottom = 46, base = height - bottom;
    const slot = width / months.length, barWidth = Math.min(15, slot / 3.2);
    const scale = value => (value / max) * (base - top);
    const grid = [0, 0.5, 1].map(ratio => '<line class="grid-line" x1="0" x2="' + width + '" y1="' + (base - scale(max * ratio)).toFixed(1) + '" y2="' + (base - scale(max * ratio)).toFixed(1) + '"/>').join('');
    const bar = (x, value, colour, title) => '<rect x="' + x.toFixed(1) + '" y="' + (base - scale(value)).toFixed(1) + '" width="' + barWidth.toFixed(1) + '" height="' + Math.max(1, scale(value)).toFixed(1) + '" rx="3" fill="' + colour + '"><title>' + esc(title) + '</title></rect>';
    const bars = months.map((row, index) => {
      const centre = slot * index + slot / 2, name = monthName(row.monthKey);
      return bar(centre - barWidth - 1.5, row.projectedIncomeCents, 'var(--accent)', name + ' · ingresos previstos ' + money(row.projectedIncomeCents))
        + bar(centre + 1.5, row.projectedExpenseCents, 'var(--danger)', name + ' · gastos previstos ' + money(row.projectedExpenseCents))
        + '<text class="axis-text" x="' + centre.toFixed(1) + '" y="' + (base + 17) + '" text-anchor="middle">' + esc(shortMonth(row.monthKey)) + '</text>'
        + '<text class="axis-text" x="' + centre.toFixed(1) + '" y="' + (base + 32) + '" text-anchor="middle" fill="' + (row.projectedBalanceCents < 0 ? 'var(--danger)' : 'var(--accent-strong)') + '">' + esc(compactMoney(row.projectedBalanceCents)) + '</text>';
    }).join('');
    $('trendChart').innerHTML = '<div class="chart-legend" style="grid-template-columns:repeat(2,max-content);margin-bottom:12px"><div class="legend-item"><span class="legend-dot" style="background:var(--accent)"></span><span>Ingresos previstos</span></div><div class="legend-item"><span class="legend-dot" style="background:var(--danger)"></span><span>Gastos previstos</span></div></div>'
      + '<svg class="chart-svg" viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="Ingresos y gastos previstos de los próximos meses">' + grid + bars + '</svg>'
      + '<p class="muted small">La cifra bajo cada mes es el resultado previsto.</p>'
      + '<details><summary>Consultar las cifras exactas</summary><table><thead><tr><th scope="col">Mes</th><th scope="col">Ingresos</th><th scope="col">Gastos</th><th scope="col">Resultado</th></tr></thead><tbody>' + months.map(row => '<tr><td>' + esc(monthName(row.monthKey)) + '</td><td>' + esc(money(row.projectedIncomeCents)) + '</td><td>' + esc(money(row.projectedExpenseCents)) + '</td><td>' + esc(money(row.projectedBalanceCents)) + '</td></tr>').join('') + '</tbody></table></details>';
  }
  function aiAlertsForMonth(month) {
    const alerts = [];
    data.transactions.filter(row => row.monthKey === month).forEach(row => {
      row.inferenceNotes.forEach(note => alerts.push({ title: '#' + row.id + ' · ' + row.description, text: note, id: row.id }));
      if (!row.projectionTemplateId && row.reviewStatus !== 'reviewed' && (row.isFixed || /sueldo|nomina/.test(normalizeText(row.description)))) alerts.push({ title: '#' + row.id + ' · sin proyección vinculada', text: 'Revisa si corresponde a un concepto previsto. No se ha vinculado automáticamente.', id: row.id });
    });
    data.projections.rows.filter(row => row.month === month).forEach(row => row.linkWarnings.forEach(text => alerts.push({ title: row.name, text, id: row.linkedTransactionIds[0], projection: row.templateId })));
    return alerts;
  }
  function renderAnalytics() {
    const month = state.analyticsMonth, rows = data.transactions.filter(row => row.monthKey === month), total = totals(rows);
    const summary = data.projections.months.find(row => row.monthKey === month), alerts = aiAlertsForMonth(month);
    const files = data.receipts.filter(row => row.monthKey === month && pending(row));
    const projected = data.projections.rows.filter(row => row.month === month && row.status !== 'skipped');
    const fixedPlan = sum(projected.filter(row => row.kind === 'expense' && !row.tracksActualCategory));
    $('analyticsNarrative').innerHTML = notice('Alcance del análisis', rows.length + ' líneas registradas en ' + monthName(month) + '. ' + (month === data.today.slice(0, 7) ? 'El mes está incompleto. ' : '') + 'Los meses sin registros no equivalen a cero actividad.', files.length || !rows.length ? 'warn' : '');
    const recommendations = [], deductions = [];
    if (summary && projected.length && summary.projectedIncomeCents > 0) {
      const margin = summary.projectedBalanceCents / summary.projectedIncomeCents, ratio = fixedPlan / summary.projectedIncomeCents;
      deductions.push(['Margen previsto', margin < 0 ? 35 : margin < .08 ? 18 : margin < .15 ? 8 : 0]);
      deductions.push(['Peso de conceptos recurrentes previstos', ratio > .7 ? 22 : ratio > .5 ? 10 : 0]);
      deductions.push(['Archivos pendientes del mes', Math.min(12, files.length * 4)]);
      deductions.push(['Avisos de clasificación o conciliación', Math.min(20, alerts.length * 2)]);
      $('analyticsScore').textContent = Math.max(0, 100 - deductions.reduce((n, [, value]) => n + value, 0)) + '/100';
      $('scoreBreakdown').innerHTML = '<p class="small muted">Base: 100. Margen: −35 si es negativo, −18 si es inferior al 8%, −8 si es inferior al 15%. Recurrentes/ingresos: −22 por encima del 70%, −10 por encima del 50%. Pendientes: −4 por archivo (máximo 12). Avisos: −2 por aviso (máximo 20). Son umbrales internos orientativos.</p>' + deductions.map(([label, points]) => '<div class="score-line"><span>' + esc(label) + '</span><strong>−' + points + '</strong></div>').join('');
      recommendations.push(notice('Previsión frente a registros', 'Plan: ' + money(summary.projectedBalanceCents) + '. Resultado registrado: ' + money(total.income - total.expense) + '. Cierre estimado: ' + money(projectionEstimate(summary)) + '.', alerts.length ? 'warn' : ''));
      if (alerts.length) recommendations.push(notice('Primero, conciliar', 'Hay diferencias o estados sin verificar. Revisa los avisos antes de usar la estimación de cierre.', 'warn'));
      if (margin < 0) recommendations.push(notice('El plan supera los ingresos previstos', 'La diferencia prevista es ' + money(-summary.projectedBalanceCents) + '. Revisa importes y fechas de los conceptos pendientes.', 'warn'));
    } else {
      $('analyticsScore').textContent = 'Sin base suficiente'; $('scoreBreakdown').innerHTML = '<p>Se necesita una proyección con ingresos mayores que cero para calcular el índice.</p>';
      recommendations.push(notice('Sin plan comparable', 'Puedes consultar los movimientos de este mes; no hay ingresos previstos suficientes para calcular un margen.'));
    }
    const top = groupTotals(rows.filter(row => row.kind === 'expense'), 'category')[0];
    if (top) recommendations.push(notice('Mayor gasto registrado: ' + top[0], money(top[1]) + (['Alquiler', 'Deudas', 'Suministros'].includes(top[0]) ? '. Comprueba el importe y su correspondencia con el concepto mensual.' : '. Consulta el detalle por compra para entender su composición.')));
    const ending = projected.filter(row => row.installmentTotal > 1 && row.remainingInstallments <= 2);
    if (ending.length) recommendations.push(notice('Cuotas próximas a terminar', ending.map(row => row.name + ': ' + row.remainingLabel.toLowerCase()).join(' · ')));
    $('analyticsRecommendations').innerHTML = recommendations.join('');
    $('analyticsAiAlertsCount').textContent = alerts.length + ' avisos';
    $('analyticsAiAlerts').innerHTML = alertsHtml(alerts) || empty('Sin avisos de interpretación o conciliación en este mes.');
    $('analyticsFutureBody').innerHTML = data.projections.months.filter(row => row.monthKey >= month).map(row => '<div class="compact-row"><div>' + esc(monthName(row.monthKey)) + '<small>Ingresos ' + esc(money(row.projectedIncomeCents)) + ' · gastos ' + esc(money(row.projectedExpenseCents)) + '</small></div><strong class="' + (row.projectedBalanceCents < 0 ? 'expense' : 'income') + '">' + esc(money(row.projectedBalanceCents)) + '</strong></div>').join('') || empty('Sin meses futuros proyectados.');
  }
  function renderReceipts() {
    const files = filteredFiles(); $('fileCount').textContent = files.length + ' archivos coinciden con los filtros';
    const labels = { nuevo: 'Pendiente', pending: 'Pendiente', voice_pending: 'Audio por revisar', dudoso: 'Revisión manual', processed: 'Procesado', duplicado: 'Duplicado', missing: 'No disponible' };
    $('receiptList').innerHTML = files.slice(0, state.filesShown).map(file => {
      const rows = data.transactions.filter(row => row.receipt === file.path);
      const currentCaption = file.status === 'processed' ? (rows.length ? rows.length + (rows.length === 1 ? ' línea registrada · ' : ' líneas registradas · ') + money(sum(rows)) : 'Procesado · sin movimientos vinculados') : (file.caption || 'Archivo pendiente de revisión');
      const visual = file.attachmentType === 'imagen' && file.fileAvailable ? '<img loading="lazy" src="' + esc(file.url) + '" alt="Miniatura del archivo #' + file.id + '">' : '<div class="file-thumb" aria-hidden="true">' + (file.attachmentType === 'audio' ? 'AUDIO' : 'DOC') + '</div>';
      const reviewAction = rows.length ? '' : '<button class="quiet" data-review="' + file.id + '">Revisar y confirmar</button>';
      return '<article class="file-card">' + visual + '<h3>' + esc(currentCaption) + '</h3><span class="status-chip ' + (file.status === 'processed' ? 'completed' : 'pending') + '">' + esc(file.fileAvailable ? labels[file.status] || file.status : 'Archivo no disponible') + '</span><p class="muted small">#' + file.id + ' · ' + esc(file.user + ' · recibido ' + file.date) + '</p><button class="secondary" data-attachment="receipts:' + file.id + '">Ver ' + esc(file.attachmentType) + '</button>' + reviewAction + (rows.length ? '<details><summary>Movimientos vinculados (' + rows.length + ')</summary>' + rows.map(row => '<button class="quiet" data-edit="' + row.id + '">' + esc(row.description + ' · ' + money(row.amountCents)) + '</button>').join('') + '</details>' : '') + '<details><summary>Historial y ubicación</summary><p>' + esc(file.caption) + '</p><p>' + esc(file.reviewNotes) + '</p><code>' + esc(file.path) + '</code></details></article>';
    }).join('') || empty('No hay archivos para estos filtros.');
    $('moreFiles').hidden = files.length <= state.filesShown;
  }
  function renderSavings() {
    const savings = data.savings || { currentBalanceCents: 0, includeCurrentBalance: false, emergencyMonthlyCents: 0, wallets: [] };
    const start = state.savingsStartMonth || data.today.slice(0, 7), end = state.savingsEndMonth || addMonths(start, 2);
    const months = data.projections.months.filter(row => row.monthKey >= start && row.monthKey <= end);
    const wallets = (savings.wallets || []).filter(wallet => wallet.active);
    const walletsTotal = wallets.filter(wallet => wallet.includeInProjection).reduce((total, wallet) => total + wallet.balanceCents, 0);
    const currentIncluded = savings.includeCurrentBalance ? savings.currentBalanceCents : 0;
    let accumulated = currentIncluded + walletsTotal;
    const rows = months.map((summary, index) => {
      const future = summary.monthKey > data.today.slice(0, 7);
      const income = future ? summary.projectedIncomeCents : summary.pendingIncomeCents;
      const expense = future ? summary.projectedExpenseCents : summary.pendingExpenseCents;
      const reserve = savings.emergencyMonthlyCents || 0;
      const result = income - expense - reserve;
      accumulated += result;
      return { ...summary, income, expense, reserve, result, accumulated, future };
    });
    const generated = rows.reduce((total, row) => total + row.result, 0);
    const finalAmount = accumulated;
    $('savingsPanel').dataset.savingsFinal = String(finalAmount);
    const deficit = finalAmount < 0;
    $('savingsSummary').innerHTML = metric('Dinero estimado al terminar', finalAmount, deficit ? 'Te faltaría ' + money(-finalAmount) : 'Saldo incluido + ahorro previsto') + metric('Ahorro nuevo del periodo', generated, 'Ingresos pendientes − gastos pendientes − reserva') + metric('Saldo actual incluido', currentIncluded, savings.includeCurrentBalance ? 'Actualizado ' + (savings.currentBalanceDate || 'sin fecha') : 'No incluido') + metric('Carteras incluidas', walletsTotal, wallets.filter(wallet => wallet.includeInProjection).length + ' cartera(s) seleccionada(s)');
    $('savingsSettingsForm').elements.currentBalance.value = (savings.currentBalanceCents / 100).toFixed(2).replace('.', ','); $('savingsSettingsForm').elements.currentBalanceDate.value = savings.currentBalanceDate || data.today; $('savingsSettingsForm').elements.includeCurrentBalance.checked = savings.includeCurrentBalance; $('savingsSettingsForm').elements.emergencyMonthly.value = (savings.emergencyMonthlyCents / 100).toFixed(2).replace('.', ',');
    $('walletsBody').innerHTML = wallets.map(wallet => '<div class="compact-row"><div><strong>' + esc(wallet.name) + '</strong><small>' + (wallet.includeInProjection ? 'Incluida en el total' : 'Solo informativa') + '</small></div><span>' + esc(money(wallet.balanceCents)) + '</span><button class="quiet" data-edit-wallet="' + wallet.id + '">Editar</button><button class="quiet" data-archive-wallet="' + wallet.id + '">Archivar</button></div>').join('') || empty('Todavía no tienes carteras.');
    $('savingsChart').innerHTML = '<p class="savings-period-note">Del ' + esc(monthName(start)) + ' al ' + esc(monthName(end)) + '. En el mes actual se usan solo cobros y pagos pendientes; después, las previsiones completas.</p>' + savingsLineChart(rows);
    $('savingsTable').className = 'savings-table'; $('savingsTable').innerHTML = rows.length ? '<table><thead><tr><th>Mes</th><th>Ingresos</th><th>Gastos</th><th>Reserva</th><th>Resultado</th><th>Acumulado</th></tr></thead><tbody>' + rows.map(row => '<tr><td>' + esc(monthName(row.monthKey)) + '</td><td>' + esc(money(row.income)) + '</td><td>' + esc(money(row.expense)) + '</td><td>' + esc(money(row.reserve)) + '</td><td class="' + (row.result < 0 ? 'expense' : 'income') + '">' + esc(money(row.result)) + '</td><td>' + esc(money(row.accumulated)) + '</td></tr>').join('') + '</tbody></table>' : '';
    $('goalsBody').innerHTML = (data.savingsGoals || []).map(goal => { const wallet = (savings.wallets || []).find(item => item.id === goal.walletId); const current = wallet ? wallet.balanceCents : goal.currentCents; const missing = Math.max(0, goal.targetCents - current); const reached = rows.find(row => row.accumulated >= missing); const percent = Math.min(100, Math.round(current / Math.max(1, goal.targetCents) * 100)); return '<div class="goal-card"><div class="goal-head"><div><strong>' + esc(goal.name) + '</strong><small>' + esc(wallet ? 'Cartera: ' + wallet.name : 'Progreso manual') + (goal.targetDate ? ' · objetivo ' + esc(goal.targetDate) : '') + '</small></div><strong>' + percent + '%</strong></div><div class="progress-track"><span style="width:' + percent + '%"></span></div><div class="progress-meta"><span>' + esc(money(current)) + ' de ' + esc(money(goal.targetCents)) + '</span><span>' + (missing ? 'Faltan ' + esc(money(missing)) : 'Objetivo alcanzado') + (reached && missing ? ' · posible en ' + esc(shortMonth(reached.monthKey)) : '') + '</span></div></div>'; }).join('') || empty('Todavía no hay objetivos.');
    $('accountsBody').innerHTML = (data.accounts || []).map(account => '<article class="file-card"><h3>' + esc(account.name) + '</h3><p class="muted small">' + esc(account.type) + '</p><strong>' + esc(money(account.balanceCents)) + '</strong></article>').join('') || empty('Añade tu primera cuenta.');
    $('budgetsBody').innerHTML = (data.budgets || []).map(budget => { const spent = data.transactions.filter(row => row.monthKey === budget.month && row.kind === 'expense' && row.category === budget.category).reduce((total, row) => total + row.amountCents, 0); const percent = Math.round(spent / Math.max(1, budget.amountCents) * 100); const remaining = budget.amountCents - spent; return '<div class="budget-card"><div class="budget-head"><div><strong>' + esc(budget.category) + '</strong><small>' + esc(monthName(budget.month)) + '</small></div><strong>' + percent + '%</strong></div><div class="progress-track ' + (remaining < 0 ? 'over' : '') + '"><span style="width:' + Math.min(100, percent) + '%"></span></div><div class="progress-meta"><span>Gastado ' + esc(money(spent)) + ' de ' + esc(money(budget.amountCents)) + '</span><span class="' + (remaining < 0 ? 'expense' : '') + '">' + (remaining < 0 ? 'Exceso ' + esc(money(-remaining)) : 'Disponible ' + esc(money(remaining))) + '</span></div></div>'; }).join('') || empty('Todavía no hay presupuestos.');
  }
  function savingsLineChart(rows) {
    if (!rows.length) return empty('No hay previsiones hasta ese mes.');
    const width = Math.max(620, rows.length * 92), height = 235, left = 64, right = 24, top = 28, bottom = 42;
    const values = rows.map(row => row.accumulated), min = Math.min(0, ...values), max = Math.max(0, ...values), range = Math.max(1, max - min);
    const x = index => rows.length === 1 ? width / 2 : left + index * (width - left - right) / (rows.length - 1);
    const y = value => top + (max - value) / range * (height - top - bottom);
    const points = rows.map((row, index) => x(index).toFixed(1) + ',' + y(row.accumulated).toFixed(1)).join(' ');
    const ticks = [max, max - range / 2, min].map(value => '<g><line class="grid-line" x1="' + left + '" x2="' + (width - right) + '" y1="' + y(value).toFixed(1) + '" y2="' + y(value).toFixed(1) + '"/><text class="axis-text" x="' + (left - 8) + '" y="' + (y(value) + 4).toFixed(1) + '" text-anchor="end">' + esc(money(Math.round(value))) + '</text></g>').join('');
    const zero = min < 0 && max > 0 ? '<line x1="' + left + '" x2="' + (width - right) + '" y1="' + y(0).toFixed(1) + '" y2="' + y(0).toFixed(1) + '" stroke="var(--danger)" stroke-width="1.5" stroke-dasharray="5 5"/>' : '';
    const dots = rows.map((row, index) => '<g><circle cx="' + x(index).toFixed(1) + '" cy="' + y(row.accumulated).toFixed(1) + '" r="5" fill="var(--surface)" stroke="' + (row.accumulated < 0 ? 'var(--danger)' : 'var(--accent)') + '" stroke-width="3"><title>' + esc(monthName(row.monthKey) + ': ' + money(row.accumulated)) + '</title></circle><text class="axis-text" x="' + x(index).toFixed(1) + '" y="' + (height - 14) + '" text-anchor="middle">' + esc(shortMonth(row.monthKey)) + '</text></g>').join('');
    return '<div class="savings-line-chart-scroll"><div class="savings-line-chart"><svg viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="Evolución del dinero estimado, termina en ' + esc(money(values.at(-1))) + '">' + ticks + zero + '<polyline points="' + points + '" fill="none" stroke="var(--accent)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>' + dots + '</svg></div></div>';
  }
  function formSnapshot(form) { return JSON.stringify([...new FormData(form).entries()]); }
  function openDialog(id) { focusOrigins.set(id, document.activeElement); const dialog = $(id); dialog.showModal(); const form = dialog.querySelector('form'); if (form) snapshots.set(id, formSnapshot(form)); }
  function closeDialog(id, force = false) {
    const dialog = $(id), form = dialog.querySelector('form');
    if (busy && !force) return;
    if (!force && form && formSnapshot(form) !== snapshots.get(id) && !confirm('Hay cambios sin guardar. ¿Quieres descartarlos?')) return;
    dialog.close(); const origin = focusOrigins.get(id); if (origin?.isConnected) origin.focus();
  }
  function fillForm(form, values) { form.reset(); Object.entries(values).forEach(([key, value]) => { const control = form.elements.namedItem(key); if (control) { if (control.type === 'checkbox') control.checked = Boolean(value); else control.value = value ?? ''; } }); }
  function projectionOptions(form, selected) {
    const month = form.elements.date.value.slice(0, 7), kind = form.elements.kind.value;
    const rows = data.projections.rows.filter(row => row.month === month && row.kind === kind);
    let options = rows.map(row => [String(row.templateId), row.name + ' · ' + money(row.amountCents)]);
    // Keep an existing historic link visible even outside the 12-month plan.
    if (selected && !options.some(([id]) => id === String(selected))) options.push([String(selected), 'Vínculo existente #' + selected + ' · revisar mes/tipo']);
    setOptions(form.elements.projectionTemplateId, options, 'Sin vínculo', String(selected || ''));
  }
  function openEditModal(id) {
    const row = data.transactions.find(row => row.id === Number(id)); if (!row) return;
    if (!data.editable) { notify('Copia de consulta: abre el panel local para editar.'); return; }
    const form = $('editForm'); setOptions(form.elements.category, data.categories, null);
    setOptions(form.elements.accountId, (data.accounts || []).map(account => [String(account.id), account.name + ' · ' + money(account.balanceCents)]), 'Sin cuenta', String(row.accountId || ''));
    const users = new Map(); data.transactions.forEach(row => { if (row.userId != null) users.set(String(row.userId), row.user); });
    setOptions(form.elements.userId, [...users], 'Sin persona');
    setOptions(form.elements.receiptId, data.receipts.map(file => [String(file.id), '#' + file.id + ' · ' + file.date + ' · ' + file.user + ' · ' + file.attachmentType]), row.receipt && !row.receiptId ? 'Conservar adjunto existente' : 'Sin adjunto');
    fillForm(form, { id: row.id, date: row.dateIso, amount: (row.amountCents / 100).toFixed(2).replace('.', ','), description: row.description, category: row.category, kind: row.kind, store: row.store, accountId: row.accountId, userId: row.userId, isFixed: String(row.isFixed), receiptId: row.receiptId });
    projectionOptions(form, row.projectionTemplateId);
    $('editContext').innerHTML = row.inferenceNotes.map(note => notice('Aviso pendiente', note, 'warn')).join('') + (row.receipt ? '<button type="button" class="quiet" data-attachment="transactions:' + row.id + '">Ver adjunto actual</button>' : '') + '<button type="button" class="quiet" data-undo="' + row.id + '">Deshacer último cambio</button>';
    $('editError').hidden = true; openDialog('editModal');
  }
  function openNewTransactionModal() {
    if (!data.editable) return;
    const form = $('newTransactionForm');
    setOptions(form.elements.category, data.categories, null);
    setOptions(form.elements.accountId, (data.accounts || []).map(account => [String(account.id), account.name + ' · ' + money(account.balanceCents)]), 'Sin cuenta');
    fillForm(form, { date: data.today, amount: '', description: '', category: data.categories[0], kind: 'expense', store: '', isFixed: 'false' });
    $('newTransactionError').hidden = true; openDialog('newTransactionModal');
  }
  function openWalletModal(wallet = null) {
    if (!data.editable) return;
    const form = $('walletForm');
    fillForm(form, { id: wallet?.id || '', name: wallet?.name || '', balance: wallet ? (wallet.balanceCents / 100).toFixed(2).replace('.', ',') : '0', includeInProjection: wallet ? wallet.includeInProjection : true });
    $('walletTitle').textContent = wallet ? 'Editar cartera' : 'Añadir cartera';
    $('walletError').hidden = true; openDialog('walletModal');
  }
  async function submitWallet(event) {
    event.preventDefault(); if (busy) return;
    const form = event.target, payload = Object.fromEntries(new FormData(form));
    payload.includeInProjection = form.elements.includeInProjection.checked;
    const error = $('walletError'); error.hidden = true; busy = true;
    const submit = form.querySelector('[type=submit]'), label = submit.textContent; submit.disabled = true; submit.textContent = 'Guardando…';
    try { await mutate('/api/savings-wallets', payload); closeDialog('walletModal', true); await refreshData(); notify(payload.id ? 'Cartera actualizada.' : 'Cartera creada.'); }
    catch (exception) { error.textContent = exception.message; error.hidden = false; }
    finally { busy = false; submit.disabled = false; submit.textContent = label; }
  }
  function openTransferModal() {
    if (!data.editable) return;
    const accounts = data.accounts || [];
    if (accounts.length < 2) { notify('Crea al menos dos cuentas para transferir.', true); return; }
    const form = $('transferForm'), options = accounts.map(account => [String(account.id), account.name + ' · ' + money(account.balanceCents)]);
    setOptions(form.elements.fromAccountId, options, null);
    setOptions(form.elements.toAccountId, options, null);
    fillForm(form, { fromAccountId: '', toAccountId: '', amount: '', note: '' });
    $('transferError').hidden = true; openDialog('transferModal');
  }
  async function submitTransfer(event) {
    event.preventDefault(); if (busy) return;
    const form = event.target, payload = Object.fromEntries(new FormData(form));
    const error = $('transferError'); error.hidden = true;
    if (payload.fromAccountId === payload.toAccountId) { error.textContent = 'Elige dos cuentas distintas.'; error.hidden = false; return; }
    busy = true; form.querySelectorAll('button').forEach(button => { button.disabled = true; });
    const submit = form.querySelector('[type=submit]'), label = submit.textContent; submit.textContent = 'Guardando…';
    try { await mutate('/api/transfers', payload); closeDialog('transferModal', true); await refreshData(); notify('Transferencia guardada.'); }
    catch (exception) { error.textContent = exception.message; error.hidden = false; }
    finally { busy = false; form.querySelectorAll('button').forEach(button => { button.disabled = false; }); submit.textContent = label; }
  }
  function updateDurationFields() { const form = $('projectionForm'), installments = form.elements.duration.value === 'installments'; form.querySelectorAll('[data-installment]').forEach(label => { label.hidden = !installments; label.querySelector('input').required = installments; }); }
  function openProjectionModal(id) {
    if (!data.editable) return;
    const row = data.projections.rows.find(row => row.templateId === Number(id) && row.month === state.projectionMonth), form = $('projectionForm');
    setOptions(form.elements.category, data.categories, null);
    fillForm(form, { id: row?.templateId || '', month: state.projectionMonth, name: row?.name || '', kind: row?.kind || 'expense', category: row?.category || data.categories[0], amount: row ? (row.amountCents / 100).toFixed(2).replace('.', ',') : '', status: row?.status || 'pending', duration: row?.installmentTotal ? row.installmentTotal === 1 ? 'once' : 'installments' : 'monthly', startMonth: row?.startMonth || state.projectionMonth, endMonth: row?.endMonth || '', updateDefault: 'false', installmentCurrent: row?.installmentCurrent || '', installmentTotal: row?.installmentTotal || '', note: row?.storedNote || '' });
    $('projectionEditTitle').textContent = row ? 'Editar concepto' : 'Añadir concepto'; $('projectionEditContext').textContent = 'Mes de los importes y del estado: ' + monthName(state.projectionMonth); $('projectionError').hidden = true;
    updateDurationFields(); openDialog('projectionModal');
  }
  async function refreshData() {
    const response = await fetch('/api/data', { cache: 'no-store' }); if (!response.ok) throw new Error('No se pudieron actualizar los datos.');
    const next = await response.json(); if (!Array.isArray(next.transactions) || !next.projections) throw new Error('La actualización no contiene datos válidos.');
    const y = window.scrollY; data = next; setup(); window.scrollTo({ top: y });
  }
  async function mutate(url, payload, method = 'POST') {
    const response = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const result = await response.json().catch(() => ({})); if (!response.ok || !result.ok) throw new Error(result.error || 'No se pudo guardar. Comprueba la conexión con el panel.');
  }
  async function submitForm(event, projection) {
    event.preventDefault(); if (busy) return;
    const form = event.target, payload = Object.fromEntries(new FormData(form));
    payload.isFixed = payload.isFixed === 'true'; payload.updateDefault = payload.updateDefault === 'true';
    payload.reviewed = Boolean(form.elements.reviewed?.checked); payload.removeAttachment = Boolean(form.elements.removeAttachment?.checked);
    if (payload.removeAttachment) payload.receiptId = '';
    const error = $(projection ? 'projectionError' : 'editError'), dialog = projection ? 'projectionModal' : 'editModal';
    const url = projection ? '/api/projections' + (payload.id ? '/' + payload.id + '/' + payload.month : '') : '/api/transactions/' + payload.id;
    busy = true; error.hidden = true; form.querySelectorAll('button').forEach(button => { button.disabled = true; });
    const submit = form.querySelector('[type=submit]'), label = submit.textContent; submit.textContent = 'Guardando…'; let saved = false;
    try { await mutate(url, payload); saved = true; closeDialog(dialog, true); await refreshData(); notify('Cambios guardados.'); }
    catch (exception) { if (saved) notify('Guardado completado. No se pudo refrescar la vista; pulsa Actualizar datos.', true); else { error.textContent = exception.message; error.hidden = false; } }
    finally { busy = false; form.querySelectorAll('button').forEach(button => { button.disabled = false; }); submit.textContent = label; }
  }
  async function submitNewTransaction(event) {
    event.preventDefault(); if (busy) return;
    const form = event.target, payload = Object.fromEntries(new FormData(form));
    payload.isFixed = payload.isFixed === 'true';
    const error = $('newTransactionError'); busy = true; error.hidden = true;
    const submit = form.querySelector('[type=submit]'), label = submit.textContent; submit.disabled = true; submit.textContent = 'Guardando…';
    try { await mutate('/api/transactions', payload); closeDialog('newTransactionModal', true); await refreshData(); notify('Movimiento guardado.'); }
    catch (exception) { error.textContent = exception.message; error.hidden = false; }
    finally { busy = false; submit.disabled = false; submit.textContent = label; }
  }
  async function setProjectionStatus(id, status, button) {
    if (busy) return; busy = true; button.disabled = true; let saved = false;
    try { await mutate('/api/projections/' + id + '/' + state.projectionMonth + '/status', { status }); saved = true; await refreshData(); notify(status === 'skipped' ? 'Concepto omitido este mes. Puedes restaurarlo desde Omitidos.' : 'Estado actualizado.'); }
    catch (error) { notify(saved ? 'Estado guardado. Actualiza los datos para verlo.' : error.message, true); }
    finally { busy = false; button.disabled = false; }
  }
  function openAttachment(key) {
    const [kind, id] = key.split(':'), row = data[kind].find(row => row.id === Number(id)); if (!row) return;
    const url = row.receiptUrl || row.url;
    $('attachmentTitle').textContent = 'Archivo #' + id + ' · ' + row.attachmentType;
    let content;
    if (!row.fileAvailable) content = notice('Archivo no disponible', 'Comprueba que la carpeta sincronizada esté conectada y que el archivo siga en su ubicación. Después actualiza los datos.', 'warn');
    else if (row.attachmentType === 'imagen') content = '<img src="' + esc(url) + '" alt="Archivo adjunto #' + id + '">';
    else if (row.attachmentType === 'audio') content = '<audio controls preload="metadata" src="' + esc(url) + '">Tu navegador no puede reproducir este audio.</audio>';
    else if ((row.receipt || row.path || '').toLowerCase().endsWith('.pdf')) content = '<iframe title="Documento PDF adjunto" src="' + esc(url) + '"></iframe>';
    else content = notice('Vista previa no disponible', 'Este formato no tiene un visor integrado.', 'warn');
    $('attachmentContent').innerHTML = content + (row.fileAvailable ? '<p><a href="' + esc(url) + '" target="_blank" rel="noopener">Abrir archivo en otra pestaña</a></p>' : '');
    $('attachmentContent').querySelectorAll('img,audio').forEach(media => media.addEventListener('error', () => { $('attachmentContent').innerHTML = notice('No se pudo abrir el archivo', 'Puede haberse movido o estar pendiente de sincronización. Actualiza los datos y comprueba su ubicación.', 'warn'); }, { once: true }));
    openDialog('attachmentModal');
  }
  function addReceiptReviewLine(values = {}) {
    const index = $('receiptReviewRows').children.length;
    const row = document.createElement('div'); row.className = 'form-grid receipt-review-line';
    row.innerHTML = '<label class="full">Descripción<input name="description-' + index + '" required></label><label>Importe (€)<input name="amount-' + index + '" inputmode="decimal" required></label><label>Categoría<select name="category-' + index + '" required>' + data.categories.map(category => '<option>' + esc(category) + '</option>').join('') + '</select></label><label>Fecha<input name="date-' + index + '" type="date" required></label><label>Comercio<input name="store-' + index + '" list="storeNames"></label><button type="button" class="quiet" data-remove-line>Quitar línea</button>';
    $('receiptReviewRows').appendChild(row); row.querySelector('[name^="description-"]').value = values.description || ''; row.querySelector('[name^="amount-"]').value = values.amount || (values.amountCents ? (values.amountCents / 100).toFixed(2).replace('.', ',') : ''); row.querySelector('[name^="category-"]').value = values.category || data.categories[0]; row.querySelector('[name^="date-"]').value = values.dateIso || values.date || $('receiptReviewForm').dataset.receiptDate || data.today; row.querySelector('[name^="store-"]').value = values.store || '';
    updateReceiptReviewSum();
  }
  function receiptAmount(value) { const number = Number(String(value || '').trim().replace(/\s/g, '').replace(',', '.')); return Number.isFinite(number) ? Math.round(number * 100) : 0; }
  function updateReceiptReviewSum() {
    const form = $('receiptReviewForm');
    const sumCents = [...$('receiptReviewRows').querySelectorAll('[name^="amount-"]')].reduce((total, input) => total + receiptAmount(input.value), 0);
    const expectedRaw = form.elements.expectedTotal.value; const expected = receiptAmount(expectedRaw);
    const node = $('receiptReviewSum'); node.className = 'receipt-review-sum';
    if (!expectedRaw.trim()) node.textContent = 'Suma de las líneas: ' + money(sumCents) + ' · Escribe el total del justificante para comprobarla.';
    else if (sumCents === expected) { node.classList.add('match'); node.textContent = 'Cuadra: las líneas suman ' + money(sumCents) + '.'; }
    else { node.classList.add('mismatch'); node.textContent = 'Faltan ' + money(Math.abs(expected - sumCents)) + (sumCents > expected ? ' por quitar.' : ' por asignar.') + ' Líneas: ' + money(sumCents) + ' · ticket: ' + money(expected) + '.'; }
  }
  function openReceiptReview(id) {
    if (!data.editable) return;
    const file = data.receipts.find(row => row.id === Number(id)); if (!file) return;
    const linked = data.transactions.filter(row => row.receiptId === Number(id));
    const form = $('receiptReviewForm'); form.reset(); form.elements.receiptId.value = id; form.dataset.receiptDate = file.dateIso || data.today; $('receiptReviewRows').innerHTML = '';
    $('receiptReviewTitle').textContent = 'Revisar ticket #' + id;
    $('receiptReviewMeta').textContent = file.user + ' · recibido ' + file.date + (file.caption ? ' · ' + file.caption : '');
    let preview = notice('Vista previa no disponible', 'Puedes abrir el archivo en otra pestaña para comprobarlo.', 'warn');
    if (!file.fileAvailable) preview = notice('Archivo no disponible', 'Comprueba la carpeta del justificante y actualiza los datos.', 'warn');
    else if (file.attachmentType === 'imagen') preview = '<img src="' + esc(file.url) + '" alt="Ticket #' + id + '">';
    else if (file.attachmentType === 'audio') preview = '<audio controls src="' + esc(file.url) + '"></audio>';
    else if ((file.path || '').toLowerCase().endsWith('.pdf')) preview = '<iframe title="Ticket #' + id + '" src="' + esc(file.url) + '"></iframe>';
    $('receiptReviewPreview').innerHTML = preview;
    (linked.length ? linked : [{}]).forEach(row => addReceiptReviewLine(row));
    if (linked.length) form.elements.expectedTotal.value = (sum(linked) / 100).toFixed(2).replace('.', ',');
    updateReceiptReviewSum(); $('receiptReviewError').hidden = true; openDialog('receiptReviewModal');
  }
  async function submitReceiptReview(event) {
    event.preventDefault(); if (busy) return;
    const form = event.target, entries = [...$('receiptReviewRows').children].map(row => ({ description: row.querySelector('[name^="description-"]').value, amount: row.querySelector('[name^="amount-"]').value, category: row.querySelector('[name^="category-"]').value, date: row.querySelector('[name^="date-"]').value, store: row.querySelector('[name^="store-"]').value, kind: 'expense' }));
    const error = $('receiptReviewError'); busy = true; error.hidden = true;
    try { await mutate('/api/receipts/' + form.elements.receiptId.value + '/review', { entries, expectedTotal: form.elements.expectedTotal.value }); closeDialog('receiptReviewModal', true); await refreshData(); notify('Ticket confirmado y movimientos registrados.'); }
    catch (exception) { error.textContent = exception.message; error.hidden = false; }
    finally { busy = false; }
  }
  async function refreshRuntimeStatus() {
    if (!$('runtimeStatus')) return;
    try { const response = await fetch('/api/status', { cache: 'no-store' }); if (!response.ok) throw new Error(); const status = await response.json(); const backup = status.lastBackupAt ? ' · copia verificada ' + new Date(status.lastBackupAt).toLocaleDateString('es-ES') : ' · sin copia verificada'; $('runtimeText').textContent = (status.bot.running === true ? 'Bot activo' : status.bot.running === false ? 'Bot detenido' : 'Bot: estado no confirmado') + ' · Panel conectado · ' + status.pendingCount + ' archivos pendientes' + backup; }
    catch (_) { $('runtimeText').textContent = 'Sin conexión con el panel. Los datos visibles pueden estar desactualizados.'; }
  }
  Object.entries(filterIds).forEach(([key, id]) => $(id).addEventListener('input', () => { state[key] = $(id).value; state.page = 1; state.filesShown = 24; render(); saveState(); }));
  $('toggleFilters').addEventListener('click', () => { state.more = !state.more; showTab(state.tab, false); });
  $('resetFilters').addEventListener('click', () => { Object.keys(filterIds).forEach(key => { state[key] = ''; }); state.page = 1; state.fileStatus = ''; setup(); saveState(); });
  $('requireReceipts').addEventListener('change', () => { state.requireReceipts = $('requireReceipts').checked; render(); saveState(); });
  [['movementView', 'view'], ['amountSort', 'sort'], ['pageSize', 'pageSize'], ['fileStatus', 'fileStatus'], ['projectionStatusFilter', 'projectionStatus']].forEach(([id, key]) => $(id).addEventListener('change', () => { state[key] = $(id).value; state.page = 1; state.filesShown = 24; render(); saveState(); }));
  [['projectionMonth', 'projectionMonth'], ['analyticsMonth', 'analyticsMonth']].forEach(([id, key]) => $(id).addEventListener('change', () => { state[key] = $(id).value; render(); saveState(); }));
  $('prevPage').addEventListener('click', () => { state.page--; render(); saveState(); }); $('nextPage').addEventListener('click', () => { state.page++; render(); saveState(); });
  $('moreFiles').addEventListener('click', () => { state.filesShown += 24; renderReceipts(); });
  document.querySelectorAll('[data-column]').forEach(input => input.addEventListener('change', () => { state.columns[input.dataset.column] = input.checked; $('transactionsBody').classList.toggle('hide-' + input.dataset.column, !input.checked); saveState(); }));
  $('addProjection').addEventListener('click', () => openProjectionModal(null));
  $('addTransaction').addEventListener('click', openNewTransactionModal);
  $('addAccount').addEventListener('click', async () => { const name = window.prompt('Nombre de la cuenta'); if (!name) return; try { await mutate('/api/accounts', { name, type: 'bank', openingBalance: '0' }); await refreshData(); notify('Cuenta creada.'); } catch (error) { notify(error.message, true); } });
  $('savingsEndMonth').addEventListener('change', () => { const min = data.today.slice(0, 7), max = addMonths(min, 35); state.savingsEndMonth = $('savingsEndMonth').value || addMonths(state.savingsStartMonth || min, 2); if (state.savingsEndMonth < (state.savingsStartMonth || min)) state.savingsEndMonth = state.savingsStartMonth || min; if (state.savingsEndMonth > max) state.savingsEndMonth = max; $('savingsEndMonth').value = state.savingsEndMonth; renderSavings(); saveState(); });
  document.addEventListener('change', event => { if (event.target.id === 'savingsStartMonth') { const min = data.today.slice(0, 7), max = addMonths(min, 35); state.savingsStartMonth = event.target.value || min; if (state.savingsStartMonth < min) state.savingsStartMonth = min; if (state.savingsStartMonth > max) state.savingsStartMonth = max; if (state.savingsEndMonth < state.savingsStartMonth) state.savingsEndMonth = state.savingsStartMonth; $('savingsStartMonth').value = state.savingsStartMonth; $('savingsEndMonth').value = state.savingsEndMonth; renderSavings(); saveState(); } });
  document.querySelectorAll('[data-savings-range]').forEach(button => button.addEventListener('click', () => { state.savingsEndMonth = addMonths(state.savingsStartMonth || data.today.slice(0, 7), Number(button.dataset.savingsRange) - 1); $('savingsEndMonth').value = state.savingsEndMonth; renderSavings(); saveState(); }));
  $('savingsSettingsForm').addEventListener('submit', async event => { event.preventDefault(); const payload = Object.fromEntries(new FormData(event.target)); payload.includeCurrentBalance = event.target.elements.includeCurrentBalance.checked; try { await mutate('/api/savings-settings', payload); await refreshData(); notify('Ajustes de ahorro guardados.'); } catch (error) { notify(error.message, true); } });
  $('addWallet').addEventListener('click', () => openWalletModal());
  document.addEventListener('click', event => { const edit = event.target.closest('[data-edit-wallet]'); if (edit) { const wallet = (data.savings?.wallets || []).find(item => item.id === Number(edit.dataset.editWallet)); if (wallet) openWalletModal(wallet); return; } const archive = event.target.closest('[data-archive-wallet]'); if (archive && confirm('¿Archivar esta cartera? Se conservará su historial.')) mutate('/api/savings-wallets', { id: archive.dataset.archiveWallet, archive: true }).then(refreshData).then(() => notify('Cartera archivada.')).catch(error => notify(error.message, true)); });
  $('savingsSimulator').addEventListener('submit', event => { event.preventDefault(); const payload = Object.fromEntries(new FormData(event.target)); const months = Math.max(1, [...document.querySelectorAll('#savingsTable tbody tr')].length); const amount = Math.round(Number(String(payload.amount).replace(',', '.')) * 100); const delta = payload.kind === 'expense' ? -amount * (payload.frequency === 'monthly' ? months : 1) : amount * (payload.frequency === 'monthly' ? months : 1); const base = Number($('savingsPanel').dataset.savingsFinal || 0); $('simulatorResult').textContent = 'Escenario simulado: ' + money(delta) + '. El resultado final cambiaría de ' + money(base) + ' a aproximadamente ' + money(base + delta) + '. No se ha guardado ningún movimiento.'; });
  const transferButton = document.createElement('button'); transferButton.type = 'button'; transferButton.className = 'secondary'; transferButton.textContent = 'Transferir'; $('addAccount').after(transferButton);
  transferButton.addEventListener('click', openTransferModal);
  $('transferForm').addEventListener('submit', submitTransfer);
  setOptions($('budgetForm').elements.category, data.categories, null);
  $('budgetForm').addEventListener('submit', async event => { event.preventDefault(); const payload = Object.fromEntries(new FormData(event.target)); try { await mutate('/api/budgets', payload); event.target.reset(); await refreshData(); notify('Presupuesto guardado.'); } catch (error) { notify(error.message, true); } });
  $('goalForm').addEventListener('submit', async event => { event.preventDefault(); const payload = Object.fromEntries(new FormData(event.target)); try { await mutate('/api/savings-goals', payload); event.target.reset(); await refreshData(); notify('Objetivo creado.'); } catch (error) { notify(error.message, true); } });
  $('deleteTransaction').addEventListener('click', async () => {
    const id = $('editForm').elements.id.value, row = data.transactions.find(item => String(item.id) === id);
    if (!row || busy) return;
    if (!confirm('¿Eliminar «' + row.description + '» (' + money(row.amountCents) + ')?\nPodrás deshacerlo justo después.')) return;
    busy = true;
    try {
      await mutate('/api/transactions/' + id, {}, 'DELETE'); closeDialog('editModal', true); await refreshData();
      notify('Movimiento eliminado.', false, { label: 'Deshacer', run: async () => { await mutate('/api/transactions/' + id + '/restore', {}); await refreshData(); notify('Movimiento restaurado.'); } });
    } catch (failure) { notify(failure.message, true); }
    finally { busy = false; }
  });
  $('editForm').addEventListener('submit', event => submitForm(event, false)); $('projectionForm').addEventListener('submit', event => submitForm(event, true)); $('newTransactionForm').addEventListener('submit', submitNewTransaction); $('walletForm').addEventListener('submit', submitWallet);
  $('receiptReviewForm').addEventListener('submit', submitReceiptReview); $('addReceiptLine').addEventListener('click', () => addReceiptReviewLine());
  $('receiptReviewForm').addEventListener('input', updateReceiptReviewSum);
  $('projectionForm').elements.duration.addEventListener('change', updateDurationFields);
  for (const name of ['date', 'kind']) $('editForm').elements[name].addEventListener('change', () => projectionOptions($('editForm'), $('editForm').elements.projectionTemplateId.value));
  document.querySelectorAll('dialog').forEach(dialog => {
    dialog.addEventListener('cancel', event => { event.preventDefault(); closeDialog(dialog.id); });
    dialog.addEventListener('close', () => { if (dialog.id === 'attachmentModal') $('attachmentContent').innerHTML = ''; });
  });
  document.addEventListener('click', event => {
    const monthStep = event.target.closest('[data-month-step]'); if (monthStep) { const [key, delta] = monthStep.dataset.monthStep.split(':'); stepMonth(key, Number(delta)); return; }
    const helpButton = event.target.closest('[data-help]'); if (helpButton) { const help = window.financeHelp?.[helpButton.dataset.help]; if (help) { $('helpTitle').textContent = help.title; $('helpWhat').textContent = help.what; $('helpSteps').innerHTML = help.steps.map(step => '<li>' + esc(step) + '</li>').join(''); $('helpExample').textContent = help.example; openDialog('helpModal'); } return; }
    const review = event.target.closest('[data-review]'); if (review) { openReceiptReview(review.dataset.review); return; }
    const removeLine = event.target.closest('[data-remove-line]'); if (removeLine) { removeLine.closest('.receipt-review-line')?.remove(); updateReceiptReviewSum(); return; }
    const button = event.target.closest('button'); if (!button) return;
    if (button.dataset.tab) showTab(button.dataset.tab);
    if (button.dataset.go) { if (button.dataset.go === 'analytics' && state.month) { state.analyticsMonth = state.month; $('analyticsMonth').value = state.month; } showTab(button.dataset.go); }
    if (button.dataset.edit) openEditModal(button.dataset.edit);
    if (button.dataset.projection) openProjectionModal(button.dataset.projection);
    if (button.dataset.reviewProjection) { state.projectionMonth = state.analyticsMonth; $('projectionMonth').value = state.projectionMonth; showTab('projection'); openProjectionModal(button.dataset.reviewProjection); }
    if (button.dataset.close) closeDialog(button.dataset.close);
    if (button.dataset.status) { const [id, status] = button.dataset.status.split(':'); setProjectionStatus(id, status, button); }
    if (button.dataset.omit) setProjectionStatus(button.dataset.omit, 'skipped', button);
    if (button.dataset.endProjection) { const [id, month] = button.dataset.endProjection.split(':'); if (confirm('¿Finalizar este concepto desde ' + monthName(month) + '? Se conservarán los meses anteriores.')) (async () => { try { await mutate('/api/projections/' + id + '/' + month + '/from', {}, 'DELETE'); await refreshData(); notify('Concepto finalizado desde ' + monthName(month) + '.'); } catch (error) { notify(error.message, true); } })(); }
    if (button.dataset.attachment) openAttachment(button.dataset.attachment);
    if (button.dataset.undo) (async () => { try { await mutate('/api/transactions/' + button.dataset.undo + '/undo', {}); await refreshData(); notify('Último cambio deshecho.'); } catch (error) { notify(error.message, true); } })();
  });
  $('quickAddTransaction').addEventListener('click', openNewTransactionModal);
  $('refreshData').addEventListener('click', async () => { $('refreshData').disabled = true; try { await refreshData(); notify('Datos actualizados.'); } catch (error) { notify(error.message, true); } finally { $('refreshData').disabled = false; } });
  $('themeToggle').addEventListener('click', () => {
    const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    applyTheme(theme);
    try { localStorage.setItem('finance-theme', theme); } catch (_) {}
  });
  $('runtimeRefresh')?.addEventListener('click', refreshRuntimeStatus);
  window.addEventListener('beforeunload', event => { for (const dialog of document.querySelectorAll('dialog[open]')) { const form = dialog.querySelector('form'); if (form && formSnapshot(form) !== snapshots.get(dialog.id)) { event.preventDefault(); event.returnValue = ''; return; } } });
  applyTheme(preferredTheme()); setup(); refreshRuntimeStatus(); if (data.editable) setInterval(refreshRuntimeStatus, 30000);
})();
