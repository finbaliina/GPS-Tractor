(() => {
  const cfg = window.FARM_AREA_CONFIG;
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

  function pointFromEvent(event) {
    const rect = svg.getBoundingClientRect();
    const x = (event.clientX - rect.left) * cfg.width / rect.width;
    const y = (event.clientY - rect.top) * cfg.height / rect.height;
    return [
      Math.max(0, Math.min(cfg.width, x)),
      Math.max(0, Math.min(cfg.height, y))
    ];
  }

  function normalised(a, b) {
    return {
      x1: Math.min(a[0], b[0]),
      y1: Math.min(a[1], b[1]),
      x2: Math.max(a[0], b[0]),
      y2: Math.max(a[1], b[1])
    };
  }

  function addRect(rect, className, index = null) {
    const el = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    el.setAttribute('x', rect.x1);
    el.setAttribute('y', rect.y1);
    el.setAttribute('width', Math.max(0, rect.x2 - rect.x1));
    el.setAttribute('height', Math.max(0, rect.y2 - rect.y1));
    el.setAttribute('class', className);
    if (index !== null) {
      el.dataset.index = index;
      el.addEventListener('pointerdown', event => {
        event.stopPropagation();
        selectedIndex = index;
        render();
      });
    }
    svg.appendChild(el);
  }

  function render() {
    svg.setAttribute('viewBox', `0 0 ${cfg.width} ${cfg.height}`);
    svg.innerHTML = '';

    areas.forEach((area, index) => {
      addRect(area, `farm-area-rect ${index === selectedIndex ? 'selected' : ''}`, index);
    });

    if (dragStart && dragNow) {
      addRect(normalised(dragStart, dragNow), 'farm-area-draft');
    }

    // Crosshair showing the geocoded centre. It is only a location hint.
    const cx = cfg.width / 2;
    const cy = cfg.height / 2;
    const marker = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    marker.setAttribute('cx', cx);
    marker.setAttribute('cy', cy);
    marker.setAttribute('r', 6);
    marker.setAttribute('class', 'farm-search-point');
    svg.appendChild(marker);

    count.textContent = String(areas.length);
    input.value = JSON.stringify(areas);
    undo.disabled = areas.length === 0;
    clear.disabled = areas.length === 0;
    deleteSelected.disabled = selectedIndex === null;
  }

  svg.addEventListener('pointerdown', event => {
    if (event.target.classList.contains('farm-area-rect')) return;
    selectedIndex = null;
    dragStart = pointFromEvent(event);
    dragNow = dragStart;
    svg.setPointerCapture(event.pointerId);
    render();
  });

  svg.addEventListener('pointermove', event => {
    if (!dragStart) return;
    dragNow = pointFromEvent(event);
    render();
  });

  svg.addEventListener('pointerup', event => {
    if (!dragStart || !dragNow) return;
    const rect = normalised(dragStart, dragNow);
    dragStart = null;
    dragNow = null;
    if (rect.x2 - rect.x1 >= 8 && rect.y2 - rect.y1 >= 8) {
      areas.push(rect);
      selectedIndex = areas.length - 1;
    }
    try { svg.releasePointerCapture(event.pointerId); } catch (_) {}
    render();
  });

  undo.addEventListener('click', () => {
    if (!areas.length) return;
    areas.pop();
    selectedIndex = null;
    render();
  });

  clear.addEventListener('click', () => {
    areas = [];
    selectedIndex = null;
    render();
  });

  deleteSelected.addEventListener('click', () => {
    if (selectedIndex === null) return;
    areas.splice(selectedIndex, 1);
    selectedIndex = null;
    render();
  });

  form.addEventListener('submit', event => {
    if (!areas.length) {
      event.preventDefault();
      alert('Draw at least one box around the land you want to scan.');
      return;
    }
    input.value = JSON.stringify(areas);
  });

  if (image.complete) render();
  else image.addEventListener('load', render);
})();
