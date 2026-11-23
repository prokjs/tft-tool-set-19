const drawers = new Set();

function createDrawer({ panelId, toggleId, closeId, expandId, openLabel, closeLabel, onOpen = () => {} }) {
  let panel;
  let toggle;
  let expand;
  let expanded = false;
  let opened = false;
  let drag = null;
  let translate = value => value;
  let didOpen = onOpen;
  const close = () => setOpen(false);
  drawers.add(close);

  function setOpen(value, restoreFocus = false) {
    if (!panel) return;
    const opening = value && !opened;
    if (value) for (const other of drawers) if (other !== close) other();
    opened = value;
    panel.classList.toggle('is-open', value);
    panel.inert = !value;
    panel.setAttribute('aria-hidden', String(!value));
    toggle.setAttribute('aria-expanded', String(value));
    toggle.setAttribute('aria-label', translate(value ? closeLabel : openLabel));
    if (restoreFocus || (!value && panel.contains(document.activeElement))) toggle.focus();
    if (opening) didOpen();
  }

  function setExpanded(value) {
    expanded = value;
    for (const element of [panel, toggle]) element.style.setProperty('--team-drawer-size', value ? '800px' : '420px');
    panel.classList.toggle('is-expanded', value);
    if (expand) {
      const label = translate(value ? 'Collapse suggested teams' : 'Expand suggested teams');
      expand.setAttribute('aria-expanded', String(value));
      expand.setAttribute('aria-label', label);
      expand.title = label;
    }
    for (const preview of panel.querySelectorAll('.suggested-team-preview')) preview.hidden = !expanded;
  }

  function initialize(localize = value => value, openedCallback = onOpen) {
    translate = localize;
    didOpen = openedCallback;
    if (panel) return;
    panel = document.getElementById(panelId);
    toggle = document.getElementById(toggleId);
    if (!panel || !toggle) { panel = null; return; }
    expand = expandId && document.getElementById(expandId);
    setExpanded(expanded);
    if (expand) expand.addEventListener('click', () => setExpanded(!expanded));
    setOpen(false);
    let suppressClick = false;
    toggle.addEventListener('click', event => {
      const suppress = suppressClick && event.detail !== 0;
      suppressClick = false;
      if (suppress) { event.preventDefault(); return; }
      setOpen(!opened);
    });
    document.getElementById(closeId).addEventListener('click', () => setOpen(false, true));
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && opened) { setOpen(false, true); event.preventDefault(); }
    });
    const start = event => {
      if (!event.isPrimary || (event.pointerType === 'mouse' && event.button !== 0)) return;
      if (event.target.closest('button') && event.currentTarget !== toggle) return;
      suppressClick = false;
      drag = { id:event.pointerId, x:event.clientX, y:event.clientY, start:opened ? 0 : panel.offsetWidth, active:false };
      event.currentTarget.setPointerCapture(event.pointerId);
    };
    const move = event => {
      if (!drag || drag.id !== event.pointerId) return;
      const dx = event.clientX - drag.x;
      if (!drag.active && Math.abs(dx) < 8) return;
      if (!drag.active && Math.abs(event.clientY - drag.y) > Math.abs(dx)) return;
      drag.active = true;
      for (const other of drawers) if (other !== close) other();
      const width = panel.offsetWidth;
      const offset = Math.max(0, Math.min(width, drag.start + dx));
      panel.classList.add('is-dragging');
      toggle.classList.add('is-dragging');
      panel.style.transform = `translateX(${offset}px)`;
      toggle.style.right = `${width - offset}px`;
      drag.offset = offset;
      event.preventDefault();
    };
    const finish = event => {
      if (!drag || drag.id !== event.pointerId) return;
      const active = drag.active;
      const next = event.type === 'pointercancel' ? opened : drag.offset < panel.offsetWidth / 2;
      drag = null;
      panel.classList.remove('is-dragging');
      toggle.classList.remove('is-dragging');
      panel.style.removeProperty('transform');
      toggle.style.removeProperty('right');
      if (active) {
        suppressClick = event.currentTarget === toggle && event.type !== 'pointercancel';
        setOpen(next);
      }
    };
    for (const handle of [toggle, panel.querySelector('.section-heading')]) {
      handle.addEventListener('pointerdown', start);
      handle.addEventListener('pointermove', move);
      handle.addEventListener('pointerup', finish);
      handle.addEventListener('pointercancel', finish);
    }
  }
  return { initialize, setOpen, refresh: () => { if (panel) setExpanded(expanded); } };
}

const teams = createDrawer({ panelId:'team-drawer', toggleId:'team-drawer-toggle', closeId:'team-drawer-close', expandId:'team-drawer-expand', openLabel:'Open suggested teams', closeLabel:'Close suggested teams' });
const unlocks = createDrawer({ panelId:'unlock-drawer', toggleId:'unlock-tracker', closeId:'unlock-drawer-close', openLabel:'Completed unlocks', closeLabel:'Close' });
let previousCount = 0;

export function initializeTeamDrawer(localize = value => value) { teams.initialize(localize); }
export function initializeUnlockDrawer(localize, onOpen) { unlocks.initialize(localize, onOpen); }
export function closeTeamDrawer() { teams.setOpen(false); }
export function updateTeamDrawer(count) {
  teams.refresh();
  const next = Math.max(0, Number(count) || 0);
  const counter = document.getElementById('team-drawer-count');
  if (counter) counter.textContent = String(next);
  if (next > 0 && previousCount === 0) teams.setOpen(true);
  if (next === 0) teams.setOpen(false);
  previousCount = next;
}
