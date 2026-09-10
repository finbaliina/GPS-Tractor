(() => {
  const cfg = window.FARM_AREA_CONFIG;
  const viewport = document.getElementById('farmAreaMap');
  const canvas = document.getElementById('farmAreaCanvas');
  const image = document.getElementById('farmOverviewImage');
  const svg = document.getElementById('farmAreaSvg');
  const form = document.getElementById('farmAreaForm');
  const input = document.getElementById('areasJson');
  const count = document.getElementById('areaCount');
  const undo = document.getElementById('undoArea');
  const clear = document.getElementById('clearAreas');
  const deleteSelected = document.getElementById('deleteArea');

  let areas = [];
  let selectedIndex = null;
  let dragStart = null;
  let dragNow = null;
  let scale = 1, panX = 0, panY = 0;
  let panning = false, panLastX = 0, panLastY = 0;

  function applyView() {
    canvas.style.transform = `translate(${panX}px, ${panY}px) scale(${scale})`;
  }

  function pointFromEvent(event) {
    const rect = svg.getBoundingClientRect();
    const x = (event.clientX - rect.left) * cfg.width / rect.width;
    const y = (event.clientY - rect.top) * cfg.height / rect.height;
    return [Math.max(0, Math.min(cfg.width, x)), Math.max(0, Math.min(cfg.height, y))];
  }

  function normalised(a, b) {
    return {x1: Math.min(a[0], b[0]), y1: Math.min(a[1], b[1]), x2: Math.max(a[0], b[0]), y2: Math.max(a[1], b[1])};
  }

  function addRect(rect, className, index = null) {
    const el = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    el.setAttribute('x', rect.x1); el.setAttribute('y', rect.y1);
    el.setAttribute('width', Math.max(0, rect.x2 - rect.x1));
    el.setAttribute('height', Math.max(0, rect.y2 - rect.y1));
    el.setAttribute('class', className);
    if (index !== null) {
      el.dataset.index = index;
      el.addEventListener('pointerdown', event => {
        if (event.shiftKey) return;
        event.stopPropagation(); selectedIndex = index; render();
      });
    }
    svg.appendChild(el);
  }

  function render() {
    svg.setAttribute('viewBox', `0 0 ${cfg.width} ${cfg.height}`);
    svg.innerHTML = '';
    areas.forEach((area, index) => addRect(area, `farm-area-rect ${index === selectedIndex ? 'selected' : ''}`, index));
    if (dragStart && dragNow) addRect(normalised(dragStart, dragNow), 'farm-area-draft');
    count.textContent = String(areas.length);
    input.value = JSON.stringify(areas);
  }

  viewport.addEventListener('wheel', event => {
    event.preventDefault();
    const rect = viewport.getBoundingClientRect();
    const px = event.clientX - rect.left, py = event.clientY - rect.top;
    const old = scale;
    scale = Math.max(1, Math.min(6, scale * (event.deltaY < 0 ? 1.18 : 1 / 1.18)));
    const ratio = scale / old;
    panX = px - (px - panX) * ratio;
    panY = py - (py - panY) * ratio;
    if (scale === 1) { panX = 0; panY = 0; }
    applyView();
  }, {passive: false});

  svg.addEventListener('pointerdown', event => {
    if (event.shiftKey) {
      panning = true; panLastX = event.clientX; panLastY = event.clientY;
      viewport.setPointerCapture(event.pointerId); viewport.classList.add('is-panning');
      return;
    }
    if (event.target !== svg) return;
    selectedIndex = null;
    dragStart = pointFromEvent(event); dragNow = dragStart;
    svg.setPointerCapture(event.pointerId); render();
  });

  viewport.addEventListener('pointermove', event => {
    if (!panning) return;
    panX += event.clientX - panLastX; panY += event.clientY - panLastY;
    panLastX = event.clientX; panLastY = event.clientY; applyView();
  });
  viewport.addEventListener('pointerup', event => {
    if (!panning) return;
    panning = false; viewport.classList.remove('is-panning');
    try { viewport.releasePointerCapture(event.pointerId); } catch (_) {}
  });

  svg.addEventListener('pointermove', event => {
    if (dragStart) { dragNow = pointFromEvent(event); render(); }
  });
  svg.addEventListener('pointerup', event => {
    if (!dragStart || !dragNow) return;
    const rect = normalised(dragStart, dragNow); dragStart = null; dragNow = null;
    if (rect.x2 - rect.x1 >= 8 && rect.y2 - rect.y1 >= 8) { areas.push(rect); selectedIndex = areas.length - 1; }
    try { svg.releasePointerCapture(event.pointerId); } catch (_) {}
    render();
  });

  undo.addEventListener('click', () => { if (areas.length) areas.pop(); selectedIndex = null; render(); });
  clear.addEventListener('click', () => { areas = []; selectedIndex = null; render(); });
  deleteSelected.addEventListener('click', () => { if (selectedIndex === null) return; areas.splice(selectedIndex, 1); selectedIndex = null; render(); });
  form.addEventListener('submit', event => {
    if (!areas.length) { event.preventDefault(); alert('Draw at least one box around the land you want to scan.'); return; }
    input.value = JSON.stringify(areas);
  });
  if (image.complete) render(); else image.addEventListener('load', render);
  applyView();
})();
