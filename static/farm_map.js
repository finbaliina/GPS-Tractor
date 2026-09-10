(() => {
  const viewport = document.querySelector('.farm-map-wrap.panzoom-viewport');
  const canvas = viewport?.querySelector('.panzoom-canvas');
  if (!viewport || !canvas) return;

  let scale = 1;
  let x = 0;
  let y = 0;
  let panning = false;
  let moved = false;
  let lastX = 0;
  let lastY = 0;

  function apply() {
    canvas.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
  }

  viewport.addEventListener('wheel', event => {
    event.preventDefault();
    const rect = viewport.getBoundingClientRect();
    const px = event.clientX - rect.left;
    const py = event.clientY - rect.top;
    const oldScale = scale;
    scale = Math.max(1, Math.min(6, scale * (event.deltaY < 0 ? 1.18 : 1 / 1.18)));
    const ratio = scale / oldScale;
    x = px - (px - x) * ratio;
    y = py - (py - y) * ratio;
    if (scale === 1) { x = 0; y = 0; }
    apply();
  }, { passive: false });

  viewport.addEventListener('pointerdown', event => {
    if (event.target.closest('.farm-map-pending-link')) return;
    panning = true;
    moved = false;
    lastX = event.clientX;
    lastY = event.clientY;
    viewport.setPointerCapture(event.pointerId);
    viewport.classList.add('is-panning');
  });

  viewport.addEventListener('pointermove', event => {
    if (!panning) return;
    const dx = event.clientX - lastX;
    const dy = event.clientY - lastY;
    if (Math.abs(dx) + Math.abs(dy) > 2) moved = true;
    x += dx; y += dy;
    lastX = event.clientX; lastY = event.clientY;
    apply();
  });

  viewport.addEventListener('pointerup', event => {
    panning = false;
    viewport.classList.remove('is-panning');
    try { viewport.releasePointerCapture(event.pointerId); } catch (_) {}
  });

  apply();
})();
