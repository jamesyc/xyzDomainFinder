const $ = (id) => document.getElementById(id);
const families = [
  ['repeat', 'Repeated blocks', '↻'], ['palindrome', 'Palindromes', '◇'],
  ['sequence', 'Sequences', '↗'], ['pair', 'Paired digits', '∷'],
  ['round', 'Round numbers', '○'], ['chunks', 'Digit chunks', '▥'],
  ['date', 'Dates', '□'], ['explicit', 'Personal numbers', '☆'],
];
const labels = Object.fromEntries(families.map(([key, label]) => [key, label]));
const tagLabels = {repeat: 'Repeat', palindrome: 'Palindrome', sequence: 'Sequence', pair: 'Paired', round: 'Round', chunks: 'Chunks', date: 'Date', explicit: 'Personal'};
const states = {unchecked: 'Unchecked', available: 'Available', unavailable: 'Unavailable', unknown: 'Unknown'};
const pageSize = 20;
let data = [], filtered = [], pattern = '', page = 1, current = null, toastTimer;
const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]));
const number = (value) => Number(value).toLocaleString();
const reasons = (row) => row.reasons ? row.reasons.split(';') : [];
const tags = (row) => reasons(row).map((reason) => `<span class="tag ${escape(reason)}">${escape(tagLabels[reason] || reason)}</span>`).join('');

function toast(message) {
  $('toast').textContent = message;
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 2400);
}

async function copy(domain) {
  try { await navigator.clipboard.writeText(domain); toast(`Copied ${domain}`); }
  catch { toast('Could not copy. Select the domain text to copy it manually.'); }
}

function detail(row) {
  current = row;
  $('detail-name').textContent = row.domain;
  const price = (value) => value == null ? 'Not quoted' : `${value} ${row.currency || '(currency unknown)'}`;
  const checked = row.checked_at ? new Date(row.checked_at).toLocaleString() : 'Not checked';
  const fields = [ ['Catalog rank', `#${number(row.rank)}`], ['Length', `${row.length} digits`],
    ['Availability', states[row.availability] || 'Unknown'], ['Last checked', checked],
    ['Registration', price(row.registration_price)], ['Renewal', price(row.renewal_price)],
    ['Registrar', row.provider || 'Not checked'] ];
  $('detail-content').innerHTML = `<div class="tags">${tags(row)}</div><dl class="detail-list">${fields.map(([label, value]) => `<div><dt>${escape(label)}</dt><dd>${escape(value)}</dd></div>`).join('')}</dl><p class="detail-note">This rank describes a pattern preference. Verify availability and renewal pricing with your registrar before choosing a name.</p>`;
  $('details').showModal();
}

function draw() {
  const query = $('search').value.trim().toLowerCase();
  filtered = data.filter((row) => (!query || row.domain.includes(query))
    && (!pattern || reasons(row).includes(pattern))
    && (!$('length').value || String(row.length) === $('length').value)
    && (!$('state').value || row.availability === $('state').value));
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  page = Math.min(page, pages);
  const start = (page - 1) * pageSize;
  const visible = filtered.slice(start, start + pageSize);
  $('result-count').textContent = `${number(filtered.length)} ${filtered.length === 1 ? 'name' : 'names'}`;
  $('active-pattern').textContent = labels[pattern] || 'All patterns';
  $('rows').innerHTML = visible.map((row, index) => `<tr>
    <td><span class="hash">#</span>${number(row.rank)}</td>
    <td><button class="domain-button" data-detail="${index}" aria-label="View ${escape(row.domain)}">${escape(row.domain.replace(/\.xyz$/, ''))}<span class="tld">.xyz</span></button></td>
    <td><div class="tags">${tags(row)}</div></td>
    <td class="length-cell">${escape(row.length)} digits</td>
    <td><span class="status ${escape(row.availability)}">${escape(states[row.availability] || 'Unknown')}</span></td>
    <td><button class="copy-button" data-copy="${index}" aria-label="Copy ${escape(row.domain)}" title="Copy domain">⧉</button></td>
  </tr>`).join('');
  $('rows').querySelectorAll('[data-detail]').forEach((button) => button.addEventListener('click', () => detail(visible[Number(button.dataset.detail)])));
  $('rows').querySelectorAll('[data-copy]').forEach((button) => button.addEventListener('click', () => copy(visible[Number(button.dataset.copy)].domain)));
  $('empty').hidden = filtered.length !== 0;
  $('range').textContent = filtered.length ? `${number(start + 1)}–${number(Math.min(start + pageSize, filtered.length))} of ${number(filtered.length)} names` : '0 names';
  $('page').textContent = `${page} / ${pages}`;
  $('previous').disabled = page <= 1;
  $('next').disabled = page >= pages;
  $('export').disabled = !filtered.length;
  $('patterns').querySelectorAll('button').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.pattern === pattern)));
}

