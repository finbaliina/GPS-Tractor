(() => {
  const cfg = window.FIELD_EDITOR_CONFIG;

  const image = document.getElementById("satelliteImage");
  const svg = document.getElementById("boundarySvg");
  const addModeButton = document.getElementById("addMode");
  const selectModeButton = document.getElementById("selectMode");
  const deleteButton = document.getElementById("deletePoint");
  const drawCircleButton = document.getElementById("drawCircle");
  const drawSquareButton = document.getElementById("drawSquare");
  const drawRectangleButton = document.getElementById("drawRectangle");
  const drawAngledRectangleButton = document.getElementById("drawAngledRectangle");
  const angledRectangleHelp = document.getElementById("angledRectangleHelp");
  const deleteObstacleButton = document.getElementById("deleteObstacle");
  const undoButton = document.getElementById("undoButton");
  const resetButton = document.getElementById("resetButton");
  const saveButton = document.getElementById("saveButton");
  const generateButton = document.getElementById("generateButton");
  const showDetected = document.getElementById("showDetected");
  const pointCount = document.getElementById("pointCount");
  const obstacleCount = document.getElementById("obstacleCount");
  const saveStatus = document.getElementById("saveStatus");
  const routeResult = document.getElementById("routeResult");
  const routePreviewPanel = document.getElementById("routePreviewPanel");
  const routePreviewImage = document.getElementById("routePreviewImage");
  const routeSummary = document.getElementById("routeSummary");

  let points = [];
  let detectedPoints = [];
  let obstacles = [];
  let history = [];
  let selectedIndex = null;
  let selectedObstacleIndex = null;
  let draggingIndex = null;
  let mode = "move";
  let dirty = false;
  let drawStart = null;
  let draftObstacle = null;
  let angledBaseStart = null;
  let angledBaseEnd = null;
  let angledStage = 0;

  function clonePoints(value) {
    return (value || []).map(p => [Number(p[0]), Number(p[1])]);
  }

  function cloneObstacles(value) {
    return (value || []).map(obstacle => ({
      shape: obstacle.shape,
      points: clonePoints(obstacle.points)
    }));
  }

  function pushHistory() {
    history.push({
      points: clonePoints(points),
      obstacles: cloneObstacles(obstacles)
    });
    if (history.length > 50) history.shift();
  }

  function markDirty() {
    dirty = true;
    saveStatus.textContent = "Unsaved";
    routePreviewPanel.hidden = true;
  }

  function setMode(next) {
    mode = next;
    selectedIndex = null;
    selectedObstacleIndex = null;
    drawStart = null;
    draftObstacle = null;
    angledBaseStart = null;
    angledBaseEnd = null;
    angledStage = 0;

    const modeButtons = [
      [selectModeButton, "move"],
      [addModeButton, "add"],
      [drawCircleButton, "circle"],
      [drawSquareButton, "square"],
      [drawRectangleButton, "rectangle"],
      [drawAngledRectangleButton, "angledRectangle"]
    ];
    modeButtons.forEach(([button, buttonMode]) => {
      button.classList.toggle("primary", mode === buttonMode);
      button.classList.toggle("secondary", mode !== buttonMode);
    });

    svg.classList.toggle("add-mode", mode === "add");
    svg.classList.toggle("draw-mode", ["circle", "square", "rectangle", "angledRectangle"].includes(mode));
    if (angledRectangleHelp) {
      angledRectangleHelp.hidden = mode !== "angledRectangle";
      if (mode === "angledRectangle") angledRectangleHelp.textContent = "Drag the first side, then click to set the rectangle width.";
    }
    render();
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
      const distance = dx * dx + dy * dy;
      if (distance < bestDistance) {
        bestDistance = distance;
        bestIndex = i;
      }
    }
    return bestIndex;
  }

  function polygonPoints(value) {
    return value.map(p => `${p[0]},${p[1]}`).join(" ");
  }

  function circlePoints(start, end, segments = 32) {
    const dx = end[0] - start[0];
    const dy = end[1] - start[1];
    const radius = Math.sqrt(dx * dx + dy * dy);
    const result = [];
    for (let i = 0; i < segments; i++) {
      const angle = 2 * Math.PI * i / segments;
      result.push([
        start[0] + radius * Math.cos(angle),
        start[1] + radius * Math.sin(angle)
      ]);
    }
    return result;
  }

  function squarePoints(start, end) {
    const halfSize = Math.max(Math.abs(end[0] - start[0]), Math.abs(end[1] - start[1]));
    return [
      [start[0] - halfSize, start[1] - halfSize],
      [start[0] + halfSize, start[1] - halfSize],
      [start[0] + halfSize, start[1] + halfSize],
      [start[0] - halfSize, start[1] + halfSize]
    ];
  }

  function rectanglePoints(start, end) {
    return [
      [start[0], start[1]],
      [end[0], start[1]],
      [end[0], end[1]],
      [start[0], end[1]]
    ];
  }

  function angledRectanglePoints(start, end, widthPoint) {
    const dx = end[0] - start[0];
    const dy = end[1] - start[1];
    const length = Math.sqrt(dx * dx + dy * dy);
    if (length < 0.001) return [start, end, end, start];
    const nx = -dy / length;
    const ny = dx / length;
    const width = (widthPoint[0] - start[0]) * nx + (widthPoint[1] - start[1]) * ny;
    const ox = nx * width;
    const oy = ny * width;
    return [
      [start[0], start[1]],
      [end[0], end[1]],
      [end[0] + ox, end[1] + oy],
      [start[0] + ox, start[1] + oy]
    ];
  }

  function makeObstacle(shape, start, end) {
    let obstaclePoints;
    if (shape === "circle") {
      obstaclePoints = circlePoints(start, end);
    } else if (shape === "rectangle") {
      obstaclePoints = rectanglePoints(start, end);
    } else {
      obstaclePoints = squarePoints(start, end);
    }
    return { shape, points: obstaclePoints };
  }

  function appendObstacleShape(obstacle, index, draft = false) {
    if (!obstacle || obstacle.points.length < 3) return;
    const polygon = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
    polygon.setAttribute("points", polygonPoints(obstacle.points));
    polygon.setAttribute(
      "class",
      `exclusion-zone ${index === selectedObstacleIndex ? "selected" : ""} ${draft ? "draft" : ""}`
    );
    if (!draft) {
      polygon.dataset.obstacleIndex = index;
    }
    svg.appendChild(polygon);
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

    obstacles.forEach((obstacle, index) => appendObstacleShape(obstacle, index));
    if (draftObstacle) appendObstacleShape(draftObstacle, -1, true);

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
    obstacleCount.textContent = String(obstacles.length);
  }

  async function load() {
    const response = await fetch(cfg.apiUrl);
    const data = await response.json();
    points = clonePoints(data.points);
    detectedPoints = clonePoints(data.detected_points);
    obstacles = cloneObstacles(data.obstacles);
    history = [];
    selectedIndex = null;
    selectedObstacleIndex = null;
    dirty = false;
    saveStatus.textContent = data.boundary_locked ? "Approved" : "Scanned boundary";
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
        obstacles,
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

    if (mode === "angledRectangle") {
      const point = svgPointFromEvent(event);
      if (angledStage === 1 && angledBaseStart && angledBaseEnd) {
        const shape = { shape: "angledRectangle", points: angledRectanglePoints(angledBaseStart, angledBaseEnd, point) };
        const width = Math.hypot(shape.points[3][0] - shape.points[0][0], shape.points[3][1] - shape.points[0][1]);
        if (width >= 3) {
          pushHistory();
          obstacles.push(shape);
          selectedObstacleIndex = obstacles.length - 1;
          markDirty();
        }
        setMode("move");
        return;
      }
      angledBaseStart = point;
      angledBaseEnd = point;
      drawStart = point;
      angledStage = 0;
      draftObstacle = { shape: "angledRectangle", points: angledRectanglePoints(point, point, point) };
      svg.setPointerCapture(event.pointerId);
      render();
      return;
    }

    if (target.classList.contains("exclusion-zone") && !target.classList.contains("draft")) {
      selectedObstacleIndex = Number(target.dataset.obstacleIndex);
      selectedIndex = null;
      render();
      return;
    }

    if (target.classList.contains("vertex")) {
      const i = Number(target.dataset.index);
      selectedIndex = i;
      selectedObstacleIndex = null;
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
      selectedObstacleIndex = null;
      markDirty();
      setMode("move");
      render();
      return;
    }

    if (mode === "circle" || mode === "square" || mode === "rectangle") {
      drawStart = svgPointFromEvent(event);
      draftObstacle = makeObstacle(mode, drawStart, drawStart);
      svg.setPointerCapture(event.pointerId);
      render();
    }
  });

  svg.addEventListener("pointermove", event => {
    if (mode === "angledRectangle") {
      const point = svgPointFromEvent(event);
      if (drawStart && angledBaseStart) {
        angledBaseEnd = point;
        draftObstacle = { shape: "angledRectangle", points: angledRectanglePoints(angledBaseStart, angledBaseEnd, angledBaseEnd) };
        render();
        return;
      }
      if (angledStage === 1 && angledBaseStart && angledBaseEnd) {
        draftObstacle = { shape: "angledRectangle", points: angledRectanglePoints(angledBaseStart, angledBaseEnd, point) };
        render();
        return;
      }
    }
    if (draggingIndex !== null) {
      points[draggingIndex] = svgPointFromEvent(event);
      markDirty();
      render();
      return;
    }
    if (drawStart && (mode === "circle" || mode === "square" || mode === "rectangle")) {
      draftObstacle = makeObstacle(mode, drawStart, svgPointFromEvent(event));
      render();
    }
  });

  svg.addEventListener("pointerup", event => {
    draggingIndex = null;
    if (mode === "angledRectangle" && drawStart && angledBaseStart) {
      angledBaseEnd = svgPointFromEvent(event);
      const length = Math.hypot(angledBaseEnd[0] - angledBaseStart[0], angledBaseEnd[1] - angledBaseStart[1]);
      drawStart = null;
      try { svg.releasePointerCapture(event.pointerId); } catch (_) {}
      if (length < 3) {
        setMode("move");
        return;
      }
      angledStage = 1;
      draftObstacle = { shape: "angledRectangle", points: angledRectanglePoints(angledBaseStart, angledBaseEnd, angledBaseEnd) };
      if (angledRectangleHelp) angledRectangleHelp.textContent = "Move the pointer to set the width, then click once.";
      render();
      return;
    }
    if (!drawStart || !draftObstacle) return;

    const end = svgPointFromEvent(event);
    const dx = end[0] - drawStart[0];
    const dy = end[1] - drawStart[1];
    const size = Math.sqrt(dx * dx + dy * dy);
    if (size >= 3) {
      pushHistory();
      obstacles.push(makeObstacle(mode, drawStart, end));
      selectedObstacleIndex = obstacles.length - 1;
      markDirty();
    }
    drawStart = null;
    draftObstacle = null;
    angledBaseStart = null;
    angledBaseEnd = null;
    angledStage = 0;
    setMode("move");
  });

  selectModeButton.addEventListener("click", () => setMode("move"));
  addModeButton.addEventListener("click", () => setMode("add"));
  drawCircleButton.addEventListener("click", () => setMode("circle"));
  drawSquareButton.addEventListener("click", () => setMode("square"));
  drawRectangleButton.addEventListener("click", () => setMode("rectangle"));
  drawAngledRectangleButton.addEventListener("click", () => setMode("angledRectangle"));

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

  deleteObstacleButton.addEventListener("click", () => {
    if (selectedObstacleIndex === null) return;
    pushHistory();
    obstacles.splice(selectedObstacleIndex, 1);
    selectedObstacleIndex = null;
    markDirty();
    render();
  });

  undoButton.addEventListener("click", () => {
    if (!history.length) return;
    const previous = history.pop();
    points = previous.points;
    obstacles = previous.obstacles;
    selectedIndex = null;
    selectedObstacleIndex = null;
    markDirty();
    render();
  });

  showDetected.addEventListener("change", render);

  resetButton.addEventListener("click", async () => {
    if (!confirm("Reset the field edge to the original scanned boundary? Avoid areas will be kept.")) return;
    const response = await fetch(cfg.resetUrl, { method: "POST" });
    const data = await response.json();
    points = clonePoints(data.points);
    history = [];
    selectedIndex = null;
    dirty = false;
    saveStatus.textContent = "Scanned boundary";
    routePreviewPanel.hidden = true;
    render();
  });

  saveButton.addEventListener("click", async () => {
    saveButton.disabled = true;
    try {
      await saveBoundary();
      if (cfg.reviewUrl) {
        dirty = false;
        window.location.href = cfg.reviewUrl;
        return;
      }
    } catch (error) {
      console.error(error);
      alert(`Could not save field:\n${error.message}`);
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
    routeResult.textContent = "Saving field and calculating route…";
    routePreviewPanel.hidden = true;
    try {
      await saveBoundary();
      const response = await fetch(cfg.routeUrl, { method: "POST" });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Route calculation failed.");
      routeResult.className = "route-result success";
      routeResult.textContent =
        `${data.route_mode}: ${data.passes} passes, ${data.turns} turns, ` +
        `about ${data.estimated_time_min} min`;
      routeSummary.textContent =
        `${data.route_mode} · ${data.passes} passes · ${data.estimated_time_min} min`;
      routePreviewImage.src = `${cfg.routeImageUrl}?t=${Date.now()}`;
      routePreviewPanel.hidden = false;
    } catch (error) {
      console.error(error);
      routeResult.className = "route-result error";
      routeResult.textContent = error.message;
      alert(`Could not calculate route:\n${error.message}`);
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
