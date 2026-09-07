(() => {
  const cfg = window.FIELD_EDITOR_CONFIG;

  const image = document.getElementById("satelliteImage");
  const svg = document.getElementById("boundarySvg");
  const addModeButton = document.getElementById("addMode");
  const selectModeButton = document.getElementById("selectMode");
  const deleteButton = document.getElementById("deletePoint");
  const undoButton = document.getElementById("undoButton");
  const resetButton = document.getElementById("resetButton");
  const saveButton = document.getElementById("saveButton");
  const generateButton = document.getElementById("generateButton");
  const showDetected = document.getElementById("showDetected");
  const pointCount = document.getElementById("pointCount");
  const saveStatus = document.getElementById("saveStatus");
  const routeResult = document.getElementById("routeResult");
  const routePreviewPanel = document.getElementById("routePreviewPanel");
  const routePreviewImage = document.getElementById("routePreviewImage");
  const routeSummary = document.getElementById("routeSummary");

  let points = [];
  let detectedPoints = [];
  let history = [];
  let selectedIndex = null;
  let draggingIndex = null;
  let mode = "move";
  let dirty = false;

  function clonePoints(value) {
    return value.map(p => [Number(p[0]), Number(p[1])]);
  }

  function pushHistory() {
    history.push(clonePoints(points));
    if (history.length > 50) history.shift();
  }

  function markDirty() {
    dirty = true;
    saveStatus.textContent = "Unsaved";
    routePreviewPanel.hidden = true;
  }

  function setMode(next) {
    mode = next;
    addModeButton.classList.toggle("primary", mode === "add");
    addModeButton.classList.toggle("secondary", mode !== "add");
    selectModeButton.classList.toggle("primary", mode === "move");
    selectModeButton.classList.toggle("secondary", mode !== "move");
    svg.classList.toggle("add-mode", mode === "add");
  }

  function svgPointFromEvent(event) {
    const rect = svg.getBoundingClientRect();
    const x = (event.clientX - rect.left) * image.naturalWidth / rect.width;
    const y = (event.clientY - rect.top) * image.naturalHeight / rect.height;
    return [x, y];
  }

  function nearestSegmentIndex(p) {
    let bestIndex = 0;
    let bestDistance = Infinity;

    for (let i = 0; i < points.length; i++) {
      const a = points[i];
      const b = points[(i + 1) % points.length];

      const vx = b[0] - a[0];
      const vy = b[1] - a[1];
      const wx = p[0] - a[0];
      const wy = p[1] - a[1];

      const vv = vx * vx + vy * vy || 1;
      let t = (wx * vx + wy * vy) / vv;
      t = Math.max(0, Math.min(1, t));

      const qx = a[0] + t * vx;
      const qy = a[1] + t * vy;
      const dx = p[0] - qx;
      const dy = p[1] - qy;
      const d = dx * dx + dy * dy;

      if (d < bestDistance) {
        bestDistance = d;
        bestIndex = i;
      }
    }

    return bestIndex;
  }

  function polygonPoints(value) {
    return value.map(p => `${p[0]},${p[1]}`).join(" ");
  }

  function render() {
    svg.setAttribute("viewBox", `0 0 ${image.naturalWidth} ${image.naturalHeight}`);
    svg.innerHTML = "";

    if (showDetected.checked && detectedPoints.length >= 3) {
      const detected = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
      detected.setAttribute("points", polygonPoints(detectedPoints));
      detected.setAttribute("class", "detected-polygon");
      svg.appendChild(detected);
    }

    if (points.length >= 3) {
      const polygon = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
      polygon.setAttribute("points", polygonPoints(points));
      polygon.setAttribute("class", "approved-polygon");
      svg.appendChild(polygon);
    }

    points.forEach((p, i) => {
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("cx", p[0]);
      circle.setAttribute("cy", p[1]);
      circle.setAttribute("r", i === selectedIndex ? "8" : "6");
      circle.setAttribute("class", `vertex ${i === selectedIndex ? "selected" : ""}`);
      circle.dataset.index = i;
      svg.appendChild(circle);
    });

    pointCount.textContent = String(points.length);
  }

  async function load() {
    const response = await fetch(cfg.apiUrl);
    const data = await response.json();

    points = clonePoints(data.points);
    detectedPoints = clonePoints(data.detected_points);
    history = [];
    selectedIndex = null;
    dirty = false;
    saveStatus.textContent = data.boundary_locked ? "Approved" : "CV result";
    render();
  }

  async function saveBoundary() {
    if (points.length < 3) throw new Error("A boundary needs at least 3 points.");

    saveStatus.textContent = "Saving…";

    const response = await fetch(cfg.saveUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        points,
        source: "manual_edit"
      })
    });

    if (!response.ok) throw new Error(await response.text());

    dirty = false;
    history = [];
    saveStatus.textContent = "Approved";
  }

  svg.addEventListener("pointerdown", event => {
    const target = event.target;

    if (target.classList.contains("vertex")) {
      const i = Number(target.dataset.index);
      selectedIndex = i;

      if (mode === "move") {
        pushHistory();
        draggingIndex = i;
        svg.setPointerCapture(event.pointerId);
      }

      render();
      return;
    }

    if (mode === "add" && points.length >= 2) {
      pushHistory();
      const p = svgPointFromEvent(event);
      const segment = nearestSegmentIndex(p);
      points.splice(segment + 1, 0, p);
      selectedIndex = segment + 1;
      markDirty();
      setMode("move");
      render();
    }
  });

  svg.addEventListener("pointermove", event => {
    if (draggingIndex === null) return;

    points[draggingIndex] = svgPointFromEvent(event);
    markDirty();
    render();
  });

  svg.addEventListener("pointerup", () => {
    draggingIndex = null;
  });

  selectModeButton.addEventListener("click", () => setMode("move"));
  addModeButton.addEventListener("click", () => setMode("add"));

  deleteButton.addEventListener("click", () => {
    if (selectedIndex === null) return;

    if (points.length <= 3) {
      alert("A field boundary must have at least three points.");
      return;
    }

    pushHistory();
    points.splice(selectedIndex, 1);
    selectedIndex = null;
    markDirty();
    render();
  });

  undoButton.addEventListener("click", () => {
    if (!history.length) return;

    points = history.pop();
    selectedIndex = null;
    markDirty();
    render();
  });

  showDetected.addEventListener("change", render);

  resetButton.addEventListener("click", async () => {
    if (!confirm("Reset this boundary to the original CV detection?")) return;

    const response = await fetch(cfg.resetUrl, { method: "POST" });
    const data = await response.json();

    points = clonePoints(data.points);
    history = [];
    selectedIndex = null;
    dirty = false;
    saveStatus.textContent = "CV result";
    routePreviewPanel.hidden = true;
    render();
  });

  saveButton.addEventListener("click", async () => {
    saveButton.disabled = true;

    try {
      await saveBoundary();
    } catch (error) {
      console.error(error);
      alert(`Could not save boundary:\n${error.message}`);
      saveStatus.textContent = "Save failed";
    } finally {
      saveButton.disabled = false;
    }
  });

  generateButton.addEventListener("click", async () => {
    generateButton.disabled = true;
    saveButton.disabled = true;
    routeResult.hidden = false;
    routeResult.className = "route-result working";
    routeResult.textContent = "Saving boundary and generating route…";
    routePreviewPanel.hidden = true;

    try {
      await saveBoundary();

      const response = await fetch(cfg.routeUrl, {
        method: "POST"
      });

      const data = await response.json();

      if (!response.ok || !data.ok) {
        throw new Error(data.error || "Route generation failed.");
      }

      routeResult.className = "route-result success";
      routeResult.textContent =
        `${data.route_mode}: ${data.passes} passes, ${data.turns} turns, ` +
        `about ${data.estimated_time_min} min`;

      routeSummary.textContent =
        `${data.route_mode} · ${data.passes} passes · ${data.estimated_time_min} min`;

      // Cache-busting timestamp so browser shows the newly generated PNG.
      routePreviewImage.src = `${cfg.routeImageUrl}?t=${Date.now()}`;
      routePreviewPanel.hidden = false;

    } catch (error) {
      console.error(error);
      routeResult.className = "route-result error";
      routeResult.textContent = error.message;
      alert(`Could not generate route:\n${error.message}`);
    } finally {
      generateButton.disabled = false;
      saveButton.disabled = false;
    }
  });

  window.addEventListener("beforeunload", event => {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });

  if (image.complete) load();
  else image.addEventListener("load", load);
})();