function reset() {
  pattern = ''; page = 1;
  $('filters').reset();
  draw();
}

async function load() {
  $('refresh').disabled = true;
  $('error').hidden = true;
  try {
    const response = await fetch('/api/catalog', {cache: 'no-store'});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || 'The catalog could not be loaded.');
    data = payload.rows;
    $('total').textContent = number(data.length);
    $('sidebar-total').textContent = number(data.length);
    $('examined').textContent = number(payload.metadata.examined || 0);
    const checked = data.filter((row) => row.availability !== 'unchecked').length;
    $('checked').textContent = number(checked);
    $('checked-caption').textContent = checked ? `${number(data.length - checked)} names still unchecked` : 'No registration checks yet';
    $('cap-warning').hidden = payload.metadata.cap_reached !== 'true';
    const created = new Date(payload.metadata.created_at);
    $('built-at').textContent = Number.isNaN(created.valueOf()) ? '' : `Catalog built ${created.toLocaleDateString(undefined, {month: 'short', day: 'numeric', year: 'numeric'})}`;
    $('patterns').innerHTML = `<button class="pattern-button" data-pattern="" aria-pressed="true"><span class="pattern-symbol">▦</span>All patterns<span class="pattern-count">${number(data.length)}</span></button>`
      + families.map(([key, label, symbol]) => `<button class="pattern-button" data-pattern="${key}" aria-pressed="false"><span class="pattern-symbol" aria-hidden="true">${symbol}</span>${label}<span class="pattern-count">${number(data.filter((row) => reasons(row).includes(key)).length)}</span></button>`).join('');
    $('patterns').querySelectorAll('button').forEach((button) => button.addEventListener('click', () => { pattern = button.dataset.pattern; page = 1; draw(); }));
    draw();
  } catch (error) {
    data = [];
    draw();
    $('patterns').innerHTML = '';
    $('built-at').textContent = '';
    $('cap-warning').hidden = true;
    $('checked-caption').textContent = 'Catalog unavailable';
    ['total', 'sidebar-total', 'examined', 'checked'].forEach((id) => { $(id).textContent = '—'; });
    $('error').textContent = error.message || 'The catalog could not be loaded. Try refreshing.';
    $('error').hidden = false;
  } finally { $('refresh').disabled = false; }
}

$('filters').addEventListener('submit', (event) => event.preventDefault());
['search', 'length', 'state'].forEach((id) => $(id).addEventListener('input', () => { page = 1; draw(); }));
$('previous').addEventListener('click', () => { page--; draw(); });
$('next').addEventListener('click', () => { page++; draw(); });
$('refresh').addEventListener('click', load);
$('reset').addEventListener('click', reset);
$('all-catalog').addEventListener('click', reset);
$('close-details').addEventListener('click', () => $('details').close());
$('copy-detail').addEventListener('click', () => { if (current) copy(current.domain); });
$('export').addEventListener('click', () => {
  const anchor = document.createElement('a');
  anchor.href = '/export.csv?' + new URLSearchParams({search:$('search').value, pattern, length:$('length').value, state:$('state').value});
  anchor.download = 'xyz-shortlist.csv'; anchor.click();
  toast('CSV download requested');
});

load();
