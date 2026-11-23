import { itemIconStrip } from './item-icons.js';

const $ = id => document.getElementById(id);
let catalog = new Map();
let config;
let confirmed = { mode: null, items: [], starting_board: null, available_stars: {} };
let draft = null;
let nextId = 1;
let afterApply = null;
const copy = value => JSON.parse(JSON.stringify(value));
const node = (tag, text, className) => {
  const value = document.createElement(tag);
  if (text != null) value.textContent = text;
  if (className) value.className = className;
  return value;
};
const tr = value => config.t(value);
function report(text) { $('inventory-status').textContent = tr(text); }
function removeButton(label, action) {
  const button = node('button', '×'); button.type = 'button'; button.title = label;
  button.setAttribute('aria-label', label); button.addEventListener('click', action); return button;
}
function rosterOptions(select, empty = false) {
  const old = select.value; select.replaceChildren();
  if (empty) select.append(new Option(tr('Bench / unassigned'), ''));
  const names = empty ? draft.starting_board.champions : [...config.champions.keys()];
  for (const name of names) select.append(new Option(config.cn(name), name));
  if ([...select.options].some(option => option.value === old)) select.value = old;
}
function render() {
  const board = $('inventory-starting-board'); board.replaceChildren();
  const used = draft.starting_board.champions.reduce((sum, name) => sum + (config.champions.get(name)?.slots || 1), 0);
  $('inventory-snapshot-note').textContent = `${tr(draft.snapshotLabel)} · ${used}/${config.maximum()} ${tr('slots')}`;
  for (const name of draft.starting_board.champions) {
    const row = node('div', null, 'inventory-roster-row'); row.dataset.startingChampion = name;
    row.append(config.portrait(name, true, { stars: draft.starting_board.stars }), node('span', config.cn(name)));
    const stars = node('select'); stars.dataset.startingStars = name;
    stars.setAttribute('aria-label', `${config.cn(name)} · ${tr('Confirmed starting stars')}`);
    for (let rank = 1; rank <= 4; rank += 1) stars.append(new Option('★'.repeat(rank), String(rank)));
    stars.value = String(draft.starting_board.stars[name] || 1);
    stars.addEventListener('change', () => { draft.starting_board.stars[name] = Number(stars.value); draft.available_stars[name] = Number(stars.value); });
    const remove = removeButton(`${tr('Remove starting champion')} · ${config.cn(name)}`, () => {
      draft.starting_board.champions = draft.starting_board.champions.filter(value => value !== name);
      delete draft.starting_board.stars[name];
      for (const item of draft.items) if (item.holder === name) item.holder = null;
      render();
    });
    remove.dataset.removeStarting = name; row.append(stars, remove); board.append(row);
  }
  rosterOptions($('inventory-holder'), true); rosterOptions($('inventory-starting-champion'));
  const list = $('inventory-rows'); list.replaceChildren();
  for (const item of draft.items) {
    const row = node('div', null, 'inventory-instance'); row.dataset.itemInstance = item.id;
    const label = node('span', null, 'inventory-instance-label'); label.append(itemIconStrip([item.item]), node('code', item.id)); row.append(label);
    const holder = node('select'); rosterOptions(holder, true); holder.value = item.holder || '';
    holder.setAttribute('aria-label', `${tr(item.item)} · ${item.id} · ${tr('Current holder')}`);
    holder.addEventListener('change', () => { item.holder = holder.value || null; }); row.append(holder);
    const remove = removeButton(`${tr('Remove item copy')} · ${tr(item.item)} · ${item.id}`, () => {
      draft.items = draft.items.filter(value => value.id !== item.id); render();
    });
    remove.dataset.removeItem = item.id; row.append(remove); list.append(row);
  }
  if (!draft.items.length) list.append(node('p', tr('No collected items entered. Apply explicitly confirms zero items.'), 'muted compact'));
  $('inventory-apply').textContent = tr(draft.items.length ? 'Confirm collected items and starting board' : 'Confirm zero items and starting board');
}
function addItem() {
  const item = $('inventory-item').value;
  const count = Number($('inventory-count').value);
  if (!catalog.has(item)) { report('Choose an item from the bundled Set 19 catalog.'); return; }
  if (!Number.isSafeInteger(count) || count < 1) { report('Quantity must be a positive whole number.'); return; }
  const holder = $('inventory-holder').value || null;
  for (let index = 0; index < count; index += 1) draft.items.push({ id: `collected-${nextId++}`, item, holder });
  report('Each copy has its own physical item ID.'); render();
}
function apply(planned) {
  if (config.busy()) return;
  if (!planned) {
    const slots = draft.starting_board.champions.reduce((sum, name) => sum + (config.champions.get(name)?.slots || 1), 0);
    if (slots > config.maximum()) { report('The starting roster exceeds the final slot budget. Remove a champion before confirming.'); return; }
    const holders = new Map();
    for (const item of draft.items) {
      if (!item.holder) continue;
      const entry = catalog.get(item.item);
      if (!entry?.equippable) { report('Consumables and non-equipment must remain on the bench; they are not automatically used.'); return; }
      const held = holders.get(item.holder) || { slots: 0, unique: new Set() };
      held.slots += entry.slot_cost || 1;
      if (held.slots > 3) { report('A starting champion cannot hold more than three item slots. Move extra copies to the bench.'); return; }
      if (entry.unique_per_champion && held.unique.has(item.item)) { report('A unique item cannot be equipped twice on the same champion. Move the extra copy to the bench.'); return; }
      held.unique.add(item.item); holders.set(item.holder, held);
    }
    confirmed = { mode: 'collected', items: copy(draft.items), starting_board: copy(draft.starting_board), available_stars: { ...draft.available_stars, ...draft.starting_board.stars }, snapshotLabel: draft.snapshotLabel };
  } else confirmed = { ...confirmed, mode: 'planned' };
  const callback = afterApply; afterApply = null;
  config.changed(); $('inventory-dialog').close();
  if (callback) callback();
}
export async function initializeInventory(options) {
  config = options;
  const response = await fetch(new URL('data/item_catalog.json', options.root));
  if (!response.ok) throw new Error(`Unable to load bundled item catalog: ${response.status}`);
  const data = await response.json(); catalog = new Map(Object.entries(data.items || {}));
  if (data.set !== 19 || !catalog.size) throw new Error('The bundled Set 19 item catalog is empty.');
  const choices = $('inventory-item-options');
  for (const name of [...catalog.keys()].sort()) choices.append(new Option(name, name));
  $('inventory-add').addEventListener('click', addItem);
  $('inventory-item-form').addEventListener('submit', event => { event.preventDefault(); addItem(); });
  $('inventory-add-starting').addEventListener('click', () => {
    const name = $('inventory-starting-champion').value;
    if (!config.champions.has(name) || draft.starting_board.champions.includes(name)) return;
    draft.starting_board.champions.push(name); draft.starting_board.stars[name] = draft.available_stars[name] || 1; render();
  });
  $('inventory-apply').addEventListener('click', () => apply(false));
  $('inventory-planned').addEventListener('click', () => apply(true));
  $('inventory-dialog').addEventListener('close', () => { afterApply = null; draft = null; });
}
export function openInventory(snapshot, callback = null) {
  if (!config || config.busy()) return;
  draft = { items: copy(confirmed.items), available_stars: { ...confirmed.available_stars },
    starting_board: copy(confirmed.starting_board || snapshot.board), snapshotLabel: confirmed.starting_board ? 'Previously confirmed starting roster — review before applying' : snapshot.label };
  afterApply = callback; report('Only items you explicitly enter are collected. Suggestions and viewed boards do not confirm ownership.');
  render(); $('inventory-dialog').showModal();
}
export const inventoryMode = () => confirmed.mode;
export function inventoryPayload() {
  return confirmed.mode === 'collected' ? { item_inventory: copy(confirmed.items), starting_board: copy(confirmed.starting_board), available_stars: { ...confirmed.available_stars } } : { item_inventory: null };
}
export function inventoryExport() { return copy(confirmed); }
export function relocalizeInventory() { if (draft && $('inventory-dialog').open) render(); }
