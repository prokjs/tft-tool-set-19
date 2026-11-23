import { BrowserEngine, isNative, invokeNative } from './engine.js';
import { Localizer } from './i18n.js';
import { initializeItemIcons, itemIconStrip, roleIcon } from './item-icons.js';
import { initializeTeamDrawer, initializeUnlockDrawer, updateTeamDrawer, closeTeamDrawer } from './team-drawer.js';
import { initializeInventory, openInventory, inventoryMode, inventoryPayload, inventoryExport, relocalizeInventory } from './inventory.js';
import { initializeChampionBis, previewChampionBis, renderChampionBis, relocalizeChampionBis } from './champion-bis.js';

const ROOT = new URL('../', import.meta.url);
const $ = id => document.getElementById(id);
const i18n = new Localizer(ROOT);
const state = {
  initialized: false, busy: false, poolBusy: false, languageBusy: false,
  revision: 0, poolRequest: 0, collab: new Set(), locked: new Set(), excluded: new Set(),
  stars: {}, selected: new Set(), lastSelected: null, poolLocked: false,
  pool: { eligible: [], visible: [], stars: {} }, results: [], originalResults: [], resultIndex: 0,
  showInactive: false, levelIndex: 0, phaseIndex: 0, finalView: false, tutorialIndex: 0,
  completedUnlocks: {}, currentLevel: 0, capView: false, capPhase: 0, capPlans: new WeakMap(),
  detailName: null, detailBoard: null,
  showAllOptions: true, damageSessions: new WeakMap(), pendingPathChange: null,
};
let init;
let champions = new Map();
let definitions = new Map();
let rosterOrder = [];
let tooltipOwner = null;
let rosterColumns = 1;
let dismissedNoticeLanguage = null;
let workspaceInfo = null;
const costs = { 1: '#9ca9b8', 2: '#64bc8a', 3: '#69aaf2', 4: '#c18af0', 5: '#ebc66c', 7: '#fb9a64' };
const carryArchetypeLabels = { tank:'Tank', attack_speed:'Attack Speed', assassin:'Assassin', fighter:'Fighter / bruiser', caster:'Ability-based carry', general:'General carry' };
const holderMatchLabels = { final:'Final carry', same_archetype:'Same carry archetype', related_melee:'Related melee archetype', damage_fallback:'Compatible damage-type fallback', unassigned:'No suitable holder on this board — keep these items unassigned.' };
const engine = new BrowserEngine({ onStatus: text => { if (!state.initialized) status(text); } });
const t = text => i18n.tr(String(text ?? ''));
const cn = name => i18n.championName(name);
const tn = name => i18n.traitName(name);
const names = list => (list || []).map(cn).join(', ') || '—';
const selectedResult = () => state.results[state.resultIndex];
const plannedStars = (name, board) => board?.planned_stars?.[name] ?? board?.stars?.[name] ?? null;
const matchCompletions = (result = selectedResult()) => result?.completed_unlocks ?? result?.planning_context?.completed_unlocks ?? state.completedUnlocks;
const matchLevel = (result = selectedResult()) => result?.current_level ?? result?.planning_context?.current_level ?? state.currentLevel;
const capPlan = () => selectedResult() && state.capPlans.get(selectedResult());
const holdGoal = () => state.capView ? capPlan()?.board || selectedResult() : selectedResult();
const guideSteps = result => [...(result?.buildup || [])].sort((a, b) => a.level - b.level);
const exportBoard = () => {
  const plan = state.capView && capPlan();
  if (plan) return state.capPhase ? plan.phases[state.capPhase - 1].board : plan.from_board;
  const result = selectedResult();
  if (state.finalView) return result;
  const step = guideSteps(result)[state.levelIndex];
  return (state.phaseIndex ? step?.upgrades?.[state.phaseIndex - 1] : step)?.board || result;
};
const starsText = value => '★'.repeat(Math.max(1, Math.min(4, Number(value) || 1)));
const finalHoldNames = result => (result?.champions || []).filter(name => {
  const cost = champions.get(name)?.cost; return cost === 1 || cost === 2;
});
const finalHoldAdvice = 'Buy and hold these low-cost champions when available, even if they are not on the current board. Keep at least one copy for Final. If an item transfer requires selling a holder, reserve another copy or use an item remover. Unlock requirements still apply.';

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}
let descriptionLanguage = null;
let descriptionPattern = null;
let descriptionTokens = new Map();
function prepareDescriptionTokens() {
  if (descriptionLanguage === i18n.language && descriptionPattern) return;
  descriptionLanguage = i18n.language; descriptionTokens = new Map();
  for (const name of champions.keys()) {
    for (const label of [name, cn(name)]) if (label) descriptionTokens.set(label, { kind: 'champion', name });
  }
  for (const name of definitions.keys()) {
    for (const label of [name, tn(name)]) if (label && !descriptionTokens.has(label)) descriptionTokens.set(label, { kind: 'trait', name });
  }
  for (const [kind, words] of [
    ['effect-ad', ['Attack Damage']], ['effect-ap', ['Ability Power', 'magic damage']],
    ['effect-defense', ['Health', 'Shield', 'Durability', 'Armor', 'Magic Resist', 'Healing']],
    ['effect-control', ['Mana', 'Attack Speed', 'Stun', 'Freeze', 'inspirations']],
  ]) for (const word of words) {
    const label = t(word); if (label.length > 1 && !descriptionTokens.has(label)) descriptionTokens.set(label, { kind });
  }
  const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const eastAsian = /[\p{Script=Hangul}\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]/u;
  const entities = [...descriptionTokens.keys()].sort((a, b) => b.length - a.length).map(label => {
    const token = escape(label);
    // CJK text and Korean particles do not use Latin-style word boundaries.
    return eastAsian.test(label) ? token : `(?<![\\p{L}\\p{N}_])${token}(?![\\p{L}\\p{N}_])`;
  }).join('|');
  descriptionPattern = new RegExp(`(?:(?<stars>[1-4])(?:[-\\s]star|★)\\s+)?(?<entity>${entities})|(?<number>\\p{Nd}+(?:[.,]\\p{Nd}+)*(?:%|x)?)`, 'gu');
}
function renderDescription(parent, text, className = '', tag = 'p') {
  prepareDescriptionTokens();
  const source = String(text ?? ''); const node = el(tag, null, `description ${className}`.trim()); node.title = source;
  node.setAttribute('aria-label', source);
  let last = 0;
  for (const match of source.matchAll(descriptionPattern)) {
    node.append(document.createTextNode(source.slice(last, match.index)));
    const token = descriptionTokens.get(match.groups.entity);
    if (match.groups.number) node.append(el('strong', match[0], 'description-number effect-value'));
    else if (token?.kind === 'champion') {
      const board = match.groups.stars ? { planned_stars: { [token.name]: Number(match.groups.stars) } } : null;
      const icon = portrait(token.name, true, board); icon.classList.add('description-champion');
      icon.title = match[0]; icon.setAttribute('aria-label', match[0]);
      icon.querySelector('.portrait-stars')?.classList.add('description-number'); node.append(icon);
    } else if (token?.kind === 'trait') {
      const icon = el('span', null, 'description-trait'); icon.title = tn(token.name); icon.setAttribute('role', 'img'); icon.setAttribute('aria-label', tn(token.name));
      const file = init?.artwork.traits?.[token.name]?.file;
      if (file) { const image = el('img'); image.src = new URL(`assets/${file}`, ROOT).href; image.alt = ''; icon.append(image); }
      else icon.append(symbolIcon('◇', tn(token.name)));
      node.append(icon);
    } else node.append(el('strong', match[0], token?.kind || ''));
    last = match.index + match[0].length;
  }
  node.append(document.createTextNode(source.slice(last))); parent.append(node); return node;
}
function paragraph(parent, text, className = '') {
  return renderDescription(parent, t(text), className);
}
function heading(parent, text, level = 3) { const node = el(`h${level}`, t(text)); parent.append(node); return node; }
function button(text, action, className = '') {
  const node = el('button', t(text), className); node.type = 'button'; node.addEventListener('click', action); return node;
}
function section(parent, title, className = 'plan-section') {
  const node = el('section', null, className); heading(node, title); parent.append(node); return node;
}
function closeWorkspaceInfo(restoreFocus = false) {
  const current = workspaceInfo; workspaceInfo = null;
  const dialog = $('workspace-info-dialog');
  if (dialog?.open) dialog.close();
  if (!current) return;
  current.content.hidden = current.hidden;
  if (current.placeholder.isConnected) current.placeholder.replaceWith(current.content);
  else current.content.remove();
  if (restoreFocus && current.owner?.isConnected) current.owner.focus();
}
function openWorkspaceInfo(title, content, owner) {
  closeWorkspaceInfo(); hideTraitTooltip();
  const placeholder = document.createComment('info content');
  content.before(placeholder);
  workspaceInfo = { content, placeholder, owner, hidden: content.hidden };
  content.hidden = false;
  $('workspace-info-title').textContent = t(title);
  $('workspace-info-content').replaceChildren(content);
  $('workspace-info-dialog').showModal();
}
function detailsSection(parent, title = 'Details & sources') {
  const wrapper = el('div', null, 'info-control');
  const content = el('div', null, 'info-content'); content.hidden = true;
  const trigger = button('ⓘ', () => openWorkspaceInfo(title, content, trigger), 'info-button');
  trigger.title = t(title); trigger.setAttribute('aria-label', t(title));
  wrapper.append(trigger, content); parent.append(wrapper); return content;
}
function championStrip(list, board = null, interactive = false) {
  const strip = el('span', null, 'champion-strip');
  for (const name of list || []) {
    if (!champions.has(name)) continue;
    if (interactive) {
      const target = button('', () => showDetails(name, board), 'portrait-action');
      target.title = detailText(name, board); target.setAttribute('aria-label', target.title);
      target.append(portrait(name, true, board)); strip.append(target);
    } else strip.append(portrait(name, true, board));
  }
  return strip;
}
function symbolIcon(symbol, label, className = '') {
  const icon = el('span', symbol, `guide-symbol ${className}`);
  icon.title = t(label); icon.setAttribute('role', 'img'); icon.setAttribute('aria-label', t(label)); return icon;
}
function buildRoute(build, board = null) {
  const route = el('span', null, 'build-route');
  route.title = `${t('For')} ${cn(build.target)} → ${build.holder ? cn(build.holder) : t('No suitable holder on this board — keep these items unassigned.')}`;
  const archetype = carryArchetypeLabels[build.target_archetype]; const match = holderMatchLabels[build.holder_match];
  if (archetype) route.title += `\n${t('Carry archetype:')} ${t(archetype)}`;
  if (match) route.title += `\n${t(match)}`;
  route.setAttribute('aria-label', route.title);
  route.append(roleIcon(build.kind), championStrip([build.target], board), symbolIcon('→', 'Planned holder:'));
  route.append(build.holder ? championStrip([build.holder], board) : symbolIcon('∅', 'No suitable holder on this board — keep these items unassigned.'));
  return route;
}
function itemBuild(build, board = null) {
  const row = el('span', null, 'item-build');
  row.append(buildRoute(build, board), itemIconStrip(build.items || [])); return row;
}
function replacementIcon(replacement) {
  if (replacement.same_role && ['tank', 'ad', 'ap', 'flex'].includes(replacement.profile)) return roleIcon(replacement.profile);
  const label = replacement.profile === 'slot_exchange' ? `${replacement.same_role ? `${t('same-role')} · ` : ''}${t('weighted slot exchange')}` : t('roster-only · no role match');
  return symbolIcon('⇄', label);
}
function status(text, error = false) {
  $('status').textContent = t(text); $('status').classList.toggle('error', error);
}
function fail(error) { status(error?.message || String(error), true); }
function link(parent, label, url) {
  try {
    const target = new URL(url, ROOT);
    const bundled = target.origin === ROOT.origin && target.pathname.startsWith(ROOT.pathname);
    if (!bundled && !['https:', 'http:'].includes(target.protocol)) return paragraph(parent, `${label}: ${url}`, 'muted');
    const node = el('a', t(label)); node.href = target.href; node.target = '_blank'; node.rel = 'noopener noreferrer';
    if (isNative()) node.addEventListener('click', async event => {
      event.preventDefault();
      try {
        if (!bundled) { await invokeNative('open_external', { url: target.href }); return; }
        const response = await fetch(target.href);
        if (!response.ok) throw new Error(`Unable to open bundled document: ${response.status}`);
        state.detailName = null; state.detailBoard = null;
        $('details-title').textContent = t(label);
        const host = $('details-content'); host.replaceChildren();
        const text = el('textarea'); text.value = await response.text(); text.readOnly = true; text.rows = 24; text.style.width = '100%'; text.dir = 'auto'; host.append(text);
        if (!$('details-dialog').open) $('details-dialog').showModal();
      } catch (error) { fail(error); }
    });
    parent.append(node); return node;
  } catch { return paragraph(parent, `${label}: ${url}`, 'muted'); }
}
function portrait(name, small = false, board = null) {
  const champion = champions.get(name);
  const stars = plannedStars(name, board);
  const wrap = el('span', null, `portrait${small ? ' small' : ''}`);
  wrap.style.setProperty('--cost', costs[champion?.cost] || '#adb8cb');
  wrap.dataset.champion = name;
  wrap.title = detailText(name, board); wrap.setAttribute('role', 'img'); wrap.setAttribute('aria-label', wrap.title);
  const file = init.artwork.champions?.[name]?.[small ? 'small' : 'large'];
  if (file) { const img = el('img'); img.src = new URL(`assets/${file}`, ROOT).href; img.alt = ''; img.loading = 'lazy'; wrap.append(img); }
  const cost = el('span', `${champion.cost}g`, 'portrait-cost'); cost.setAttribute('aria-hidden', 'true'); wrap.append(cost);
  if (champion.unlockable) {
    const lock = el('img', null, 'lock-overlay'); lock.src = new URL('assets/unlock-badge.png', ROOT).href; lock.alt = ''; wrap.append(lock);
  }
  if (stars != null) {
    const badge = el('span', starsText(stars), 'portrait-stars'); badge.setAttribute('aria-hidden', 'true');
    wrap.dataset.plannedStars = String(stars); wrap.append(badge);
  }
  return wrap;
}
function supportText(support) { return t(support?.percent == null ? 'Trait tiers n/a' : `Trait tiers ${support.percent}%`); }
function holderKinds(name, board) { return Object.entries(board?.item_holders || {}).filter(([, holder]) => holder === name).map(([kind]) => kind.toUpperCase()); }
function detailText(name, board) {
  const champion = champions.get(name);
  const support = board?.champion_trait_support?.[name];
  const reason = board?.positioning?.find(marker => marker.champion === name)?.reason;
  const lines = [
    `${cn(name)} · ${champion.cost}g${plannedStars(name, board) == null ? '' : ` · ${t(Object.hasOwn(board || {}, 'item_bench') ? 'Known / conditional stars:' : 'Planned stars:')} ${starsText(plannedStars(name, board))}`}${champion.slots > 1 ? ` · ${champion.slots} ${t('slots')}` : ''}`,
    `${t('Role:')} ${t(champion.role || 'Unclassified')} · ${t('Attack range:')} ${champion.attack_range ?? t('unavailable')} ${t('hexes')}`,
    `${t('Traits:')} ${champion.traits.map(tn).join(', ')}`,
    supportText(support),
  ];
  if (reason) lines.push(t(reason));
  if (champion.unlockable) lines.push(t(champion.unlock_condition), `${t('Player level:')} ${t(champion.unlock_level)}`);
  else lines.push(t('No special unlock requirement.'));
  if (champion.unlock_note) lines.push(t(champion.unlock_note));
  if (name === 'Yone') lines.push(t('AD/AP FLEX: choose one AD or AP build, not both.'));
  if (champion.cost <= 2 && holdGoal()?.champions.includes(name)) lines.push(`${t('Hold for Final')} · ${t('Buy when found · keep a copy')}`);
  return lines.join('\n');
}
function syncControls() {
  const ready = state.initialized && !state.languageBusy;
  const teamReady = ready && state.poolLocked && !state.poolBusy;
  const resultReady = ready && Boolean(selectedResult()) && !state.busy;
  for (const id of ['no-ad-items', 'no-ap-items']) $(id).disabled = !resultReady;
  $('cap-inventory').disabled = !resultReady;
  $('reset-comp').disabled = !resultReady || !state.originalResults[state.resultIndex];
  for (const id of ['level', 'minimum', 'ranking', 'band', 'search', 'reset', 'language', 'tutorial', 'legal']) $(id).disabled = !ready;
  $('apply-language').disabled = !ready;
  $('pool-lock').disabled = !ready || state.poolBusy || (!state.poolLocked && ![5, 6].includes(state.collab.size));
  $('pool-lock').textContent = t(state.poolLocked ? 'Unlock Collab pool' : 'Lock Collab pool');
  $('find').disabled = !teamReady || state.busy || ![5, 6].includes(state.collab.size);
  for (const id of ['team-lock', 'exclude', 'auto-stars']) $(id).disabled = !teamReady || !state.selected.size;
  document.querySelectorAll('[data-stars]').forEach(node => { node.disabled = !teamReady || !state.selected.size; });
  $('unit-details').disabled = !ready || !state.selected.size;
  for (const id of ['copy-code', 'copy-plan', 'save-json']) $(id).disabled = !resultReady;
  for (const id of ['early-scenario', 'scenario-champion', 'scenario-level', 'scenario-stars', 'scenario-apply']) $(id).disabled = !resultReady;
  $('scenario-clear').disabled = !resultReady || !Object.keys(selectedResult()?.early_availability || {}).length;
  document.querySelectorAll('[data-scenario-remove]').forEach(node => { node.disabled = !resultReady; });
  $('unlock-tracker').disabled = !ready || state.busy;
  for (const id of ['match-level', 'unlock-reset']) $(id).disabled = !ready || state.busy;
  document.querySelectorAll('[data-unlock-champion]').forEach(node => { node.disabled = !ready || state.busy || !matchLevel(); });
  document.querySelectorAll('[data-clear-pin], [data-view="cap-out"]').forEach(node => { node.disabled = !resultReady; });
  document.querySelectorAll('[data-temporary-choice]').forEach(node => { node.disabled = !resultReady; });
  $('buildup-guide').setAttribute('aria-busy', String(state.busy));
  document.body.setAttribute('aria-busy', String(state.busy || !state.initialized));
  $('progress').hidden = state.initialized && !state.busy;
}
function idleStatus() {
  status(state.poolLocked ? 'Pool locked. Select team units or find recommendations.' : `Pool unlocked: ${state.collab.size} picked. Choose 5 or 6, then lock the pool.`);
}
function invalidate() {
  state.revision += 1;
  state.damageSessions = new WeakMap();
  state.results = []; state.originalResults = []; state.resultIndex = 0; state.levelIndex = 0; state.phaseIndex = 0; state.finalView = false; state.capView = false;
  hideTraitTooltip();
  renderResults(); renderOverview(); syncControls();
  if (state.busy) status('Inputs changed. The running calculation will be discarded; wait before searching again.');
  else idleStatus();
}
async function refreshPool() {
  const request = ++state.poolRequest;
  state.poolBusy = true; syncControls();
  try {
    const pool = await engine.request('pool', { selected: [...state.collab], locked: state.poolLocked, size: Number($('level').value), stars: { ...state.stars }, sona_band: $('band').value });
    if (request !== state.poolRequest) return;
    const pickerStars = Object.fromEntries([...champions.keys()].map(name => [name, state.stars[name] ?? 1]));
    state.pool = { ...pool, stars: pickerStars, planned_stars: pickerStars }; renderPool(); renderRoster();
  } catch (error) { if (request === state.poolRequest) fail(error); }
  finally { if (request === state.poolRequest) { state.poolBusy = false; syncControls(); } }
}
function renderPool() {
  $('pool-hint').textContent = t(state.poolLocked ? `LOCKED · ${state.collab.size} picks + Sona. Full team-building roster.` : `UNLOCKED · ${state.collab.size}/6 picks + Sona. Click portraits to add/remove.`);
  const strip = $('pool-strip'); strip.replaceChildren();
  for (const name of [...state.collab, 'Sona']) {
    const chip = button(cn(name), () => { if (!state.poolLocked && name !== 'Sona') changePool(name); else showDetails(name, state.pool); }, 'pool-chip');
    chip.style.setProperty('--cost', costs[champions.get(name)?.cost]); chip.prepend(portrait(name, true, state.pool)); chip.title = detailText(name, state.pool); chip.setAttribute('aria-label', chip.title); strip.append(chip);
  }
}
function searchKey(value) { return i18n.searchText(String(value)).normalize('NFKD').toLocaleLowerCase().replace(/[\u0300-\u036f]/g, ''); }
function visibleRoster() {
  const visible = new Set(state.pool.visible);
  return rosterOrder.filter(name => visible.has(name));
}
function matchesRosterSearch(name, query) {
  if (!query) return true;
  const champion = champions.get(name);
  const aliases = [champion.aliases, champion.alias, champion.api_name, champion.id].flat().filter(Boolean);
  return searchKey([name, cn(name), ...champion.traits, ...champion.traits.map(tn), ...aliases].join(' ')).includes(query);
}
function updateRosterSearch() {
  const query = searchKey($('search').value.trim());
  const tiles = $('roster').children;
  let matches = 0;
  for (const tile of tiles) {
    const match = matchesRosterSearch(tile.dataset.champion, query);
    tile.dataset.searchMatch = String(match); tile.classList.toggle('search-dimmed', !match);
    if (match) matches += 1;
  }
  $('roster-count').textContent = query
    ? `${matches} / ${tiles.length} · ${t('Search champions or traits')}`
    : `${tiles.length} / ${champions.size} · ${t(state.poolLocked ? 'Full roster' : 'Nonconflicting candidates and selected picks')}`;
}
function rosterFlags(name) {
  return [state.collab.has(name) || name === 'Sona' ? 'C' : '', state.locked.has(name) ? 'L' : '', state.excluded.has(name) ? 'X' : ''].filter(Boolean).join(' ');
}
function layoutRoster() {
  const roster = $('roster');
  roster.style.setProperty('--roster-cell', '32px');
  rosterColumns = Math.max(1, Math.floor(roster.clientWidth / 32));
  roster.style.setProperty('--roster-columns', rosterColumns);
}
function renderRoster() {
  const roster = $('roster');
  const focusedName = roster.contains(document.activeElement) ? document.activeElement.dataset.champion : null;
  const focusedIndex = focusedName ? [...roster.children].indexOf(document.activeElement) : -1;
  const scrollTop = roster.scrollTop;
  const existing = new Map([...roster.children].map(tile => [tile.dataset.champion, tile]));
  const visible = visibleRoster();
  for (const [index, name] of visible.entries()) {
    const champion = champions.get(name);
    const flags = rosterFlags(name);
    let tile = existing.get(name);
    if (!tile) {
      tile = button('', event => activateChampion(name, event), 'roster-tile');
      tile.dataset.champion = name;
      tile.addEventListener('dblclick', () => { if (state.poolLocked) showDetails(name, state.pool); });
    }
    existing.delete(name);
    const stars = plannedStars(name, state.pool);
    if (stars != null) tile.dataset.plannedStars = String(stars); else delete tile.dataset.plannedStars;
    tile.style.setProperty('--cost', costs[champion.cost]); tile.classList.toggle('selected', state.selected.has(name));
    tile.classList.toggle('pool-member', state.collab.has(name)); tile.classList.toggle('excluded', state.excluded.has(name));
    tile.classList.toggle('team-locked', state.locked.has(name));
    tile.setAttribute('aria-pressed', String(state.poolLocked ? state.selected.has(name) : state.collab.has(name) || name === 'Sona'));
    const details = detailText(name, state.pool);
    tile.setAttribute('aria-label', `${cn(name) === name ? details : `${name} · ${details}`}\n${flags}`);
    tile.title = details;
    const image = portrait(name, false, state.pool); image.removeAttribute('title');
    tile.replaceChildren(image);
    const tags = [flags.replaceAll(' ', ''), champion.slots > 1 ? String(champion.slots) : ''].filter(Boolean).join(' · ');
    const signals = el('span', tags, 'unit-flags'); signals.setAttribute('aria-hidden', 'true'); tile.append(signals);
    if (roster.children[index] !== tile) roster.insertBefore(tile, roster.children[index] || null);
  }
  for (const tile of existing.values()) tile.remove();
  if (focusedName && roster.children.length) {
    const target = [...roster.children].find(tile => tile.dataset.champion === focusedName) || roster.children[Math.min(focusedIndex, roster.children.length - 1)];
    target.focus({ preventScroll: true });
  }
  roster.scrollTop = scrollTop;
  updateRosterSearch();
  $('selection').textContent = `${t('Selected:')} ${names([...state.selected])}`;
  syncControls();
  layoutRoster();
}
function activateChampion(name, event) {
  if (!state.initialized || state.languageBusy || state.poolBusy) return;
  previewChampionBis(name);
  if (!state.poolLocked) { if (name !== 'Sona') changePool(name); return; }
  if (event.shiftKey && state.lastSelected) {
    const visible = visibleRoster(); const first = visible.indexOf(state.lastSelected); const last = visible.indexOf(name);
    if (!event.ctrlKey && !event.metaKey) state.selected.clear();
    if (first >= 0 && last >= 0) visible.slice(Math.min(first, last), Math.max(first, last) + 1).forEach(value => state.selected.add(value));
    else state.selected.add(name);
  } else if (event.ctrlKey || event.metaKey) {
    if (state.selected.has(name)) state.selected.delete(name); else state.selected.add(name);
  } else { state.selected.clear(); state.selected.add(name); }
  state.lastSelected = name; renderRoster();
}
function changePool(name) {
  if (state.poolBusy || state.poolLocked) return;
  if (state.collab.has(name)) state.collab.delete(name);
  else if (state.collab.size < 6 && state.pool.eligible.includes(name)) state.collab.add(name);
  else return;
  $('search').value = ''; updateRosterSearch();
  state.selected.clear(); invalidate(); refreshPool();
}
function toggleTeam(collection) {
  if (!state.poolLocked || state.poolBusy) return;
  const target = state[collection]; const other = state[collection === 'locked' ? 'excluded' : 'locked'];
  for (const name of state.selected) { if (target.has(name)) target.delete(name); else { target.add(name); other.delete(name); } }
  invalidate(); renderRoster();
}
function setStars(value) {
  if (!state.poolLocked) return;
  for (const name of state.selected) { if (value == null) delete state.stars[name]; else state.stars[name] = value; }
  invalidate(); refreshPool();
}
function renderResults() {
  const host = $('result-list'); const scrollTop = host.scrollTop; host.replaceChildren();
  state.results.forEach((result, index) => {
    const entry = el('article', null, 'suggested-team-entry');
    const card = button('', () => selectResult(index), 'suggested-team');
    card.setAttribute('aria-pressed', String(index === state.resultIndex));
    card.setAttribute('aria-label', `${t('Rank')} ${index + 1}: ${names(result.champions)}`);
    card.append(el('strong', `#${index + 1}`, 'suggested-team-rank'));
    const lineup = championStrip(result.lineup_order || result.champions, result);
    lineup.classList.add('suggested-team-lineup'); card.append(lineup);
    const stats = el('span', null, 'suggested-team-stats');
    for (const [label, key] of [['Active traits', 'active'], ['Trait tiers', 'tiers'], ['Frontline', 'frontline'], ['Gold', 'cost']]) {
      stats.append(el('span', `${t(label)} ${result[key]}`));
    }
    card.append(stats); entry.append(card);
    const preview = el('div', null, 'suggested-team-preview'); preview.hidden = true;
    const traits = el('div', null, 'suggested-team-traits');
    for (const trait of sortedTraits(result).filter(value => value.tier)) {
      const chip = button('', () => showTraitTooltip(chip, trait, result), 'suggested-trait');
      const definition = definitions.get(trait.name); const file = init.artwork.traits?.[trait.name]?.file;
      if (file) { const icon = el('img', null, 'trait-icon'); icon.src = new URL(`assets/${file}`, ROOT).href; icon.alt = ''; chip.append(icon); }
      chip.append(el('span', tn(trait.name), 'trait-name suggested-trait-name'));
      chip.append(el('strong', `${trait.count}${definition?.count_mode === 'stars' ? '★' : ''}`, 'suggested-trait-count'));
      chip.append(el('span', `${t('Breakpoint:')} ${trait.tier}${trait.unique ? ` · ${t('unique')}` : ''}`, 'suggested-trait-tier'));
      chip.title = `${tn(trait.name)} · ${t('Count:')} ${trait.count} · ${t('Breakpoint:')} ${trait.tier}`;
      chip.addEventListener('pointerenter', () => showTraitTooltip(chip, trait, result));
      chip.addEventListener('pointerleave', hideTraitTooltip); traits.append(chip);
    }
    preview.append(traits); entry.append(preview); host.append(entry);
  });
  updateTeamDrawer(state.results.length); host.scrollTop = scrollTop;
}
function selectResult(index) {
  state.levelIndex = 0; state.phaseIndex = 0; state.finalView = true; state.capView = false;
  state.resultIndex = index; hideTraitTooltip(); renderResults(); renderOverview(); syncControls();
  closeTeamDrawer();
}
async function findTeams() {
  if (state.busy || state.poolBusy || !state.poolLocked || ![5, 6].includes(state.collab.size)) return;
  const revision = state.revision;
  const payload = recommendationPayload();
  state.busy = true; state.results = []; state.originalResults = []; renderResults(); renderOverview(); syncControls();
  status('Searching end boards and planning level-by-level buildups…');
  try {
    const results = await engine.request('recommend', payload);
    if (revision !== state.revision) { status('Inputs changed. Stale calculation results were discarded.'); return; }
    state.damageSessions = new WeakMap();
    state.originalResults = results.slice();
    state.results = results; state.resultIndex = 0; state.levelIndex = 0; state.phaseIndex = 0; state.finalView = true; state.capView = false; renderResults(); renderOverview();
    status(results.length ? `${results.length} teams found. Select a team to inspect it.` : 'No feasible team found. Adjust team locks, exclusions or settings.');
  } catch (error) { if (revision === state.revision) fail(error); }
  finally { state.busy = false; if (!state.results.length) renderOverview(); syncControls(); }
}
function recommendationPayload() {
  return { collab: [...state.collab].sort(), size: Number($('level').value), locked: [...state.locked].sort(), excluded: [...state.excluded].sort(), min_collab: Number($('minimum').value), mode: $('ranking').value, sona_band: $('band').value, limit: 12, stars: { ...state.stars }, completed_unlocks: { ...state.completedUnlocks }, current_level: state.currentLevel };
}
async function resetComp() {
  const current = selectedResult();
  const index = state.resultIndex;
  const original = state.originalResults[index];
  if (!current || !original || state.busy || state.languageBusy) return;
  const revision = ++state.revision;
  state.busy = true; syncControls(); status('Restoring original recommendation…');
  try {
    const restored = await engine.request('replan', {
      result: original, temporary_choices: {},
      early_availability: original.planning_context?.early_availability || {},
      completed_unlocks: matchCompletions(current), current_level: matchLevel(current),
    });
    if (revision !== state.revision || selectedResult() !== current) return;
    state.damageSessions.delete(current); state.capPlans.delete(current);
    state.results[index] = restored;
    state.levelIndex = 0; state.phaseIndex = 0; state.capPhase = 0;
    state.finalView = true; state.capView = false; state.pendingPathChange = null;
    state.detailName = null; state.detailBoard = null;
    if ($('details-dialog').open) $('details-dialog').close();
    renderResults(); renderOverview();
    $('buildup-scroll').scrollTop = 0;
    status('Original recommendation restored. Collected inventory and unlock confirmations are preserved.');
  } catch (error) { if (revision === state.revision && selectedResult() === current) fail(error); }
  finally { state.busy = false; syncControls(); }
}

