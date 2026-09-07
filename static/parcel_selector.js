(() => {
  const polygons = Array.from(document.querySelectorAll('.candidate-parcel'));
  const info = window.PARCEL_INFO || {};
  const selected = new Set();
  const count = document.getElementById('selectedCount');
  const area = document.getElementById('selectedArea');
  const input = document.getElementById('selectedIds');
  const form = document.getElementById('confirmForm');

  function setParcel(id, enabled) {
    polygons.filter(p => p.dataset.parcelId === id).forEach(p => {
      p.classList.toggle('selected', enabled);
    });
    if (enabled) selected.add(id); else selected.delete(id);
  }

  function refresh() {
    let areaM2 = 0;
    selected.forEach(id => areaM2 += Number((info[id] || {}).area_m2 || 0));
    count.textContent = selected.size;
    area.textContent = `${(areaM2 / 10000).toFixed(1)} ha`;
    input.value = Array.from(selected).join('|');
  }

  polygons.forEach(poly => {
    const id = poly.dataset.parcelId;
    if ((info[id] || {}).contains_search_point) selected.add(id);
    poly.addEventListener('click', () => {
      setParcel(id, !selected.has(id));
      refresh();
    });
  });

  document.getElementById('selectContaining')?.addEventListener('click', () => {
    Object.entries(info).forEach(([id, item]) => setParcel(id, !!item.contains_search_point));
    refresh();
  });

  document.getElementById('clearSelection')?.addEventListener('click', () => {
    Array.from(selected).forEach(id => setParcel(id, false));
    refresh();
  });

  form?.addEventListener('submit', (event) => {
    if (!selected.size) {
      event.preventDefault();
      alert('Select at least one parcel.');
      return;
    }
    refresh();
  });

  refresh();
})();
