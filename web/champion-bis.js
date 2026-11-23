import { itemIconStrip } from './item-icons.js';

let builds;
let catalog;
let config;
let selected = null;
export async function initializeChampionBis(options) {
  config = options;
  const response = await fetch(new URL('data/item_builds.json', options.root));
  if (!response.ok) throw new Error(`Unable to load champion item suggestions: ${response.status}`);
  catalog = await response.json(); builds = catalog.champions || {};
}
export function renderChampionBis(host, name, compact = false) {
  host.replaceChildren(); host.dataset.bisChampion = name;
  const { t, cn } = config;
  const build = builds[name];
  const title = document.createElement('strong'); title.textContent = `${cn(name)} · ${t('Own recommended items (BiS suggestions)')}`; host.append(title);
  if (!build) { const empty = document.createElement('p'); empty.textContent = t('No sourced champion item suggestion is bundled.'); host.append(empty); return; }
  host.append(itemIconStrip(build.items || []));
  const label = document.createElement('small');
  label.textContent = t(build.kind === 'published_trio' ? 'Published item trio · not guaranteed optimal' : 'Role-based suggestion · not verified champion BiS'); host.append(label);
  config.link(host, 'Item build source', build.source || catalog.source);
  if (!compact) {
    const note = document.createElement('p'); note.className = 'muted compact'; note.textContent = t(build.selection_note || catalog.selection); host.append(note);
    if (build.items_source) config.link(host, 'Item catalog source', build.items_source);
    const snapshot = document.createElement('small'); snapshot.textContent = `${t('Source snapshot:')} ${catalog.snapshot_date} · ${t('Patch')} ${catalog.patch}`; host.append(snapshot);
  }
  const separate = document.createElement('small'); separate.className = 'muted'; separate.textContent = t('Recommendation only — separate from collected inventory; never auto-equipped.'); host.append(separate);
}
export function previewChampionBis(name) {
  if (!builds || !config.champions.has(name)) return;
  selected = name;
  const host = document.getElementById('champion-bis'); host.hidden = false; renderChampionBis(host, name, true);
}
export function relocalizeChampionBis() { if (selected) previewChampionBis(selected); }