function damageView() {
  const level = state.finalView || state.capView ? null : guideSteps(selectedResult())[state.levelIndex]?.level ?? null;
  const phase = level === null ? 0 : state.phaseIndex;
  const active = [...(state.damageSessions.get(selectedResult())?.views.entries() || [])]
    .find(([, view]) => view.level === level && (view.display_phase ?? view.phase) === phase);
  return { key: active?.[0] ?? (level === null ? 'final' : `${level}:${phase}`), level, phase: active?.[1].phase ?? phase };
}
function syncDamageControls() {
  const current = damageView();
  const view = state.damageSessions.get(selectedResult())?.views.get(current.key);
  const visible = view && (current.level === null || (view.display_phase ?? view.phase) === state.phaseIndex);
  $('no-ad-items').checked = visible ? view.no_ad_items : false;
  $('no-ap-items').checked = visible ? view.no_ap_items : false;
  damageItemNotice();
}
async function applyDamageViews(baseline, views) {
  let result = baseline;
  const fixedFinal = [...views].find(([, view]) => view.level === null && view.goal);
  if (fixedFinal) {
    const [key, view] = fixedFinal;
    result = await engine.request('replan', {
      result: { ...view.goal, temporary_slots: baseline.temporary_slots },
      temporary_choices: baseline.temporary_choices,
      early_availability: scenarioAssumptions(baseline),
      completed_unlocks: matchCompletions(baseline), current_level: matchLevel(baseline),
    });
    views.set(key, { ...view, goal: result, display_phase: 0 });
  }
  for (const [key, view] of views) {
    if (key === fixedFinal?.[0]) continue;
    const { level, phase, no_ad_items, no_ap_items } = view;
    result = await engine.request('damage_alternative', { result, level, phase, no_ad_items, no_ap_items });
    views.set(key, { ...view, ...(level === null ? { goal: result } : {}), display_phase: result.damage_alternative?.phase ?? phase });
  }
  return result;
}
async function replanWithDamageViews(result, payload) {
  const session = state.damageSessions.get(result);
  if (!session) return engine.request('replan', { result, ...payload });
  const views = new Map(session.views);
  const fixedFinal = [...views.values()].some(view => view.level === null && view.goal);
  // Unlock tracking keeps the selected Final, including an explicitly adapted branch.
  const baseline = fixedFinal ? { ...session.baseline, ...payload } : await engine.request('replan', { result: session.baseline, ...payload });
  const updated = await applyDamageViews(baseline, views);
  state.damageSessions.set(updated, { baseline, views, baselineDirty: fixedFinal });
  return updated;
}
async function chooseBranchWithDamageViews(result, payload) {
  // The displayed board and selected Final are the source, never a cached old goal.
  const updated = await engine.request('choose_branch', { result, ...payload });
  const session = state.damageSessions.get(result);
  if (!session) return updated;
  const views = new Map([...session.views]
    .filter(([, view]) => view.level === null || view.level < payload.level)
    .map(([key, view]) => [key, view.level === null ? { ...view, goal: updated } : { ...view }]));
  if (views.size) {
    const finalView = [...views.values()].some(view => view.level === null);
    const baseline = finalView ? {
      ...updated,
      planning_context: {
        ...updated.planning_context,
        no_ad_items: session.baseline.planning_context?.no_ad_items || false,
        no_ap_items: session.baseline.planning_context?.no_ap_items || false,
      },
    } : updated;
    // Explicit toggle changes may rebuild this new goal under real manual pins;
    // neither the old Final snapshot nor automatic local choices may replace it.
    state.damageSessions.set(updated, { baseline, views, baselineDirty: true });
  }
  return updated;
}
function damageItemNotice() {
  const ad = $('no-ad-items').checked; const ap = $('no-ap-items').checked;
  const notice = $('damage-item-notice'); notice.hidden = !ad && !ap;
  notice.textContent = t(ad && ap ? 'No offensive item assumptions — unitemized, tank and utility planning only. Collected cap-out items remain unchanged.' : ad ? 'AP-focused alternative · no AD item assumptions. Collected cap-out items remain unchanged.' : 'AD-focused alternative · no AP item assumptions. Collected cap-out items remain unchanged.');
}
async function changeDamageItems() {
  const result = selectedResult();
  if (state.busy || !state.poolLocked || !result) { syncDamageControls(); return; }
  const view = damageView(); const index = state.resultIndex;
  const session = state.damageSessions.get(result) || { baseline: result, views: new Map() };
  const views = new Map(session.views);
  const no_ad_items = $('no-ad-items').checked; const no_ap_items = $('no-ap-items').checked;
  if (no_ad_items || no_ap_items) views.set(view.key, { level: view.level, phase: view.phase, no_ad_items, no_ap_items });
  else views.delete(view.key);
  const revision = ++state.revision;
  const level = guideSteps(result)[state.levelIndex]?.level;
  const capVisible = state.capView; const capPhase = state.capPhase;
  state.busy = true; syncControls(); status('Finding an alternative for the selected damage-item restrictions…');
  let baseline = session.baseline; let baselineDirty = session.baselineDirty || false;
  try {
    let source = baseline;
    if (baselineDirty && ![...views.values()].some(value => value.level === null && value.goal)) {
      const changingFinal = [...views.values()].some(value => value.level === null);
      source = await engine.request('replan', {
        result: changingFinal ? { ...result, temporary_slots: baseline.temporary_slots } : baseline,
        temporary_choices: baseline.temporary_choices,
        early_availability: scenarioAssumptions(baseline),
        completed_unlocks: matchCompletions(baseline), current_level: matchLevel(baseline),
      });
      if (!changingFinal) { baseline = source; baselineDirty = false; }
    }
    const updated = await applyDamageViews(source, views);
    if (revision !== state.revision || selectedResult() !== result) return;
    const plan = capVisible ? await engine.request('cap_out', {
      result: updated, completed_unlocks: matchCompletions(updated), current_level: matchLevel(updated), ...inventoryPayload(),
    }) : null;
    if (revision !== state.revision || selectedResult() !== result) return;
    if (views.size) state.damageSessions.set(updated, { baseline, views, baselineDirty });
    else state.damageSessions.delete(updated);
    state.results[index] = updated;
    if (plan) { state.capPlans.set(updated, plan); state.capPhase = Math.min(capPhase, (plan.phases || []).length); }
    state.levelIndex = Math.max(0, guideSteps(updated).findIndex(step => step.level === level));
    if (view.level !== null && !state.finalView && !state.capView) state.phaseIndex = views.get(view.key)?.display_phase ?? view.phase;
    state.detailName = null; state.detailBoard = null;
    if ($('details-dialog').open) $('details-dialog').close();
    renderResults(); renderOverview(); status('Damage-item alternative updated. Collected inventory and unlock confirmations are preserved.');
  } catch (error) {
    if (revision === state.revision && selectedResult() === result) { syncDamageControls(); fail(error); }
  } finally { state.busy = false; syncDamageControls(); syncControls(); }
}
function traitStyle(trait) {
  if (!trait.tier) return 'inactive';
  const points = definitions.get(trait.name)?.breakpoints || [];
  if (trait.unique || trait.tier === points.at(-1)) return 'gold';
  return points.indexOf(trait.tier) === 0 ? 'bronze' : points.indexOf(trait.tier) === 1 ? 'silver' : 'gold';
}
function sortedTraits(board) {
  const order = { gold: 0, silver: 1, bronze: 2, inactive: 3 };
  return [...(board.traits || [])].sort((a, b) => order[traitStyle(a)] - order[traitStyle(b)] || b.count - a.count || a.name.localeCompare(b.name));
}
function renderTraitList(parent, board, includeInactive = state.showInactive) {
  const list = el('div', null, 'trait-list'); parent.append(list);
  for (const trait of sortedTraits(board).filter(value => includeInactive || value.tier)) {
    const definition = definitions.get(trait.name);
    const row = el('button', null, `trait-row ${traitStyle(trait)}`); row.type = 'button';
    const image = init.artwork.traits?.[trait.name]?.file;
    if (image) { const img = el('img', null, 'trait-icon'); img.src = new URL(`assets/${image}`, ROOT).href; img.alt = ''; row.append(img); }
    row.append(el('span', `${trait.count}${definition?.count_mode === 'stars' ? '★' : ''}`, 'trait-count'));
    const copy = el('span', null, 'trait-copy'); copy.append(el('span', tn(trait.name), 'trait-name'));
    const points = el('span', null, 'breakpoints');
    (definition?.breakpoints || []).forEach((point, index) => { if (index) points.append(el('span', '›')); points.append(el('span', point, point === trait.tier ? 'current' : '')); });
    copy.append(points); row.append(copy);
    row.addEventListener('pointerenter', () => showTraitTooltip(row, trait, board));
    row.addEventListener('pointerleave', hideTraitTooltip);
    row.addEventListener('focus', () => showTraitTooltip(row, trait, board)); row.addEventListener('blur', hideTraitTooltip);
    row.addEventListener('click', () => { if (tooltipOwner === row) hideTraitTooltip(); else showTraitTooltip(row, trait, board); });
    row.addEventListener('wheel', event => {
      if (tooltipOwner !== row) return;
      const tip = $('trait-tooltip');
      if (tip.scrollHeight > tip.clientHeight) { event.preventDefault(); tip.scrollTop += event.deltaY; }
    }, { passive: false });
    list.append(row);
  }
}
function richLine(parent, text, className = '') {
  return renderDescription(parent, text, `effect-line ${className}`, 'div');
}
function traitMembers(traitName) {
  const result = selectedResult(); const collab = new Set(result?.collab_pool || state.collab);
  return rosterOrder.filter(name => traitName === 'Collab' ? collab.has(name) || name === 'Sona' : champions.get(name).traits.includes(traitName) || (name === 'Sona' && traitName === (result?.sona_band || $('band').value)));
}
function showTraitTooltip(owner, trait, board) {
  hideTraitTooltip(); tooltipOwner = owner;
  const definition = definitions.get(trait.name); if (!definition) return;
  const tip = $('trait-tooltip'); tip.replaceChildren();
  heading(tip, `${tn(trait.name)} · ${trait.count} · ${t(trait.tier ? `active breakpoint ${trait.tier}` : 'inactive')}`);
  paragraph(tip, 'Scroll on the trait for long effects · move off it to close', 'muted compact');
  heading(tip, 'WHAT IT DOES', 4);
  const description = i18n.traitDescription(trait.name, definition.description || '').replace(/<br\s*\/?>/gi, '\n').replace(/<[^>]*>/g, '');
  let breakpoints = false;
  for (const line of description.split('\n')) {
    const match = /^(\d+)\s+(.+)$/.exec(line);
    if (match && definition.breakpoints.includes(Number(match[1]))) {
      if (!breakpoints) { heading(tip, 'BREAKPOINTS', 4); breakpoints = true; }
      richLine(tip, `(${match[1]}) ${Number(match[1]) === trait.tier ? `${t('ACTIVE')} · ` : ''}${match[2]}`, Number(match[1]) === trait.tier ? 'active' : '');
    } else richLine(tip, line);
  }
  const members = traitMembers(trait.name);
  heading(tip, `${t('CHAMPIONS')} · ${members.length}`, 4);
  const catalogue = el('div', null, 'trait-catalogue');
  for (const name of members) { const tile = el('div', null, 'catalogue-unit'); tile.append(portrait(name, true, board), el('span', cn(name))); catalogue.append(tile); }
  tip.append(catalogue); paragraph(tip, `${t('Source:')} ${definition.description_source || ''}`, 'source');
  // A noninteractive tooltip is attached to the trait's hover, not the popup's hover.
  // Place it in the top dialog when necessary so it remains above the modal layer.
  const dialog = owner.closest('dialog'); (dialog || document.body).append(tip);
  tip.hidden = false; tip.scrollTop = 0;
  const rect = owner.getBoundingClientRect(); const width = tip.getBoundingClientRect().width;
  let left = rect.right + 8;
  if (left + width > window.innerWidth - 10) left = rect.left - width - 8;
  left = Math.max(10, Math.min(left, window.innerWidth - width - 10));
  let top = Math.max(10, Math.min(rect.top, window.innerHeight - tip.getBoundingClientRect().height - 10));
  if (left < rect.right && left + width > rect.left) top = rect.bottom + 8;
  if (top + tip.getBoundingClientRect().height > window.innerHeight - 10) top = Math.max(10, rect.top - tip.getBoundingClientRect().height - 8);
  tip.style.left = `${left}px`; tip.style.top = `${top}px`;
}
function hideTraitTooltip() { tooltipOwner = null; $('trait-tooltip').hidden = true; }
function isFinalMember(name) {
  return Boolean(selectedResult()?.champions?.includes(name));
}
function slotChoice(step, name) {
  return step?.temporary_options?.find(choice => choice.selected === name && choice.options?.includes(name));
}
function manualChoices(result = selectedResult()) {
  return (state.damageSessions.get(result)?.baseline || result)?.temporary_choices || {};
}
function slotPin(step, slot) {
  return Object.entries(manualChoices())
    .filter(([level, choices]) => Number(level) <= step.level && Object.hasOwn(choices, slot))
    .sort((a, b) => Number(b[0]) - Number(a[0]))[0]?.[1]?.[slot];
}
function renderClearSlotPin(parent, step, choice) {
  if (!slotPin(step, choice.slot)) return;
  const clear = button('×', () => chooseTemporary(step, choice.slot, null), 'clear-pin');
  clear.dataset.clearPin = choice.slot; clear.title = t('Remove this slot pin and rebuild later levels'); parent.append(clear);
  clear.setAttribute('aria-label', `${t('Clear pin')} · ${cn(choice.selected)}`);
}
function branchRouteText(route) {
  if (!route) return '';
  const parts = [];
  const traits = [...new Set([...(route.final_traits || []), ...(route.branch_traits || [])])];
  if (traits.length) parts.push(`${t('Final trait direction')}: ${traits.map(tn).join(', ')}`);
  if (route.unlock_targets?.length) parts.push(`${t('Unlock preparation direction')}: ${names(route.unlock_targets)}`);
  return parts.length ? `${parts.join('\n')}\n${t('Route guidance only — not a guaranteed Final roster, unlock or owned unit.')}` : '';
}
function branchRouteIcons(route) {
  const strip = el('span', null, 'branch-route');
  strip.setAttribute('aria-hidden', 'true');
  const traits = [...new Set([...(route?.final_traits || []), ...(route?.branch_traits || [])])];
  if (!traits.length && !route?.unlock_targets?.length) return strip;
  strip.append(el('span', '→', 'branch-route-arrow'));
  for (const name of traits.slice(0, 2)) {
    const file = init.artwork.traits?.[name]?.file;
    if (file) {
      const icon = el('img', null, 'branch-route-trait');
      icon.src = new URL(`assets/${file}`, ROOT).href; icon.alt = ''; strip.append(icon);
    } else strip.append(el('span', tn(name)));
  }
  for (const name of (route.unlock_targets || []).slice(0, 2)) strip.append(portrait(name, true));
  return strip;
}
function renderSlotOptions(parent, step, choice, compact = false, expanded = false) {
  if (isFinalMember(choice.selected)) {
    if (!compact && !expanded) renderClearSlotPin(parent, step, choice);
    return;
  }
  const options = el('div', null, `slot-options${compact ? ' hex-slot-options' : ''}`);
  if (!state.showAllOptions) return;
  options.dataset.slot = choice.slot;
  options.setAttribute('role', 'group');
  options.setAttribute('aria-label', t('Choose and pin this slot for later levels'));
  const choiceBoard = { planned_stars: choice.planned_stars || {} };
  const all = [choice.selected, ...(choice.options || []).filter(name => name !== choice.selected)];
  const selected = champions.get(choice.selected);
  const original = all.includes(choice.slot) && choice.slot !== choice.selected ? choice.slot : null;
  const paired = (all.includes(choice.recommended) && choice.recommended !== choice.selected ? choice.recommended : null) || original || all.find(name => name !== choice.selected && champions.get(name).cost === selected.cost && champions.get(name).role === selected.role) || all[1];
  const shown = expanded ? all : [choice.selected, paired].filter(Boolean);
  for (const name of shown) {
    const option = button('', () => chooseTemporary(step, choice.slot, name), 'slot-option');
    option.dataset.temporaryChoice = name; option.dataset.slot = choice.slot; option.dataset.champion = name; option.dataset.level = String(step.level);
    option.dataset.phase = String(step.display_phase ?? state.phaseIndex);
    const recommended = name === choice.recommended;
    option.dataset.recommended = String(recommended);
    option.setAttribute('aria-pressed', String(name === choice.selected));
    const route = choice.routes?.[name]; const routeText = branchRouteText(route);
    option.title = [t(name === choice.selected ? 'Current choice' : 'Choose and pin this slot for later levels'), recommended ? t('Recommended') : '', detailText(name, choiceBoard), routeText].filter(Boolean).join('\n');
    option.setAttribute('aria-label', option.title);
    option.disabled = state.busy || state.languageBusy;
    option.append(portrait(name, true, choiceBoard));
    if (recommended) {
      const badge = el('span', '✓', 'branch-recommended-badge');
      badge.setAttribute('aria-hidden', 'true'); option.append(badge);
    }
    if (!compact && routeText) option.append(branchRouteIcons(route));
    options.append(option);
  }
  parent.append(options);
  if (!compact && !expanded && all.length > shown.length) {
    const more = detailsSection(options, 'Temporary choices');
    renderSlotOptions(more, step, choice, false, true);
  }
  if (!compact && !expanded) renderClearSlotPin(parent, step, choice);
}
function renderLineupSlot(name, board, options) {
  const slot = el('div', null, 'unit-slot');
  slot.dataset.selectedChampion = name;
  const choice = slotChoice(options.guideStep, name);
  if (choice) renderSlotOptions(slot, options.guideStep, choice);
  slot.append(renderUnitCard(name, board, options));
  if (!choice && isFinalMember(name) && options.guideStep) {
    for (const pinnedSlot of Object.keys(Object.assign({}, ...Object.entries(manualChoices())
      .filter(([level]) => Number(level) <= options.guideStep.level).map(([, choices]) => choices)))) {
      if (slotPin(options.guideStep, pinnedSlot) === name) renderClearSlotPin(slot, options.guideStep, { slot: pinnedSlot, selected: name });
    }
  }
  return slot;
}
function renderUnitCard(name, board, options = {}) {
  const champion = champions.get(name);
  const card = button('', () => showDetails(name, board), 'unit-card'); card.style.setProperty('--cost', costs[champion.cost]);
  card.title = detailText(name, board); card.setAttribute('aria-label', card.title); card.append(portrait(name, false, board));
  const badges = el('span', null, 'unit-badges'); badges.append(roleIcon(init?.roles?.[name] || 'unknown'));
  if (champion.slots > 1) badges.append(symbolIcon(`${champion.slots}◈`, `Occupies ${champion.slots} team slots; shown once on the board.`));
  const support = board.champion_trait_support?.[name];
  if (support?.percent != null) {
    const metric = el('span', `${support.percent}%`, 'support'); metric.title = supportText(support); badges.append(metric);
  }
  for (const kind of holderKinds(name, board)) {
    if (kind.toLowerCase() !== init?.roles?.[name]) badges.append(roleIcon(kind.toLowerCase()));
  }
  card.append(badges);
  for (const build of board.planned_item_builds || []) {
    if (build.holder === name) card.append(itemBuild(build, board));
  }
  if (options.targets) card.append(symbolIcon(options.targets.has(name) ? '◆' : '↻', options.targets.has(name) ? 'FINAL TARGET' : 'TEMPORARY', 'target-tag'));
  const prep = options.preparation?.filter(task => task.helpers?.includes(name));
  if (prep?.length) {
    const targets = championStrip(prep.map(task => task.name)); targets.title = t('UNLOCK PREPARATION'); card.append(targets);
  }
  return card;
}
function renderHexBoard(parent, board, options = {}) {
  heading(parent, 'Estimated positioning', 4);
  const host = el('div', null, 'board'); host.setAttribute('aria-label', t('Estimated board · enemy-facing frontline → backline'));
  const positions = new Map((board.positioning || []).map(marker => [`${marker.row}:${marker.column}`, marker]));
  for (let row = 0; row < 4; row += 1) {
    const line = el('div', null, 'board-row');
    for (let column = 0; column < 7; column += 1) {
      const cell = el('div', null, 'hex'); const marker = positions.get(`${row}:${column}`);
      if (marker) {
        const name = marker.champion; const champion = champions.get(name);
        const target = button('', () => showDetails(name, board)); target.style.setProperty('--cost', costs[champion.cost]);
        target.title = detailText(name, board); target.setAttribute('aria-label', `${target.title}\n${t('Row')} ${row + 1} · ${t('Column')} ${column + 1}`);
        target.append(portrait(name, false, board));
        const items = el('span', null, 'hex-items');
        for (const build of board.planned_item_builds || []) {
          if (build.holder !== name) continue;
          const set = itemIconStrip(build.items || []); set.classList.add('hex-item-set');
          set.title = `${t('For')} ${cn(build.target)} · ${t(build.item_ids ? 'Assigned finite item copies' : 'Recommended item sets · sources & assumptions')}`;
          set.setAttribute('aria-label', `${set.title}: ${(build.items || []).map(t).join(', ')}`);
          items.append(set);
          target.title += `\n${set.getAttribute('aria-label')}`;
        }
        if (items.childElementCount) target.append(items);
        target.setAttribute('aria-label', `${target.title}\n${t('Row')} ${row + 1} · ${t('Column')} ${column + 1}`);
        const kinds = holderKinds(name, board);
        if (kinds.length) { const icons = el('span', null, 'hex-holder'); kinds.forEach(kind => icons.append(roleIcon(kind.toLowerCase()))); target.append(icons); }
        cell.append(target);
        const choice = slotChoice(options.guideStep, name);
        if (choice && !isFinalMember(name)) {
          cell.dataset.slot = choice.slot;
          cell.classList.add('hex-with-options');
          renderSlotOptions(cell, options.guideStep, choice, true);
        }
      }
      line.append(cell);
    }
    host.append(line);
  }
  const scroll = el('div', null, 'board-scroll'); scroll.append(host); parent.append(scroll);
  if (!positions.size) paragraph(parent, 'Positioning metadata unavailable', 'warning');
  const notes = detailsSection(parent); paragraph(notes, 'Estimated placement from shared role/range rules, not combat simulation. Empty hexes are intentional; weighted units are shown once. Click a marker for details.', 'board-note');
}
function renderItemPlans(parent, builds, board = null) {
  const finite = board && Object.hasOwn(board, 'item_bench');
  if (!builds.length && !finite) return;
  const details = detailsSection(parent, finite ? 'Assigned item copies and bench' : 'Recommended item sets · sources & assumptions');
  paragraph(details, finite ? 'These are the same finite item IDs throughout the transition, not fresh recommended trios. Retained holders keep custody unless an explicit legal release occurs.' : 'Ideal item targets only: these items are not owned, equipped, or guaranteed. Temporary holders keep the final carry’s recommended set until a legal transfer.', 'warning');
  for (const build of builds) {
    const group = el('div', null, 'item-build-detail'); group.append(itemBuild(build));
    if (build.item_ids) paragraph(group, `${t('Physical item IDs:')} ${build.item_ids.join(', ')}`, 'muted compact');
    if (!build.holder) paragraph(group, 'No suitable holder on this board — keep these items unassigned.', 'warning');
    if (build.note) paragraph(group, build.note, 'muted');
    if (build.source) link(group, 'Item build source', build.source);
    details.append(group);
  }
  if (finite) renderFiniteItems(details, board);
}
function holderSummary(parent, board) {
  if (Object.hasOwn(board, 'item_bench')) {
    const summary = el('div', null, 'holder-summary cap-inventory-summary');
    const mode = board.item_mode || capPlan()?.item_mode;
    summary.append(el('strong', t(mode === 'collected' ? 'Collected items · finite copies' : 'Planned items · finite what-if')));
    const bench = board.item_bench || [];
    summary.append(el('span', `${t('Bench / unassigned')}: ${bench.length}`), itemIconStrip(bench.map(item => item.item)));
    parent.append(summary);
    renderItemPlans(parent, board.planned_item_builds || [], board); return;
  }
  const row = el('div', null, 'holder-summary');
  for (const build of board.planned_item_builds || []) {
    if (!build.holder) row.append(itemBuild(build, board));
  }
  if (row.childElementCount) parent.append(row);
  const notes = detailsSection(parent); notes.classList.add('holder-notes');
  paragraph(notes, 'Choose holders for the items you actually have; these are alternatives, not three guaranteed item sets. Flex uses one chosen build, never both. No item inventory or ownership is simulated.', 'muted compact');
  renderItemPlans(notes, board.planned_item_builds || []);
}
function renderFiniteItems(parent, board) {
  const ledger = el('div', null, 'cap-item-ledger');
  for (const build of board.planned_item_builds || []) {
    const row = el('div', null, 'cap-item-ledger-row');
    row.append(el('strong', `${t('Assigned to:')} ${build.holder ? cn(build.holder) : t('Bench / unassigned')}`), itemIconStrip(build.items || []));
    row.append(el('code', (build.item_ids || []).join(', '))); ledger.append(row);
  }
  const label = el('strong', t('Bench / unassigned')); ledger.append(label);
  for (const item of board.item_bench || []) {
    const row = el('div', null, 'cap-item-ledger-row'); row.dataset.benchItemInstance = item.id;
    row.append(itemIconStrip([item.item]), el('code', item.id)); ledger.append(row);
  }
  if (!board.item_bench?.length) ledger.append(el('span', '—'));
  parent.append(ledger);
}
function renderTeamBoard(parent, board, options = {}) {
  if (!board) { paragraph(parent, options.fallback ? 'No playable no-unlock alternative was found at this level.' : 'No eligible exact-slot primary board at this level.', 'warning'); return; }
  const grid = el('div', null, 'overview-grid');
  const traits = el('div', null, 'traits-panel'); const header = el('div', null, 'traits-heading');
  const unique = (board.traits || []).filter(trait => trait.tier && trait.unique).length;
  heading(header, `${t('TRAITS')} · ${board.active} ${t('active')} + ${unique} ${t('unique')}`, 4);
  if (options.inactiveControl) {
    const label = el('label'); const input = el('input'); input.type = 'checkbox'; input.checked = state.showInactive;
    input.addEventListener('change', () => { state.showInactive = input.checked; hideTraitTooltip(); renderOverview(); });
    label.append(input, document.createTextNode(`${t('Show inactive')} (${board.traits.filter(trait => !trait.tier).length})`)); header.append(label);
  }
  traits.append(header); renderTraitList(traits, board, options.inactiveControl ? state.showInactive : false); grid.append(traits);
  if (!options.compact) {
    const holds = el('aside', null, 'final-holds');
    renderFinalHolds(holds, holdGoal()); traits.append(holds);
  }
  const team = el('div', null, 'team-panel');
  const teamTitle = heading(team, `${t('TEAM')} · ${board.champions.length} ${t('champions')} · ${t('frontline → backline')}`, 4);
  const slots = board.champions.reduce((sum, name) => sum + (champions.get(name).slots || 1), 0);
  renderDescription(teamTitle, ` · ${slots}${options.level ? `/${options.level}` : ''} ${t('slots')} · ${board.cost}g`, 'compact', 'span');
  holderSummary(team, board);
  const lineup = el('div', null, 'lineup');
  const selected = new Set(board.champions);
  const order = [...new Set([...(board.lineup_order || []), ...board.champions])].filter(name => selected.has(name));
  for (const name of order) lineup.append(renderLineupSlot(name, board, options));
  team.append(lineup);
  const lower = el('div', null, 'guide-board-row');
  const positioning = el('div', null, 'guide-positioning'); renderHexBoard(positioning, board, options); lower.append(positioning);
  if (options.guideStep) {
    const choices = el('aside', null, 'guide-sidecars unlock-side-panel'); choices.id = 'unlock-side-panel';
    if (options.guideStep.preparation_boards?.length) {
      renderPreparationBoards(choices, options.guideStep, options.guideResult, board);
    } else paragraph(choices, 'No pending preparation at this level.', 'muted compact');
    if (choices.childElementCount) lower.append(choices);
  }
  team.append(lower);
  if (board.frontline_anchor) {
    const anchor = detailsSection(team, 'Planned frontline anchor:'); anchor.append(championStrip([board.frontline_anchor], board, true));
  }
  grid.append(team); parent.append(grid);
  if (board.strategy_notes?.length) {
    const notes = detailsSection(parent, 'Planned team assumptions');
    const list = el('ul'); for (const note of board.strategy_notes) renderDescription(list, t(note), '', 'li'); notes.append(list);
  }
}
function renderOverview() {
  const host = $('overview'); host.replaceChildren();
  const result = selectedResult();
  renderBuildup();
  if (!result) {
    paragraph(host, state.busy ? 'Calculating in the browser worker. You can change inputs; outdated results will not be displayed.' : 'Use the main portrait grid to pick your Collab pool.\n\nWhile unlocked, click to add/remove champions. Conflicting candidates disappear; selected picks remain removable. Sona is automatic.\n\nLock a 5- or 6-champion pool to restore the full roster and enable team locks, exclusions, star controls, and recommendations.', 'overview-placeholder');
    return;
  }
  heading(host, result.branch_selection ? 'Adapted Final' : 'Final team overview');
  paragraph(host, 'Planned end goal — not current inventory. Stars are upgrade targets; unlock confirmations do not confirm purchases or cashout.', 'warning compact');
  if (result.branch_selection) paragraph(host, 'Final adapted to the selected branch. Required core units remain; unlock tasks still require manual completion.', 'warning compact');
  renderTeamBoard(host, result, { inactiveControl: true, level: Number($('level').value) });
  const conditional = result.champions.filter(name => champions.get(name).unlockable);
  if (conditional.length) {
    const tasks = detailsSection(host, `${t('Required unlocks')} · ${conditional.length}`);
    for (const name of conditional) { const task = section(tasks, cn(name), 'detail-section'); paragraph(task, champions.get(name).unlock_condition, 'warning'); task.append(button('Champion details', () => showDetails(name, result))); }
  }
}
function showDetails(name, board = state.pool) {
  previewChampionBis(name);
  if (!champions.has(name)) return;
  state.detailName = name; state.detailBoard = board;
  hideTraitTooltip(); renderDetails();
  if (!$('details-dialog').open) $('details-dialog').showModal();
}
function renderDetails() {
  const name = state.detailName; if (!name) return;
  const board = state.detailBoard; const champion = champions.get(name); const host = $('details-content'); host.replaceChildren();
  $('details-title').textContent = cn(name);
  const hero = el('div', null, 'detail-hero'); hero.append(portrait(name, false, board));
  const text = el('div'); paragraph(text, cn(name));
  paragraph(text, champion.traits.map(tn).join(' · '), 'muted'); hero.append(text); host.append(hero);
  paragraph(host, `${t('Attack range:')} ${champion.attack_range ?? t('unavailable')} ${t('hexes')}`);
  const profile = el('p', null, 'detail-role'); profile.append(roleIcon(init?.roles?.[name] || 'unknown')); host.append(profile);
  const ownItems = section(host, 'Own recommended items (BiS suggestions)', 'champion-bis');
  renderChampionBis(ownItems, name);
  if (champion.slots > 1) paragraph(host, `Occupies ${champion.slots} team slots; shown once on the board.`, 'warning');
  const holders = holderKinds(name, board); if (holders.length) { const options = el('p'); holders.forEach(kind => options.append(roleIcon(kind.toLowerCase()))); host.append(options); }
  if (name === 'Yone') { const flex = el('p'); flex.append(roleIcon('ad'), document.createTextNode(' / '), roleIcon('ap'), document.createTextNode(` · ${t('Choose one build, never both.')}`)); host.append(flex); }
  const marker = board?.positioning?.find(value => value.champion === name);
  if (marker) paragraph(host, `${t('Estimated position:')} ${t(marker.reason)}`, 'muted');
  const unlock = section(host, 'Unlock requirements', 'detail-section');
  paragraph(unlock, champion.unlockable ? champion.unlock_condition : 'No special unlock requirement.', champion.unlockable ? 'warning' : '');
  if (champion.unlockable) {
    paragraph(unlock, `${t('Published player-level requirement:')} ${t(champion.unlock_level)}`, 'warning');
    paragraph(unlock, 'These are conditions, not confirmed rewards. Published minimums and planning availability are distinct; “Any” does not guarantee shop access.', 'muted');
  }
  if (champion.unlock_note) paragraph(unlock, champion.unlock_note, 'warning');
  const support = board?.champion_trait_support?.[name];
  if (support) {
    const metric = section(host, supportText(support), 'detail-section'); paragraph(metric, support.note, 'muted');
    for (const trait of support.details || []) {
      const item = el('div', null, 'detail-trait'); heading(item, tn(trait.name), 4);
      paragraph(item, `${t('Count:')} ${trait.count} · ${t('Breakpoint:')} ${trait.reached || t('inactive')} · ${trait.unique ? t('unique — excluded from percentage') : `${trait.tier_index}/${trait.max_tiers} ${t('tiers')}`}`);
      paragraph(item, i18n.traitDescription(trait.name, trait.effect || ''), 'muted'); metric.append(item);
    }
  }
  renderItemPlans(host, (board?.planned_item_builds || []).filter(build => build.holder === name || build.target === name), board);
  const sources = section(host, 'Sources', 'detail-section');
  for (const [label, key] of [['Role source', 'role_source'], ['Range source', 'attack_range_source'], ['Unlock source', 'unlock_source']]) {
    if (champion[key]) { const p = el('p'); link(p, label, champion[key]); sources.append(p); }
  }
}
function renderUnlocks(parent, unlocks, preparation = false, step = {}) {
  if (!unlocks?.length) return;
  const group = section(parent, preparation ? 'Unlock preparation — intentional helpers for future targets' : 'Selected board’s required unlocks — verify every task before fielding');
  paragraph(group, preparation ? 'These helpers prepare the listed end-goal quests, not unrelated chain recommendations. Their displayed stars are planning assumptions, not proof of the required upgrade, item or progress. Keep the setup until the full task is earned; only then make the conditional handoff.' : 'These are conditions, not confirmed rewards. Published minimums and planning availability are distinct; “Any” does not guarantee shop access.', 'warning');
  for (const unlock of unlocks) {
    const task = section(group, cn(unlock.name), 'detail-section'); task.append(button('Champion details', () => showDetails(unlock.name, step.board)));
    if (preparation) {
      paragraph(task, `${t('Preparation helpers:')} ${(unlock.helpers || []).map(name => `${cn(name)} (${starsText(step.stars?.[name] || 1)} ${t('planned')})`).join(', ') || '—'}`, 'muted');
      if (unlock.count_mode === 'distinct_champions') paragraph(task, `${t('Distinct champions required:')} ${unlock.count} · ${(unlock.traits || []).map(tn).join(' / ')}. ${t('Aggregate trait stars do not satisfy this task.')}`, 'warning');
    }
    paragraph(task, unlock.condition, 'warning'); paragraph(task, `${t('Published player-level requirement:')} ${t(unlock.level)}`);
    if (unlock.note) paragraph(task, unlock.note, 'warning');
    link(task, 'Requirement source', unlock.source);
  }
}
function renderUnlockTracker() {
  const level = matchLevel(); const completed = matchCompletions(); const maximum = Number($('level').value);
  $('match-level').replaceChildren(new Option(t('Static pre-game plan'), '0'));
  for (let value = 1; value <= maximum; value += 1) $('match-level').append(new Option(String(value), String(value)));
  $('match-level').value = String(level);
  const host = $('unlock-checklist'); host.replaceChildren();
  const targets = new Set(selectedResult()?.champions || []);
  const unlockable = [...champions.values()].filter(champion => champion.unlockable && champion.unlock_condition)
    .sort((a, b) => Number(targets.has(b.name)) - Number(targets.has(a.name)) || a.cost - b.cost || a.name.localeCompare(b.name));
  for (const champion of unlockable) {
    const row = el('div', null, 'unlock-check-row'); const label = el('label', null, 'unlock-check-label');
    const check = el('input'); check.type = 'checkbox'; check.dataset.unlockChampion = champion.name;
    check.checked = Object.hasOwn(completed, champion.name);
    check.disabled = state.busy || state.languageBusy || !level;
    check.setAttribute('aria-label', `${t('Task completed')} · ${cn(champion.name)}`);
    check.addEventListener('change', () => {
      const next = { ...matchCompletions() };
      if (check.checked) next[champion.name] = matchLevel(); else delete next[champion.name];
      applyMatchTracking(next, matchLevel());
    });
    label.append(check, portrait(champion.name, true), el('strong', cn(champion.name)));
    row.append(label);
    const stamp = el('span', Object.hasOwn(completed, champion.name) ? `${t('Completed at match level')} ${completed[champion.name]}` : t('Not confirmed'), 'unlock-timestamp');
    row.append(stamp);
    const info = detailsSection(row, 'Unlock requirements');
    paragraph(info, champion.unlock_condition, 'warning');
    paragraph(info, `${t('Published player-level requirement:')} ${t(champion.unlock_level)}`, 'muted compact');
    if (champion.unlock_note) paragraph(info, champion.unlock_note, 'muted compact');
    if (champion.unlock_source) link(info, 'Requirement source', champion.unlock_source);
    row.title = `${t(champion.unlock_condition)}\n${t(champion.unlock_level)}`; host.append(row);
  }
  syncControls();
}
async function applyMatchTracking(completed, level) {
  if (state.busy || state.languageBusy || !state.initialized) return;
  const revision = state.revision; const results = state.results;
  state.busy = true; syncControls(); $('unlock-status').textContent = t('Updating completed unlocks and rebuilding the leveling path…');
  try {
    for (const [name, recorded] of Object.entries(completed)) {
      const minimum = /^Level (\d+)$/.exec(champions.get(name)?.unlock_level || '');
      if (minimum && recorded < Number(minimum[1])) throw new Error(`${cn(name)} · ${t('Published player-level requirement:')} ${t(champions.get(name).unlock_level)}`);
      if (recorded > Number($('level').value)) throw new Error(t('A completion timestamp is above the final level. Raise the final level or reset completed unlocks.'));
    }
    const updated = await Promise.all(results.map(result => replanWithDamageViews(result, {
      completed_unlocks: completed, current_level: level,
    })));
    if (revision !== state.revision || state.results !== results) return;
    state.completedUnlocks = { ...completed }; state.currentLevel = level; state.results = updated;
    state.capView = false; state.phaseIndex = 0; state.capPhase = 0;
    renderResults(); renderOverview();
    $('unlock-status').textContent = t('Completed tasks updated. Unlocked is not owned; planned stars and cashout remain unconfirmed.');
    status('Completed tasks updated. Unlocked is not owned; planned stars and cashout remain unconfirmed.');
  } catch (error) {
    if (revision === state.revision && state.results === results) {
      $('unlock-status').textContent = t(error?.message || String(error)); fail(error);
    }
  } finally {
    state.busy = false;
    if ($('unlock-drawer').classList.contains('is-open')) renderUnlockTracker();
    syncControls();
  }
}
function startingSnapshot() {
  const result = selectedResult();
  const step = [...(result?.buildup || [])].sort((a, b) => a.level - b.level)[state.levelIndex];
  const visibleL10 = !state.finalView && !state.capView && step?.level === 10;
  const phase = visibleL10 && state.phaseIndex ? step.upgrades?.[state.phaseIndex - 1] : step;
  const source = visibleL10 && phase?.board ? phase.board : result;
  const names = [...(source?.champions || [])];
  return { board: { champions: names, stars: Object.fromEntries(names.map(name => [name, plannedStars(name, source) || 1])) },
    label: visibleL10 ? 'Snapshot of the visible level 10 phase — review and confirm actual roster/ranks' : 'Editable assumption from planned Final — not confirmed owned roster/ranks' };
}
function inventoryChanged() {
  state.revision += 1; state.capPlans = new WeakMap(); state.capView = false; state.capPhase = 0;
  renderOverview(); syncControls();
  status('Item mode and starting snapshot updated. All cap-out calculations were cleared; normal Final and unlock tracking are unchanged.');
}
async function openCapOut() {
  const result = selectedResult();
  if (!result || state.busy || state.languageBusy) return;
  if (!inventoryMode()) { openInventory(startingSnapshot(), openCapOut); return; }
  state.capView = true; state.finalView = false;
  if (state.capPlans.has(result)) { renderBuildup(); syncControls(); return; }
  const revision = state.revision;
  state.busy = true; renderBuildup(); syncControls(); status('Calculating the post-cashout cap-out and exact replacements…');
  try {
    const plan = await engine.request('cap_out', { result, completed_unlocks: matchCompletions(result), current_level: matchLevel(result), ...inventoryPayload() });
    if (revision !== state.revision || selectedResult() !== result) return;
    state.capPlans.set(result, plan); state.capPhase = (plan.phases || []).length;
    renderBuildup(); status('Cap-out is a heuristic target, not a purchase or cashout confirmation.');
  } catch (error) {
    if (revision === state.revision && selectedResult() === result) {
      state.capView = false; state.finalView = true; renderBuildup(); fail(error);
    }
  } finally { state.busy = false; syncControls(); }
}
function renderCapOut(host, actions, result) {
  const plan = state.capPlans.get(result);
  if (!plan) { paragraph(host, 'Calculating the post-cashout cap-out and exact replacements…', 'muted'); return; }
  const phases = plan.phases || [];
  state.capPhase = Math.max(0, Math.min(state.capPhase, phases.length));
  const phase = state.capPhase ? phases[state.capPhase - 1] : { board: plan.from_board, added: [], removed: [], retained: plan.from_board.champions };
  paragraph(actions, 'Cap-out is a heuristic target, not a purchase or cashout confirmation.', 'warning compact');
  const step = { level: result.planning_context?.size || Number($('level').value), upgrades: phases };
  renderGuideActions(actions, step, phase, state.capPhase);
  renderPivotWarning(actions, step, phase);
  if (plan.source_unlock_assumptions?.length) {
    const assumptions = el('div', null, 'cap-source-assumptions compact');
    assumptions.dataset.sourceUnlockAssumptions = 'true';
    assumptions.append(el('span', t('Conditional source unlocks')), championStrip(plan.source_unlock_assumptions.map(value => value.name), plan.from_board, true));
    assumptions.title = t('These source Final copies are assumed only for this planned comparison; no unlock, ownership, purchase or cashout is confirmed.');
    actions.append(assumptions);
  }
  const navigation = $('phase-navigation'); const selector = el('select'); selector.id = 'cap-phase-select';
  const label = el('label', t('Cap-out transition')); label.htmlFor = selector.id;
  selector.append(new Option(t(plan.item_mode === 'collected' ? 'Confirmed starting board' : 'Original planned Final · what-if'), '0'));
  phases.forEach((value, index) => selector.append(new Option(`${t('Buy before sell')} ${index + 1}/${phases.length}`, String(index + 1))));
  selector.value = String(state.capPhase);
  const change = value => { state.capPhase = value; renderBuildup(); syncControls(); };
  selector.addEventListener('change', () => change(Number(selector.value)));
  navigation.append(label, selector);
  const primary = section(host, state.capPhase === phases.length ? 'Cap-out target' : 'Cap-out transition', 'plan-section plan-board primary-board');
  renderTeamBoard(primary, phase.board, { level: step.level, targets: new Set(plan.board.champions) });
  for (const transfer of phase.item_transfers || []) {
    const row = el('div', null, 'cap-item-transfer');
    row.append(championStrip([transfer.outgoing], null, true), symbolIcon('→', 'Planned holder:'), championStrip([transfer.incoming], phase.board, true));
    paragraph(row, transfer.method === 'sell_outgoing' ? 'Sell the outgoing holder to release items.' : 'Old holder stays fielded: requires an item remover or a later sale; no free transfer.', 'warning compact');
    if (transfer.condition) paragraph(row, transfer.condition, 'compact warning'); actions.append(row);
  }
  const notes = detailsSection(host, 'Cap-out reasoning and transition');
  paragraph(notes, plan.item_mode === 'collected' ? 'Confirmed collected-item budget and starting board. Future purchases, upgrades and cashout remain conditional.' : 'Original planned item budget · what-if only, not collected inventory. Future purchases, upgrades and cashout remain conditional.', 'warning compact');
  renderFiniteItems(notes, phase.board);
  for (const reason of plan.replacement_reasons || []) paragraph(notes, reason, 'compact');
  if (plan.preserved_core?.length) { heading(notes, 'Preserved core', 4); notes.append(championStrip(plan.preserved_core, phase.board, true)); }
  for (const note of plan.notes || []) paragraph(notes, note, 'muted compact');
  if (!phases.length) paragraph(actions, plan.notes?.[0] || 'The original planned Final is already the selected cap-out.', 'muted compact');
  phases.forEach((value, index) => {
    const row = section(notes, `${t('Buy before sell')} ${index + 1}`);
    row.append(symbolIcon('+', 'Buy first'), championStrip(value.added, value.board, true), symbolIcon('−', 'Then replace / remove'), championStrip(value.removed, null, true));
    if (value.reason) paragraph(row, value.reason, 'compact');
  });
}
function selectGuideLevel(index) {
  state.levelIndex = index; state.phaseIndex = 0; state.finalView = false; state.capView = false; renderBuildup();
  $('buildup-scroll').scrollTop = 0;
}
function scenarioAssumptions(result = selectedResult()) {
  return result?.early_availability || result?.planning_context?.early_availability || {};
}
function prefillEarlyScenario() {
  const assumption = scenarioAssumptions()[$('scenario-champion').value];
  $('scenario-level').value = String(assumption?.level || Math.min(3, Number($('level').value)));
  $('scenario-stars').value = String(assumption?.stars || 1);
}
function renderEarlyScenario() {
  const result = selectedResult(); if (!result) return;
  const previousName = $('scenario-champion').value;
  const excluded = new Set(result.planning_context?.excluded || []);
  $('scenario-champion').replaceChildren();
  for (const name of rosterOrder) {
    const option = new Option(`${cn(name)} · ${champions.get(name).cost}g`, name);
    option.disabled = excluded.has(name); $('scenario-champion').append(option);
  }
  const preferred = previousName && !excluded.has(previousName) ? previousName
    : result.champions.find(name => champions.get(name).cost === 4 && !champions.get(name).unlockable) || result.champions[0];
  $('scenario-champion').value = preferred;
  $('scenario-level').replaceChildren();
  for (let level = 1; level <= Number($('level').value); level += 1) $('scenario-level').append(new Option(String(level), String(level)));
  prefillEarlyScenario();
  const saved = $('scenario-saved'); saved.replaceChildren();
  for (const [name, assumption] of Object.entries(scenarioAssumptions(result))) {
    const row = el('div', null, 'scenario-assumption');
    row.append(championStrip([name], { planned_stars: { [name]: assumption.stars } }),
      el('span', `${cn(name)} · ${t('Available from level')} ${assumption.level}`));
    const remove = button('×', () => {
      const assumptions = { ...scenarioAssumptions() }; delete assumptions[name]; applyEarlyScenario(assumptions);
    });
    remove.dataset.scenarioRemove = name; remove.title = `${t('Remove scenario')} · ${cn(name)}`;
    remove.setAttribute('aria-label', remove.title); row.append(remove); saved.append(row);
  }
  syncControls();
}
async function applyEarlyScenario(assumptions, inspectLevel = null) {
  const result = selectedResult();
  if (!result || state.busy || state.languageBusy || !state.initialized) return;
  const revision = state.revision; const index = state.resultIndex;
  const previousLevel = result.buildup?.[state.levelIndex]?.level;
  const finalView = state.finalView;
  state.busy = true; syncControls();
  $('scenario-status').textContent = t('Rebuilding the pre-game scenario…');
  try {
    const updated = await replanWithDamageViews(result, { temporary_choices: {}, early_availability: assumptions });
    if (revision !== state.revision || selectedResult() !== result) return;
    state.results[index] = updated;
    const steps = [...(updated.buildup || [])].sort((a, b) => a.level - b.level);
    state.levelIndex = Math.max(0, steps.findIndex(step => step.level === (inspectLevel || previousLevel)));
    state.phaseIndex = inspectLevel ? (steps[state.levelIndex]?.upgrades || []).length : 0;
    state.finalView = inspectLevel ? false : finalView;
    state.capView = false;
    $('scenario-dialog').close(); state.detailName = null; state.detailBoard = null;
    renderResults(); renderOverview();
    status('Pre-game scenario updated. Unlock requirements still apply.');
  } catch (error) {
    $('scenario-status').textContent = t(error?.message || String(error)); fail(error);
  } finally { state.busy = false; syncControls(); }
}
async function chooseTemporary(step, slot, name, clearFuture = false) {
  const result = selectedResult();
  if (!result || state.busy || state.languageBusy || !state.initialized) return;
  const choice = step.temporary_options?.find(choice => choice.slot === slot);
  const current = choice?.selected || slotPin(step, slot) || (step.board?.champions?.includes(slot) ? slot : null);
  if (name !== null && isFinalMember(current)) {
    if (name === current) previewChampionBis(name);
    return;
  }
  if (name && step.temporary_options?.some(choice => choice.slot === slot && choice.selected === name)) { previewChampionBis(name); return; }
  const later = Object.entries(manualChoices(result)).filter(([level, values]) => Number(level) > step.level && Object.keys(values).length);
  if (later.length && !clearFuture) {
    const list = $('path-change-choices'); list.replaceChildren();
    for (const [level, values] of later.sort((a, b) => Number(a[0]) - Number(b[0]))) {
      for (const [laterSlot, champion] of Object.entries(values)) list.append(el('li', `L${level} · ${cn(laterSlot)} → ${cn(champion)}`));
    }
    state.pendingPathChange = { step: { ...step, display_phase: step.display_phase ?? state.phaseIndex }, slot, name, result, revision: state.revision };
    $('path-change-dialog').showModal(); return;
  }
  if (name) previewChampionBis(name);
  const revision = state.revision;
  const index = state.resultIndex;
  const choices = Object.fromEntries(Object.entries(manualChoices(result)).map(([level, values]) => [level, { ...values }]));
  if (clearFuture) for (const level of Object.keys(choices)) if (Number(level) > step.level) delete choices[level];
  if (name === null) {
    for (const [level, values] of Object.entries(choices)) {
      if (Number(level) <= step.level) delete values[slot];
      if (!Object.keys(values).length) delete choices[level];
    }
  } else choices[String(step.level)] = { ...choices[String(step.level)], [slot]: name };
  state.busy = true; syncControls(); status('Adapting Final and rebuilding the leveling guide…');
  try {
    const updated = await chooseBranchWithDamageViews(result, {
      level: step.level, phase: step.display_phase ?? state.phaseIndex,
      slot, champion: name, temporary_choices: choices,
    });
    if (revision !== state.revision || selectedResult() !== result) {
      status('Inputs changed. Stale calculation results were discarded.'); return;
    }
    state.results[index] = updated;
    const steps = guideSteps(updated);
    state.levelIndex = Math.max(0, steps.findIndex(value => value.level === updated.branch_selection.level));
    state.phaseIndex = updated.branch_selection.phase;
    state.finalView = false; state.capView = false;
    if (state.detailBoard && $('details-dialog').open) $('details-dialog').close();
    state.detailName = null; state.detailBoard = null;
    renderResults(); renderOverview();
    $('buildup-scroll').scrollTop = 0;
    status('Final adapted to the selected branch. Required core units remain; unlock tasks still require manual completion.');
  } catch (error) { if (revision === state.revision && selectedResult() === result) fail(error); }
  finally { state.busy = false; if (!selectedResult()) renderOverview(); syncControls(); }
}
function renderTemporaryOptions(parent, step) {
  const choices = (step.temporary_options || []).filter(choice => !isFinalMember(choice.selected) || slotPin(step, choice.slot));
  if (!choices.length) return;
  const group = section(parent, 'Temporary choices', 'plan-section temporary-choices');
  for (const choice of choices) {
    const row = el('div', null, 'temporary-slot'); row.append(championStrip([choice.slot]));
    renderSlotOptions(row, step, choice); group.append(row);
  }
}
function taskSummary(task) {
  if (task.kind === 'position' && task.board?.positioning_constraint === 'one_front_two_rows') return t('Exactly 1 in front two rows; everyone else in back two rows.');
  const text = t(task.requirements || task.condition || '').replace(/\s+/g, ' ').trim();
  return text.length > 125 ? `${text.slice(0, 122).trimEnd()}…` : text;
}
function requirementStrip(list, requiredBoard, displayedBoard) {
  const strip = championStrip(list, requiredBoard, true);
  for (const target of strip.children) {
    const name = target.querySelector('[data-champion]')?.dataset.champion;
    const present = displayedBoard?.champions?.includes(name) || false;
    const required = plannedStars(name, requiredBoard) || 1;
    const shown = present ? plannedStars(name, displayedBoard) || 1 : 0;
    target.dataset.requirementChampion = name;
    target.classList.toggle('requirement-missing', !present);
    target.classList.toggle('requirement-rank-unmet', present && shown < required);
    const statusText = !present ? t('Absent from displayed board') : shown < required ? t('Displayed planned rank is below the requirement') : t('Present on displayed board; task completion is still manual');
    target.title += `\n${statusText}\n${t('Required planned stars:')} ${starsText(required)}`;
    target.setAttribute('aria-label', target.title);
    target.append(symbolIcon(!present ? '∅' : shown < required ? '↑' : '·', statusText, 'requirement-state'));
  }
  return strip;
}
function renderPreparationBoards(parent, step, result, displayedBoard = step.board) {
  if (!step.preparation_boards?.length) return;
  const group = section(parent, 'Prep before acquisition', 'plan-section preparation-setups');
  for (const preparation of [...step.preparation_boards].sort((a, b) => Number(!a.board) - Number(!b.board) || a.target.localeCompare(b.target))) {
    const setup = el('div', null, 'preparation-setup'); setup.dataset.preparationTarget = preparation.target;
    if (preparation.status === 'completed') {
      setup.append(championStrip([preparation.target], null, true));
      paragraph(setup, `${t('Completed at match level')} ${preparation.completed_level}`, 'compact muted');
      group.append(setup); continue;
    }
    const champion = champions.get(preparation.target);
    const minimum = /^Level (\d+)$/.exec(champion?.unlock_level || '');
    const waitingForLevel = preparation.kind === 'position' && !preparation.board && minimum && step.level < Number(minimum[1]);
    const row = el('div', null, 'preparation-lineup');
    row.append(championStrip([preparation.target], null, true), symbolIcon('←', 'Preparation helpers:'));
    if (preparation.board) row.append(requirementStrip(preparation.helpers?.length ? preparation.helpers : preparation.board.lineup_order || preparation.board.champions, preparation.board, displayedBoard));
    else row.append(symbolIcon('∅', 'No legal preparation board is available at this level. Do not field the target until its full unlock task is complete.'));
    setup.append(row);
    const task = paragraph(setup, waitingForLevel ? champion.unlock_level : taskSummary(preparation), preparation.kind === 'position' ? 'compact warning positioning-requirement' : 'compact prep-task');
    task.title = t(preparation.requirements || preparation.condition || '');
    for (const marker of task.querySelectorAll('.description-champion')) {
      const name = marker.dataset.champion; const present = displayedBoard?.champions?.includes(name) || false;
      marker.dataset.requirementChampion = name;
      marker.classList.toggle('requirement-missing', !present);
      marker.classList.toggle('requirement-rank-unmet', present && (plannedStars(name, displayedBoard) || 1) < (plannedStars(name, preparation.board) || Number(marker.dataset.plannedStars) || 1));
    }
    if (!preparation.board && !waitingForLevel) paragraph(setup, preparation.status === 'unavailable' ? 'No legal setup at this level · verify manually' : 'Manual task · no lineup proves completion', 'compact warning');
    if (preparation.accumulating && !waitingForLevel) row.append(symbolIcon('↻', 'Hold across combats · verify progress manually'));
    if (preparation.conditional_dependencies?.length) {
      const dependencies = el('div', null, 'prep-dependencies'); dependencies.append(symbolIcon('⚿', 'First unlock setup dependency:'), requirementStrip(preparation.conditional_dependencies.map(value => value.name), preparation.board, displayedBoard)); setup.append(dependencies);
    }
    const details = detailsSection(setup);
    paragraph(details, preparation.condition, 'warning');
    if (preparation.requirements && preparation.requirements !== preparation.condition) paragraph(details, preparation.requirements, 'warning');
    if (preparation.note) paragraph(details, preparation.note, 'warning');
    if (preparation.positioning_requirement) paragraph(details, preparation.positioning_requirement, 'warning');
    paragraph(details, 'Grey portraits are absent from the displayed phase board. An upward marker means the displayed planned rank is too low. Presence and planned rank never prove task completion or ownership.', 'muted compact');
    for (const dependency of preparation.conditional_dependencies || []) paragraph(details, `${t('First unlock setup dependency:')} ${cn(dependency.name)} — ${t(dependency.condition)}`, 'warning');
    if (champion?.unlockable) paragraph(details, `${t('Published player-level requirement:')} ${t(champion.unlock_level)}`, 'muted');
    if (preparation.board) renderTeamBoard(details, preparation.board, { compact: true, level: step.level, targets: new Set(result.champions), preparation: step.unlock_prep });
    else paragraph(details, 'No legal preparation board is available at this level. Do not field the target until its full unlock task is complete.', 'warning');
    if (preparation.source || champion?.unlock_source) link(details, 'Requirement source', preparation.source || champion.unlock_source);
    for (const source of preparation.sources || []) link(details, typeof source === 'string' ? 'Requirement source' : (source.name || 'Requirement source'), typeof source === 'string' ? source : source.url);
    group.append(setup);
  }
}
function phaseLabel(step, phase, index) {
  if (!index) return t('Arrival');
  const number = `${index}/${step.upgrades.length}`;
  if (phase.unlock_checks?.length) return `${t('Prep')} ${number} · ${names(phase.unlock_checks.map(task => task.name))}`;
  if (phase.star_upgrades?.length) return `${t('Planned stars:')} ${number}`;
  return t(`Upgrade ${number}`);
}
function renderFinalHolds(parent, result) {
  const held = finalHoldNames(result);
  parent.replaceChildren(); parent.hidden = !held.length;
  if (!held.length) return;
  parent.title = t(finalHoldAdvice);
  parent.append(el('strong', t('Hold for Final')), championStrip(held, result, true),
    el('span', t('Buy when found · keep a copy'), 'final-hold-note'));
  const information = detailsSection(parent, 'Hold for Final'); paragraph(information, finalHoldAdvice, 'warning');
}
function renderGuideActions(parent, step, phase, phaseIndex = state.phaseIndex) {
  const title = el('div', null, 'guide-action-title');
  title.append(el('strong', `${t('Level')} ${step.level} · ${phaseLabel(step, phase, phaseIndex)}`));
  const assumptions = detailsSection(title, 'Source policy & planning assumptions');
  paragraph(assumptions, 'Targets, not owned · unlocks manual', 'warning'); parent.append(title);
  if (phase.final_pivot) {
    paragraph(parent, 'Final-goal transition replaces temporary pins explicitly. Secure the incoming final champions before selling the pinned temporary units.', 'warning compact');
    if (phase.reason) paragraph(parent, phase.reason, 'compact warning');
  }
  const rows = el('div', null, 'guide-action-rows'); parent.append(rows);
  const action = (label, list, board = phase.board) => {
    const row = el('div', null, 'guide-action');
    row.append(el('strong', t(label)), list?.length ? championStrip(list, board, true) : el('span', '—', 'muted'));
    rows.append(row); return row;
  };
  const buy = action('Buy first', phase.added);
  if ((phase.added || []).some(name => champions.get(name)?.unlockable)) buy.append(symbolIcon('⚿', 'Verify unlock requirements before buying or fielding.'));
  if (phase.star_upgrades?.length) {
    const upgrades = el('div', null, 'guide-action star-upgrades'); upgrades.append(el('strong', t('Planned stars:')));
    for (const change of phase.star_upgrades) {
      const name = change.champion; const pair = el('span', null, 'replacement-pair');
      pair.append(championStrip([name], { planned_stars: { [name]: change.to } }, true),
        symbolIcon(`${starsText(change.from)}→${starsText(change.to)}`, 'Planned stars:'));
      upgrades.append(pair);
    }
    rows.append(upgrades);
  }
  if (phase.unlock_checks?.length) {
    const checks = el('div', null, 'guide-action unlock-checks'); checks.append(el('strong', t('Prep')));
    for (const task of phase.unlock_checks) {
      checks.append(symbolIcon('⚿', task.condition || 'Verify unlock requirements before buying or fielding.'), championStrip([task.name], phase.board, true));
    }
    const content = detailsSection(checks, 'Verify unlock requirements before buying or fielding.');
    for (const task of phase.unlock_checks) {
      paragraph(content, `${cn(task.name)} · ${t(task.condition || task.requirements || '')}`, 'warning');
      if (task.source) link(content, 'Requirement source', task.source);
    }
    rows.append(checks);
  }
  const replace = el('div', null, 'guide-action'); replace.append(el('strong', t('Replace')));
  for (const replacement of phase.replacements || []) {
    const pair = el('span', null, 'replacement-pair');
    pair.append(championStrip(replacement.outgoing, phase.board, true), symbolIcon('→', 'Buy / add first'), championStrip(replacement.incoming, phase.board, true));
    pair.append(replacementIcon(replacement));
    replace.append(pair);
  }
  for (const change of phase.copy_replacements || []) {
    const pair = el('span', null, 'replacement-pair');
    pair.append(championStrip([change.champion], { planned_stars: { [change.champion]: change.from } }, true),
      symbolIcon('→', 'Buy / add first'),
      championStrip([change.champion], { planned_stars: { [change.champion]: change.to } }, true));
    replace.append(pair);
  }
  const pairedRemovals = new Set((phase.replacements || []).flatMap(value => value.outgoing));
  const unpaired = (phase.removed || []).filter(name => !pairedRemovals.has(name));
  if (unpaired.length) replace.append(symbolIcon('−', 'Then replace / remove'), championStrip(unpaired, null, true));
  if (phase.replacements?.length || phase.copy_replacements?.length || unpaired.length) rows.append(replace);
  const prep = el('div', null, 'guide-action'); prep.append(el('strong', t('Prep')));
  for (const setup of step.preparation_boards || []) {
    const target = el('span', null, 'prep-target'); target.title = t(setup.requirements || setup.condition || '');
    target.append(championStrip([setup.target], null, true)); prep.append(target);
  }
  if (!step.preparation_boards?.length) prep.append(el('span', '—', 'muted'));
  rows.append(prep);
}
function renderPivotWarning(parent, step, phase) {
  const warnings = [phase.pivot_warning, step.pivot_warning, ...(phase.pivot_warnings || []), ...(step.pivot_warnings || [])].filter(Boolean);
  if (!warnings.length) return;
  const host = el('div', null, 'pivot-warning'); host.setAttribute('role', 'note');
  paragraph(host, 'Large board overhaul — prepare purchases/bench before this round; fast micro required', 'warning compact');
  const details = detailsSection(host, 'Exact replacements — secure incoming units before selling');
  details.append(symbolIcon('+', 'Buy first'), championStrip(phase.added, phase.board, true), symbolIcon('−', 'Then replace / remove'), championStrip(phase.removed, null, true));
  const seen = new Set();
  for (const warning of warnings) {
    const reason = typeof warning === 'string' ? warning : warning.reason;
    if (reason && !seen.has(reason)) { paragraph(details, reason, 'compact'); seen.add(reason); }
    for (const line of warning.sequence || []) if (!seen.has(line)) { paragraph(details, line, 'compact'); seen.add(line); }
  }
  paragraph(details, 'Plan purchases before selling; no gold, bench space or owned copies are assumed.', 'muted compact');
  parent.append(host);
}
function renderBuildup() {
  closeWorkspaceInfo();
  const result = selectedResult();
  const steps = [...(result?.buildup || [])].sort((a, b) => a.level - b.level);
  const nav = $('buildup-navigation'); const summary = $('buildup-summary'); const host = $('buildup-content'); const actions = $('guide-actions');
  nav.replaceChildren(); summary.replaceChildren(); summary.hidden = true; host.replaceChildren(); actions.replaceChildren(); $('phase-navigation').replaceChildren();
  const completedCount = Object.keys(matchCompletions(result)).length;
  $('unlock-tracker').textContent = `${t('Completed unlocks')} · ${completedCount}${matchLevel(result) ? ` · L${matchLevel(result)}` : ''}`;
  $('unlock-tracker').setAttribute('aria-label', `${t('Completed unlocks')} ${completedCount}${matchLevel(result) ? ` · ${t('Level')} ${matchLevel(result)}` : ''}`);
  $('unlock-tracker').title = t('Manual match tracking changes board eligibility and the leveling path. Unlocked does not mean owned: purchases, copies, stars, items and cashout are never confirmed here.');
  const scenarioCount = Object.keys(scenarioAssumptions(result)).length;
  $('early-scenario').textContent = scenarioCount ? `◇ ${scenarioCount}` : '◇';
  $('early-scenario').title = t('Pre-game early availability');
  $('early-scenario').setAttribute('aria-label', `${t('Pre-game early availability')}${scenarioCount ? ` · ${scenarioCount}` : ''}`);
  host.hidden = state.finalView && !state.capView; actions.hidden = state.finalView && !state.capView;
  $('overview').hidden = !result || !state.finalView || state.capView;
  $('buildup-scroll').hidden = !steps.length; $('buildup-placeholder').hidden = Boolean(steps.length);
  if (!steps.length) { syncDamageControls(); return; }
  hideTraitTooltip();
  state.levelIndex = Math.max(0, Math.min(state.levelIndex, steps.length - 1));
  const step = steps[state.levelIndex];
  state.phaseIndex = Math.max(0, Math.min(state.phaseIndex, (step.upgrades || []).length));
  syncDamageControls();
  const phase = state.phaseIndex ? step.upgrades[state.phaseIndex - 1] : step;
  steps.forEach((value, index) => {
    const card = button('', () => selectGuideLevel(index), 'level-summary');
    card.setAttribute('aria-pressed', String(!state.finalView && !state.capView && index === state.levelIndex)); card.dataset.level = String(value.level);
    card.setAttribute('aria-label', `${t('Level')} ${value.level}`);
    card.append(el('strong', `L${value.level}`, 'level-label'));
    const changes = el('span', null, 'level-changes');
    for (const [symbol, label, list] of [
      ['+', 'Buy / add first', value.added], ['−', 'Then replace / remove', value.removed],
      ['⚿', 'UNLOCK PREPARATION', (value.preparation_boards || []).map(setup => setup.target).filter(name => !(steps[index - 1]?.preparation_boards || []).some(setup => setup.target === name))],
    ]) {
      if (!list?.length) continue;
      const delta = el('span', null, 'level-delta'); delta.append(symbolIcon(symbol, label), championStrip(list, value.board)); changes.append(delta);
    }
    if (!changes.childElementCount) changes.append(symbolIcon('=', 'Keep'));
    card.append(changes); nav.append(card);
  });
  const finalTab = button(result.branch_selection ? 'Adapted Final' : 'Final', () => {
    state.finalView = true; state.capView = false; renderBuildup(); $('buildup-scroll').scrollTop = 0;
  }, 'level-summary');
  finalTab.dataset.view = 'final'; finalTab.setAttribute('aria-pressed', String(state.finalView && !state.capView));
  finalTab.setAttribute('aria-label', t('Final team overview')); nav.append(finalTab);
  const capTab = button('Cap-out', openCapOut, 'level-summary');
  capTab.dataset.view = 'cap-out'; capTab.setAttribute('aria-pressed', String(state.capView));
  capTab.disabled = state.busy; nav.append(capTab);
  if (state.capView) { renderCapOut(host, actions, result); return; }
  if (state.finalView) return;
  renderGuideActions(actions, step, phase);
  renderPivotWarning(actions, step, phase);
  const phases = $('phase-navigation');
  const selector = el('select'); selector.id = 'phase-select'; const label = el('label', t('Phase')); label.htmlFor = selector.id;
  selector.append(new Option(t('Arrival · hold lineup'), '0'));
  (step.upgrades || []).forEach((value, index) => selector.append(new Option(phaseLabel(step, value, index + 1) + (index === step.upgrades.length - 1 && state.levelIndex === steps.length - 1 ? ` · ${t('ideal end goal')}` : ''), String(index + 1))));
  selector.value = String(state.phaseIndex); selector.addEventListener('change', () => { state.phaseIndex = Number(selector.value); renderBuildup(); });
  phases.append(label, selector);
  const primary = section(host, state.phaseIndex ? 'Upgrade — conditional board' : 'Arrival board — hold while finding upgrades', 'plan-section plan-board primary-board');
  if (state.showAllOptions && (phase.temporary_options || step.temporary_options || []).some(choice => !isFinalMember(choice.selected) && choice.recommended)) {
    const legend = paragraph(primary, '✓ Recommended · purple = selected · → Route guidance, not completed unlocks or owned units.', 'branch-recommendation-legend compact');
    legend.dataset.branchRecommendationLegend = 'true';
  }
  renderTeamBoard(primary, phase.board, { level: step.level, targets: new Set(result.champions), preparation: step.unlock_prep, guideStep: { ...step, board: phase.board, display_phase: state.phaseIndex, temporary_options: phase.temporary_options || step.temporary_options }, guideResult: result });
  if (!phase.board) {
    renderTemporaryOptions(host, step);
    renderPreparationBoards(host, step, result);
  }
  const details = detailsSection(host);
  paragraph(details, 'Conditional plan: no quest completion is assumed. Stars are upgrade targets, not confirmed purchases or guaranteed hits.', 'warning');
  if (step.upgrades?.length) paragraph(details, 'Hold the Arrival lineup until the incoming champions are found and their unlock tasks are complete. Buy before selling: secure the next phase’s additions before replacing its listed removals. These phases do not assert owned units, available gold, or completed unlocks.', 'warning');
  const transitions = section(details, 'Exact replacements — secure incoming units before selling');
  for (const replacement of phase.replacements || []) {
    const line = el('div', null, 'transition-line'); line.append(championStrip(replacement.outgoing), symbolIcon('→', 'Replace'), championStrip(replacement.incoming));
    line.append(replacementIcon(replacement));
    transitions.append(line);
  }
  if (!phase.replacements?.length) paragraph(transitions, 'No paired replacement in this phase.', 'muted');
  const paired = new Set((phase.replacements || []).flatMap(value => value.incoming)); const additions = (phase.added || []).filter(name => !paired.has(name));
  if (additions.length) { const row = el('div', null, 'transition-line'); row.append(el('strong', t('Open-slot additions:')), championStrip(additions)); transitions.append(row); }
  for (const transfer of phase.item_transfers || []) {
    const row = el('div', null, 'item-transfer'); row.append(roleIcon(transfer.kind), championStrip([transfer.outgoing]), symbolIcon('→', 'Planned holder:'), championStrip([transfer.incoming]));
    paragraph(row, transfer.method === 'sell_outgoing' ? 'Sell the outgoing holder to release items.' : 'Old holder stays fielded: requires an item remover or a later sale; no free transfer.', 'warning');
    if (transfer.condition) paragraph(row, transfer.condition, 'warning'); transitions.append(row);
  }
  for (const [labelText, key] of [['Buy / add first', 'added'], ['Then replace / remove', 'removed'], ['Keep', 'retained']]) {
    const row = el('div', null, 'transition-line'); row.append(el('strong', t(labelText)), championStrip(phase[key])); transitions.append(row);
  }
  if (phase.reason) paragraph(transitions, phase.reason, 'muted');
  for (const milestone of phase.milestones || []) paragraph(transitions, `• ${t(milestone)}`, 'warning');
  renderUnlocks(details, phase.unlocks, false, { ...phase, level: step.level });
  renderUnlocks(details, step.unlock_prep, true, step);
  paragraph(details, 'Slot portraits offer backend-validated playable branches. Selecting an alternative adapts the future Final while retaining required fielded core units. Recommended and selected are separate; route icons suggest trait and unlock preparation, not completed tasks. Clear a pin to rebuild toward the selected Final; Reset comp restores the original recommendation.', 'muted');
  const notes = section(details, 'Source policy & planning assumptions');
  for (const note of result.buildup_notes || []) paragraph(notes, `• ${t(note)}`, 'muted');
  paragraph(notes, 'No combat, item, economy, or quest-progress simulation is performed. Trait counts alone do not establish that any unlock quest is complete.', 'warning');
  syncControls();
}
async function clipboard(text) {
  if (isNative()) { await invokeNative('copy_text', { text }); return; }
  try {
    if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return; }
  } catch { /* The selectable text below also works when clipboard access is denied. */ }
  const host = $('details-content'); host.replaceChildren(); $('details-title').textContent = t('Copy text');
  paragraph(host, 'Clipboard access is unavailable. Select and copy this text manually.', 'warning');
  const area = el('textarea'); area.value = text; area.readOnly = true; area.rows = 16; area.style.width = '100%'; area.dir = 'auto'; host.append(area);
  if (!$('details-dialog').open) $('details-dialog').showModal(); area.focus(); area.select();
}
async function copyCode() {
  const result = exportBoard(); if (!result) return;
  const revision = state.revision;
  try { const value = await engine.request('encode', { champions: result.champions }); if (revision !== state.revision || exportBoard() !== result) return; await clipboard(value.code); status('TFT code ready to copy.'); }
  catch (error) { fail(error); }
}
function finiteItemLines(board) {
  return [
    ...(board.planned_item_builds || []).map(build => `${t('Assigned to:')} ${build.holder ? cn(build.holder) : t('Bench / unassigned')}: ${(build.items || []).map(t).join(', ')} · ${t('Physical item IDs:')} ${(build.item_ids || []).join(', ')} · ${build.source || ''}`),
    `${t('Bench / unassigned')}: ${(board.item_bench || []).map(item => `${t(item.item)} [${item.id}]`).join(', ') || '—'}`,
  ];
}
function inventoryLines() {
  const inventory = inventoryExport();
  return [t(inventory.mode === 'collected' ? 'Collected items · finite copies' : inventory.mode === 'planned' ? 'Planned items · what-if only' : 'Item ownership not confirmed'),
    ...(inventory.mode === 'planned' && inventory.items.length ? [t('Saved collected inventory — not used by this planned what-if')] : []),
    ...inventory.items.map(item => `${t(item.item)} [${item.id}] · ${item.holder ? cn(item.holder) : t('Bench / unassigned')}`),
    ...(inventory.starting_board ? [t('Confirmed starting board'), ...inventory.starting_board.champions.map(name => `${cn(name)} ${starsText(inventory.starting_board.stars[name])}`), `${t('Known available stars:')} ${JSON.stringify(inventory.available_stars)}`] : [])];
}
async function copyPlan() {
  const result = selectedResult(); if (!result) return;
  const revision = state.revision; const board = exportBoard(); const plan = state.capView && capPlan();
  if (plan) {
    const lines = [t('Cap-out transition'), `${t('Phase')} ${state.capPhase}/${plan.phases.length}`, t('TEAM'),
      ...board.champions.map(name => detailText(name, board)), t('ACTIVE TRAITS'),
      ...board.traits.filter(trait => trait.tier).map(trait => `${tn(trait.name)}: ${trait.count} → ${trait.tier}`),
      `${t('Match level')}: ${matchLevel(result)}`, t('Completed unlocks'),
      ...Object.entries(matchCompletions(result)).map(([name, level]) => `${cn(name)} · ${t('Completed at match level')} ${level}`),
      t('Manual match tracking changes board eligibility and the leveling path. Unlocked does not mean owned: purchases, copies, stars, items and cashout are never confirmed here.'),
      ...(plan.notes || []).map(t)];
    lines.push(t(plan.item_mode === 'collected' ? 'Collected items · finite copies' : 'Planned items · finite what-if'), ...inventoryLines(),
      t(plan.item_mode === 'collected' ? 'Confirmed starting board' : 'Original planned Final · what-if'), ...plan.from_board.champions.map(name => detailText(name, plan.from_board)), ...finiteItemLines(plan.from_board),
      t('Displayed phase item ledger'), ...finiteItemLines(board),
      `${t('Finite starting item budget:')} ${JSON.stringify(plan.item_budget || [])}`);
    for (const [index, phase] of plan.phases.entries()) {
      lines.push('', `${t('Buy before sell')} ${index + 1}/${plan.phases.length}`,
        `${t('Buy first')}: ${names(phase.added)}`, `${t('Then replace / remove')}: ${names(phase.removed)}`,
        `${t('Keep')}: ${names(phase.retained)}`, ...phase.board.champions.map(name => detailText(name, phase.board)));
      for (const change of phase.star_upgrades || []) lines.push(`${cn(change.champion)}: ${starsText(change.from)} → ${starsText(change.to)}`);
      for (const task of phase.unlock_checks || []) lines.push(`${cn(task.name)}: ${t(task.condition || task.requirements || '')}`);
      for (const transfer of phase.item_transfers || []) lines.push(`${cn(transfer.outgoing)} → ${cn(transfer.incoming)} · ${transfer.method} · ${t(transfer.condition || '')}`);
      lines.push(...finiteItemLines(phase.board));
      if (phase.reason) lines.push(t(phase.reason));
    }
    try { await clipboard(lines.join('\n')); status('Complete plan ready to copy.'); } catch (error) { fail(error); }
    return;
  }
  try {
    const value = await engine.request('format', { result }); if (revision !== state.revision || selectedResult() !== result || exportBoard() !== board || state.capView) return;
    const holds = finalHoldNames(result);
    const step = !state.finalView && guideSteps(result)[state.levelIndex];
    const phase = step && (state.phaseIndex ? step.upgrades?.[state.phaseIndex - 1] : step);
    const lines = [step ? `${t('Level')} ${step.level} · ${phaseLabel(step, phase, state.phaseIndex)}` : t('Final'),
      t('TEAM'), ...board.champions.map(name => detailText(name, board)), '',
      ...(holds.length ? [t('Hold for Final'), names(holds), t(finalHoldAdvice), ''] : []),
      t('ACTIVE TRAITS'), ...board.traits.filter(trait => trait.tier).map(trait => `${tn(trait.name)}: ${trait.count} → ${trait.tier}`), '', ...(result.strategy_notes || []).map(t), '', t(value.text)];
    for (const view of state.damageSessions.get(result)?.views.values() || []) {
      lines.push(`${view.level === null ? t('Final') : `${t('Level')} ${view.level} · ${t('Phase')} ${view.display_phase ?? view.phase}`}: ${[view.no_ad_items && t('NO AD items'), view.no_ap_items && t('NO AP items')].filter(Boolean).join(' / ')}`);
    }
    lines.push('', ...inventoryLines());
    await clipboard(lines.join('\n')); status('Complete plan ready to copy.');
  } catch (error) { fail(error); }
}
async function saveJSON() {
  if (!state.results.length) return;
  const used = new Set();
  for (const result of state.results) {
    result.champions.forEach(name => used.add(name));
    const cap = state.capPlans.get(result);
    for (const board of cap ? [cap.board, cap.from_board, ...(cap.phases || []).map(phase => phase.board)] : []) board.champions.forEach(name => used.add(name));
    for (const step of result.buildup || []) {
      for (const board of [step.board, step.fallback, step.goal_board, ...(step.upgrades || []).map(phase => phase.board), ...(step.preparation_boards || []).map(setup => setup.board)]) board?.champions.forEach(name => used.add(name));
    }
  }
  const unlocks = {};
  for (const name of [...used].sort()) if (champions.get(name).unlockable) unlocks[name] = Object.fromEntries(Object.entries(champions.get(name)).filter(([key]) => key.startsWith('unlock_')));
  const contents = JSON.stringify({
    version: init.data.version, results: state.results, unlocks,
    completed_unlocks: matchCompletions(), current_level: matchLevel(),
    item_session: inventoryExport(), ...inventoryPayload(),
    damage_alternatives: state.results.flatMap((result, result_index) => {
      const session = state.damageSessions.get(result);
      return session ? [{ result_index, views: [...session.views.values()].map(({ goal, ...view }) => view) }] : [];
    }),
    cap_out: state.results.flatMap((result, result_index) => state.capPlans.has(result) ? [{ result_index, ...state.capPlans.get(result) }] : []),
    export_view: { view: state.capView ? 'cap-out' : state.finalView ? 'final' : 'buildup', result_index: state.resultIndex, level: damageView().level, phase: state.capView ? state.capPhase : state.finalView ? null : state.phaseIndex, no_ad_items: $('no-ad-items').checked, no_ap_items: $('no-ap-items').checked, board: exportBoard() },
  }, null, 2);
  if (isNative()) {
    try { status(await invokeNative('save_json', { contents }) ? 'Canonical results saved as JSON.' : 'JSON export cancelled.'); }
    catch (error) { fail(error); }
    return;
  }
  const blob = new Blob([contents], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob); const anchor = el('a'); anchor.href = url; anchor.download = 'collab-teams.json'; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  status('Canonical results saved as JSON.');
}
function renderTutorial() {
  const steps = init.tutorial; const step = steps[state.tutorialIndex]; const host = $('tutorial-content'); host.replaceChildren();
  const body = section(host, step[0], 'tutorial-step');
  for (const [title, text] of step[1]) { heading(body, title, 4); paragraph(body, text); }
  $('tutorial-step').textContent = `${state.tutorialIndex + 1} / ${steps.length}`;
  if (state.tutorialIndex === 0) body.append(button('Language settings', () => { $('tutorial-dialog').close(); $('language').focus(); }));
  $('tutorial-previous').disabled = state.tutorialIndex === 0;
  $('tutorial-next').textContent = t(state.tutorialIndex === steps.length - 1 ? 'Done' : 'Next');
}
function renderLegal() {
  const host = $('legal-content'); host.replaceChildren();
  heading(host, `${init.app.name} · v${init.app.version}`);
  for (const [label, key] of [['Purpose', 'purpose'], ['Unapproved prototype', 'prototype'], ['As-is notice', 'liability'], ['Content & attribution', 'content']]) { const group = section(host, label); paragraph(group, init.legal[key]); }
  const riot = section(host, 'Riot Games disclosure'); const notice = el('p', init.legal.riot); notice.lang = 'en'; notice.dir = 'ltr'; riot.append(notice);
  const requirements = section(host, 'Public release requirements'); const list = el('ul'); for (const text of init.legal.public_release_requirements || []) list.append(el('li', t(text))); requirements.append(list);
  for (const [title, url] of init.legal.policy_links || []) { const p = el('p'); link(p, title, url); requirements.append(p); }
  const privacy = section(host, 'Local Rust computation');
  paragraph(privacy, 'Calculations and manual inputs stay on this device. The desktop app includes the Rust engine; the browser runs the same engine as bundled WebAssembly. No account, API key, ads or tracking.');
  paragraph(privacy, 'Only the language preference is saved locally. Calculation inputs are not uploaded to a server, and there is no remote calculation fallback.', 'muted');
  const sources = section(host, 'Sources & third-party notices'); paragraph(sources, init.data.version, 'muted'); paragraph(sources, init.artwork.attribution, 'muted');
  if (init.artwork.source) { const p = el('p'); link(p, 'CommunityDragon', init.artwork.source); sources.append(p); }
  for (const source of init.data.sources || []) { const item = el('div', null, 'detail-section'); link(item, source.name, source.url); if (source.note) paragraph(item, source.note, 'muted compact'); if (source.updated) paragraph(item, `${t('Updated:')} ${source.updated}`, 'muted compact'); sources.append(item); }
  for (const [label, path] of [['Shared Rust calculation source', 'engine-source/optimizer.rs'], ['Shared buildup source', 'engine-source/planner.rs'], ['Shared cap-out source', 'engine-source/cap_out.rs'], ['Shared positioning source', 'engine-source/positioning.rs'], ['Bundled source data', 'data/set19.json'], ['Third-party notices', 'THIRD_PARTY_NOTICES.txt'], ['Project documentation', 'README.md'], ['Legal documentation', 'LEGAL.md']]) { const p = el('p'); link(p, label, new URL(path, ROOT).href); sources.append(p); }
  const runtime = el('p'); link(runtime, 'Tauri · Rust desktop runtime · source and licenses', 'https://github.com/tauri-apps/tauri'); sources.append(runtime);
}
function localizeStatic() {
  document.querySelectorAll('[data-i18n]').forEach(node => { node.textContent = t(node.dataset.i18n.replace(/\\n/g, '\n')); });
  document.querySelectorAll('.compact-toolbar button[data-i18n]').forEach(node => {
    node.title = node.textContent; node.setAttribute('aria-label', node.textContent);
  });
  document.documentElement.lang = i18n.language.replace('_', '-');
  document.documentElement.dir = /^(ar|he|fa|ur)(?:[_-]|$)/i.test(i18n.language) ? 'rtl' : 'ltr';
  $('buildup-navigation').setAttribute('aria-label', t('Level buildup'));
  $('overview').setAttribute('aria-label', t('Final team overview'));
  $('locale-notice').textContent = i18n.notice || ''; $('locale-notice').hidden = !i18n.notice;
  $('locale-notice-banner').hidden = !i18n.notice || dismissedNoticeLanguage === i18n.language;
  $('dismiss-locale-notice').title = t('Dismiss language notice'); $('dismiss-locale-notice').setAttribute('aria-label', t('Dismiss language notice'));
  $('band').options[0].textContent = t('None');
  for (const option of [...$('band').options].slice(1)) option.textContent = tn(option.value);
  damageItemNotice();
}
async function applyLanguage() {
  if (state.languageBusy) return;
  closeWorkspaceInfo(); state.languageBusy = true; syncControls(); hideTraitTooltip();
  try {
    await i18n.setLanguage($('language').value);
    try { localStorage.setItem('tft-browser-language', i18n.language); } catch { /* A private browser may decline preference storage. */ }
    localizeStatic(); renderPool(); renderRoster(); renderResults(); renderOverview(); renderTutorial(); renderLegal();
    if ($('scenario-dialog').open) renderEarlyScenario();
    if ($('unlock-drawer').classList.contains('is-open')) renderUnlockTracker();
    relocalizeInventory(); relocalizeChampionBis();
    if ($('details-dialog').open && state.detailName) renderDetails();
    status('Language applied.');
  } catch (error) { fail(error); }
  finally { state.languageBusy = false; syncControls(); }
}
function bindEvents() {
  initializeTeamDrawer(t);
  initializeUnlockDrawer(t, () => { renderUnlockTracker(); $('unlock-status').textContent = ''; });
  $('cap-inventory').addEventListener('click', () => openInventory(startingSnapshot(), state.capView ? openCapOut : null));
  $('reset-comp').addEventListener('click', resetComp);
  $('show-all-options').addEventListener('change', () => {
    state.showAllOptions = $('show-all-options').checked;
    document.body.classList.toggle('selected-options-only', !state.showAllOptions);
    renderOverview(); syncControls();
  });
  for (const id of ['no-ad-items', 'no-ap-items']) $(id).addEventListener('change', changeDamageItems);
  $('path-change-cancel').addEventListener('click', () => $('path-change-dialog').close());
  $('path-change-dialog').addEventListener('close', () => { state.pendingPathChange = null; });
  $('path-change-confirm').addEventListener('click', () => {
    const pending = state.pendingPathChange; $('path-change-dialog').close();
    if (pending && pending.revision === state.revision && selectedResult() === pending.result) chooseTemporary(pending.step, pending.slot, pending.name, true);
  });
  $('match-level').addEventListener('change', () => applyMatchTracking({ ...matchCompletions() }, Number($('match-level').value)));
  $('unlock-reset').addEventListener('click', () => applyMatchTracking({}, matchLevel()));
  $('early-scenario').addEventListener('click', () => { renderEarlyScenario(); $('scenario-status').textContent = ''; $('scenario-dialog').showModal(); });
  $('scenario-champion').addEventListener('change', prefillEarlyScenario);
  $('scenario-clear').addEventListener('click', () => applyEarlyScenario({}));
  $('scenario-form').addEventListener('submit', event => {
    event.preventDefault();
    const name = $('scenario-champion').value;
    applyEarlyScenario({ ...scenarioAssumptions(), [name]: { level: Number($('scenario-level').value), stars: Number($('scenario-stars').value) } }, Number($('scenario-level').value));
  });
  document.querySelectorAll('[data-info-target]').forEach(node => node.addEventListener('click', () => {
    const content = $(node.dataset.infoTarget);
    if (content) openWorkspaceInfo(node.dataset.infoTitle || node.title || 'Details & sources', content, node);
  }));
  $('workspace-info-dialog').addEventListener('close', () => { if (!$('workspace-info-dialog').open) closeWorkspaceInfo(true); });
  $('dismiss-locale-notice').addEventListener('click', () => { dismissedNoticeLanguage = i18n.language; $('locale-notice-banner').hidden = true; });
  const roster = $('roster');
  document.addEventListener('pointerdown', event => { if (event.pointerType === 'touch') hideTraitTooltip(); }, true);
  document.addEventListener('keydown', event => { if (event.key === 'Escape') hideTraitTooltip(); }, true);
  new ResizeObserver(layoutRoster).observe(roster);
  $('search').addEventListener('input', updateRosterSearch);
  $('roster').addEventListener('keydown', event => {
    const tile = event.target.closest('.roster-tile');
    if (!tile || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    const tiles = [...$('roster').children]; const index = tiles.indexOf(tile);
    const columns = rosterColumns;
    const rtl = getComputedStyle($('roster')).direction === 'rtl';
    const delta = { ArrowLeft: rtl ? 1 : -1, ArrowRight: rtl ? -1 : 1, ArrowUp: -columns, ArrowDown: columns }[event.key];
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? tiles.length - 1 : Math.max(0, Math.min(tiles.length - 1, index + delta));
    event.preventDefault(); tiles[next].focus();
  });
  for (const id of ['level', 'minimum', 'ranking', 'band']) $(id).addEventListener('change', () => { invalidate(); if (id === 'level' || id === 'band') refreshPool(); });
  $('pool-lock').addEventListener('click', () => { if (state.poolBusy) return; state.poolLocked = !state.poolLocked; state.selected.clear(); state.lastSelected = null; $('search').value = ''; invalidate(); refreshPool(); });
  $('reset').addEventListener('click', () => {
    state.collab.clear(); state.locked.clear(); state.excluded.clear(); state.selected.clear(); state.lastSelected = null; state.stars = {}; state.completedUnlocks = {}; state.currentLevel = 0; state.poolLocked = false; $('search').value = ''; invalidate(); refreshPool();
  });
  $('team-lock').addEventListener('click', () => toggleTeam('locked')); $('exclude').addEventListener('click', () => toggleTeam('excluded'));
  document.querySelectorAll('[data-stars]').forEach(node => node.addEventListener('click', () => setStars(Number(node.dataset.stars))));
  $('auto-stars').addEventListener('click', () => setStars(null));
  $('unit-details').addEventListener('click', () => { const name = state.lastSelected && state.selected.has(state.lastSelected) ? state.lastSelected : [...state.selected][0]; if (name) showDetails(name); });
  $('find').addEventListener('click', findTeams);
  $('copy-code').addEventListener('click', copyCode); $('copy-plan').addEventListener('click', copyPlan); $('save-json').addEventListener('click', saveJSON);
  $('apply-language').addEventListener('click', applyLanguage);
  $('tutorial').addEventListener('click', () => { state.tutorialIndex = 0; renderTutorial(); $('tutorial-dialog').showModal(); });
  $('tutorial-previous').addEventListener('click', () => { if (state.tutorialIndex) { state.tutorialIndex -= 1; renderTutorial(); } });
  $('tutorial-next').addEventListener('click', () => { if (state.tutorialIndex === init.tutorial.length - 1) $('tutorial-dialog').close(); else { state.tutorialIndex += 1; renderTutorial(); } });
  $('legal').addEventListener('click', () => { renderLegal(); $('legal-dialog').showModal(); });
  document.querySelectorAll('[data-close]').forEach(node => node.addEventListener('click', () => $(node.dataset.close).close()));
  document.querySelectorAll('dialog').forEach(node => node.addEventListener('close', hideTraitTooltip));
  window.addEventListener('resize', hideTraitTooltip);
  window.addEventListener('resize', layoutRoster);
  window.addEventListener('scroll', event => { if (event.target !== $('trait-tooltip')) hideTraitTooltip(); }, true);
  window.addEventListener('pagehide', () => engine.close(), { once: true });
}
async function start() {
  bindEvents(); syncControls();
  try {
    init = await engine.initialize();
    await initializeItemIcons(ROOT, t);
    champions = new Map(init.data.champions.map(champion => [champion.name, champion]));
    await Promise.all([
      initializeChampionBis({ root: ROOT, t, cn, champions, link }),
      initializeInventory({ root: ROOT, t, cn, champions, portrait, maximum: () => selectedResult()?.planning_context?.size || Number($('level').value), busy: () => state.busy || state.languageBusy || !state.initialized, changed: inventoryChanged }),
    ]);
    definitions = new Map(init.data.traits.map(trait => [trait.name, trait]));
    descriptionLanguage = null;
    rosterOrder = [...champions.keys()].sort((a, b) => champions.get(a).cost - champions.get(b).cost || a.localeCompare(b));
    $('app-title').replaceChildren(document.createTextNode(`${init.app.name} `), el('small', `v${init.app.version}`)); document.title = isNative() ? init.app.title : `${init.app.title} · Browser preview`;
    $('riot-notice').textContent = init.legal.riot;
    for (let level = 3; level <= 11; level += 1) $('level').append(new Option(String(level), String(level))); $('level').value = '10';
    $('band').append(new Option('None', '')); init.bands.forEach(name => $('band').append(new Option(name, name)));
    $('language').replaceChildren(); init.languages.forEach(([code, name]) => $('language').append(new Option(`${name} [${code}]`, code)));
    let language = 'en_US'; try { language = localStorage.getItem('tft-browser-language') || language; } catch { /* Language selection still works without storage. */ }
    if (!init.languages.some(([code]) => code === language)) language = 'en_US';
    $('language').value = language; await i18n.setLanguage(language); localizeStatic();
    state.initialized = true; renderResults(); renderOverview(); renderTutorial(); renderLegal(); await refreshPool(); idleStatus(); syncControls();
  } catch (error) {
    state.initialized = false; fail(error); $('progress').hidden = true; document.body.setAttribute('aria-busy', 'false');
    paragraph($('buildup-placeholder'), 'The local Rust engine could not start. Reload this page or restart the desktop app. No remote calculation fallback is used.', 'error');
  }
}
start();
