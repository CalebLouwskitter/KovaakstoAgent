/**
 * Kovaak Agent - Pure Vanilla SVG Chart Library (Zero Build Dependencies)
 *
 * Theme-aware interactive visualizations for:
 * 1. Skill Category Radar (normalized skill index & consistency overlay; horizontal bars when < 3 categories)
 * 2. Scenario Trend Chart (raw runs, 5-run rolling median, PB markers, hardware-change annotations)
 * 3. Within-Session Progression Sparkline (Early -> Mid -> Late normalized index)
 */

(() => {
  const SVG_NS = "http://www.w3.org/2000/svg";

  function svgEl(tag, attrs = {}, text = null) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v !== null && v !== undefined) node.setAttribute(k, String(v));
    }
    if (text !== null) node.textContent = String(text);
    return node;
  }

  function fmt(val, digits = 1) {
    if (val === null || val === undefined || Number.isNaN(Number(val))) return "—";
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(Number(val));
  }

  function renderRadarChart(container, categories) {
    container.replaceChildren();
    const played = (categories || []).filter(
      (c) => c.runs > 0 && c.skill_index !== null && c.skill_index !== undefined && c.id !== "benchmark"
    );

    if (!played.length) {
      container.innerHTML = '<p class="hint empty-chart">Play or import scenarios across skill categories to unlock your skill profile chart.</p>';
      return;
    }

    if (played.length < 3) {
      const wrap = document.createElement("div");
      wrap.className = "fallback-bars";
      const note = document.createElement("p");
      note.className = "hint";
      note.textContent = "Showing normalized category comparison (play 3+ distinct skill categories for full radar view).";
      wrap.append(note);
      for (const c of played) {
        const row = document.createElement("div");
        row.className = "fallback-bar-row";
        const head = document.createElement("div");
        head.className = "fallback-bar-head";
        head.innerHTML = `<strong>${escapeHtml(c.label)}</strong><span>Index ${fmt(c.skill_index)} / 100 · ${c.runs} runs</span>`;
        const track = document.createElement("div");
        track.className = "conversion-track";
        const fill = document.createElement("span");
        fill.style.width = `${Math.max(4, Math.min(100, c.skill_index))}%`;
        track.append(fill);
        row.append(head, track);
        wrap.append(row);
      }
      container.append(wrap);
      return;
    }

    const size = 340;
    const cx = size / 2;
    const cy = size / 2;
    const radius = 108;
    const count = played.length;

    const wrap = document.createElement("div");
    wrap.className = "chart-wrap";

    const readout = document.createElement("div");
    readout.className = "chart-readout hint";
    readout.textContent = "Hover any category node to inspect normalized index, consistency, and runs.";

    const svg = svgEl("svg", {
      viewBox: `0 0 ${size} ${size}`,
      class: "radar-svg",
      role: "img",
      "aria-label": "Skill category radar chart",
    });

    const angleFor = (i) => (-Math.PI / 2) + (i * 2 * Math.PI) / count;
    const pointFor = (i, pct) => {
      const r = (Math.max(0, Math.min(100, pct)) / 100) * radius;
      const a = angleFor(i);
      return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
    };

    // Grid rings
    for (const ring of [25, 50, 75, 100]) {
      const pts = played.map((_, i) => pointFor(i, ring).map((n) => n.toFixed(1)).join(",")).join(" ");
      svg.append(svgEl("polygon", { points: pts, class: "radar-grid" }));
      svg.append(svgEl("text", { x: cx + 4, y: cy - (ring / 100) * radius + 3, class: "radar-ring-label" }, ring));
    }

    // Axes & labels
    played.forEach((cat, i) => {
      const [ax, ay] = pointFor(i, 100);
      svg.append(svgEl("line", { x1: cx, y1: cy, x2: ax.toFixed(1), y2: ay.toFixed(1), class: "radar-axis" }));
      const a = angleFor(i);
      const lx = cx + (radius + 24) * Math.cos(a);
      const ly = cy + (radius + 24) * Math.sin(a);
      const anchor = Math.abs(Math.cos(a)) < 0.2 ? "middle" : Math.cos(a) > 0 ? "start" : "end";
      svg.append(svgEl("text", {
        x: lx.toFixed(1),
        y: (ly + 4).toFixed(1),
        "text-anchor": anchor,
        class: "radar-label",
      }, cat.short_label || cat.label));
    });

    // Consistency overlay polygon
    const consPts = played
      .map((cat, i) => pointFor(i, cat.consistency_score ?? 0).map((n) => n.toFixed(1)).join(","))
      .join(" ");
    svg.append(svgEl("polygon", { points: consPts, class: "radar-consistency" }));

    // Primary Skill Index polygon
    const skillPts = played
      .map((cat, i) => pointFor(i, cat.skill_index ?? 0).map((n) => n.toFixed(1)).join(","))
      .join(" ");
    svg.append(svgEl("polygon", { points: skillPts, class: "radar-fill" }));

    // Interactive vertex nodes
    played.forEach((cat, i) => {
      const [px, py] = pointFor(i, cat.skill_index ?? 0);
      const node = svgEl("circle", {
        cx: px.toFixed(1),
        cy: py.toFixed(1),
        r: 5,
        class: "radar-node",
        tabindex: 0,
      });
      const summary = `${cat.label}: Skill Index ${fmt(cat.skill_index)}/100 · PB Proximity ${fmt(cat.pb_proximity_pct)}% · Consistency ${fmt(cat.consistency_score)}/100 · ${cat.runs} runs`;
      node.append(svgEl("title", {}, summary));
      node.addEventListener("mouseenter", () => { readout.textContent = summary; });
      node.addEventListener("focus", () => { readout.textContent = summary; });
      svg.append(node);
    });

    const legend = document.createElement("div");
    legend.className = "chart-legend";
    legend.innerHTML = '<span><i class="legend-swatch primary"></i> Normalized Skill Index</span><span><i class="legend-swatch dashed"></i> Consistency Score</span>';

    wrap.append(svg, legend, readout);
    container.append(wrap);
  }

  function renderTrendChart(container, trendData, metric = "score") {
    container.replaceChildren();
    const points = (trendData?.points || []).filter((p) => p[metric] !== null && p[metric] !== undefined);
    if (!points.length) {
      container.innerHTML = `<p class="hint empty-chart">No ${metric === "accuracy" ? "accuracy" : "score"} points recorded in this range.</p>`;
      return;
    }

    const w = 680;
    const h = 250;
    const padL = 54;
    const padR = 20;
    const padT = 22;
    const padB = 34;
    const plotW = w - padL - padR;
    const plotH = h - padT - padB;

    const vals = points.map((p) => Number(p[metric]));
    const minRaw = Math.min(...vals);
    const maxRaw = Math.max(...vals);
    const span = maxRaw - minRaw || Math.max(1, Math.abs(maxRaw) * 0.1);
    const yMin = metric === "accuracy" ? Math.max(0, minRaw - span * 0.15) : minRaw - span * 0.12;
    const yMax = metric === "accuracy" ? Math.min(100, maxRaw + span * 0.15) : maxRaw + span * 0.12;
    const ySpan = yMax - yMin || 1;

    const xFor = (i) => (points.length === 1 ? padL + plotW / 2 : padL + (i / (points.length - 1)) * plotW);
    const yFor = (val) => padT + plotH - ((val - yMin) / ySpan) * plotH;

    const wrap = document.createElement("div");
    wrap.className = "chart-wrap";
    const readout = document.createElement("div");
    readout.className = "chart-readout hint";
    readout.textContent = `Showing ${points.length} run(s) · Hover points for details${trendData.hardware_events?.length ? " · Dashed vertical lines mark setup changes" : ""}.`;

    const svg = svgEl("svg", {
      viewBox: `0 0 ${w} ${h}`,
      class: "trend-svg",
      role: "img",
      "aria-label": `${trendData.scenario || "Scenario"} ${metric} trend chart`,
    });

    // Horizontal gridlines
    for (let i = 0; i <= 4; i++) {
      const frac = i / 4;
      const y = padT + plotH * (1 - frac);
      const val = yMin + ySpan * frac;
      svg.append(svgEl("line", { x1: padL, y1: y.toFixed(1), x2: w - padR, y2: y.toFixed(1), class: "trend-grid" }));
      svg.append(svgEl("text", { x: padL - 8, y: (y + 3).toFixed(1), "text-anchor": "end", class: "trend-axis-label" }, fmt(val, metric === "accuracy" ? 1 : 0) + (metric === "accuracy" ? "%" : "")));
    }

    // Hardware change vertical annotations
    const times = points.map((p) => p.observed_at);
    for (const ev of trendData.hardware_events || []) {
      const evTs = String(ev.observed_at || "").replace("T", " ").slice(0, 19);
      let idx = times.findIndex((t) => t >= evTs);
      if (idx < 0) idx = points.length - 1;
      const x = xFor(idx);
      svg.append(svgEl("line", { x1: x.toFixed(1), y1: padT, x2: x.toFixed(1), y2: h - padB, class: "trend-hw-line" }));
      const tag = svgEl("text", { x: Math.min(w - padR - 4, x + 4), y: padT + 11, class: "trend-hw-label" }, "Setup Δ");
      tag.append(svgEl("title", {}, `${ev.observed_at.slice(0, 10)}: ${ev.summary}`));
      svg.append(tag);
    }

    // Raw line & rolling median line (for score)
    if (points.length > 1) {
      const linePts = points.map((p, i) => `${xFor(i).toFixed(1)},${yFor(p[metric]).toFixed(1)}`).join(" ");
      svg.append(svgEl("polyline", { points: linePts, class: "trend-line" }));

      if (metric === "score") {
        const rollPts = points
          .filter((p) => p.rolling_median !== null && p.rolling_median !== undefined)
          .map((p, _, arr) => {
            const origIdx = points.indexOf(p);
            return `${xFor(origIdx).toFixed(1)},${yFor(p.rolling_median).toFixed(1)}`;
          })
          .join(" ");
        if (rollPts) svg.append(svgEl("polyline", { points: rollPts, class: "trend-rolling" }));
      }
    }

    // Data points + PB diamonds
    points.forEach((p, i) => {
      const x = xFor(i);
      const y = yFor(p[metric]);
      const isPb = metric === "score" && p.is_pb;
      let marker;
      if (isPb) {
        marker = svgEl("polygon", {
          points: `${x.toFixed(1)},${(y - 5.5).toFixed(1)} ${(x + 5.5).toFixed(1)},${y.toFixed(1)} ${x.toFixed(1)},${(y + 5.5).toFixed(1)} ${(x - 5.5).toFixed(1)},${y.toFixed(1)}`,
          class: "trend-pb-dot",
          tabindex: 0,
        });
      } else {
        marker = svgEl("circle", { cx: x.toFixed(1), cy: y.toFixed(1), r: 3.6, class: "trend-dot", tabindex: 0 });
      }
      const info = `${p.observed_at.slice(0, 16)} · Score ${fmt(p.score, 1)}${p.accuracy != null ? ` · Acc ${fmt(p.accuracy, 1)}%` : ""}${isPb ? " · PB!" : ""}${p.sensitivity != null ? ` · ${fmt(p.sensitivity, 1)} sens` : ""}`;
      marker.append(svgEl("title", {}, info));
      marker.addEventListener("mouseenter", () => { readout.textContent = info; });
      marker.addEventListener("focus", () => { readout.textContent = info; });
      svg.append(marker);
    });

    // X-axis labels (first, middle, last)
    const xIndices = points.length === 1 ? [0] : points.length === 2 ? [0, 1] : [0, Math.floor(points.length / 2), points.length - 1];
    for (const idx of xIndices) {
      const anchor = idx === 0 ? "start" : idx === points.length - 1 ? "end" : "middle";
      svg.append(svgEl("text", { x: xFor(idx).toFixed(1), y: h - 10, "text-anchor": anchor, class: "trend-axis-label" }, points[idx].date));
    }

    const legend = document.createElement("div");
    legend.className = "chart-legend";
    legend.innerHTML = metric === "score"
      ? '<span><i class="legend-swatch primary"></i> Run Score</span><span><i class="legend-swatch dashed"></i> 5-Run Rolling Median</span><span><i class="legend-swatch pb"></i> PB Milestone</span>'
      : '<span><i class="legend-swatch primary"></i> Run Accuracy (%)</span>';

    wrap.append(svg, legend, readout);
    container.append(wrap);
  }

  function renderSessionDriftChart(container, progression) {
    container.replaceChildren();
    if (!progression || !progression.sessions_analyzed) {
      container.innerHTML = '<p class="hint">Need at least 1 multi-run session (3+ runs) to measure within-session progression.</p>';
      return;
    }
    const items = [
      { label: "Early (Warmup)", delta: 0, note: "Baseline within session" },
      { label: "Mid (Peak)", delta: progression.avg_warmup_ramp_pct ?? 0, note: "vs early" },
      { label: "Late (Finish)", delta: progression.avg_end_to_end_pct ?? 0, note: "vs early" },
    ];
    const grid = document.createElement("div");
    grid.className = "drift-grid";
    for (const item of items) {
      const card = document.createElement("div");
      card.className = "drift-item";
      const sign = item.delta > 0 ? "+" : "";
      card.innerHTML = `<span>${escapeHtml(item.label)}</span><strong>${sign}${fmt(item.delta, 1)}%</strong><small>${escapeHtml(item.note)}</small>`;
      grid.append(card);
    }
    container.append(grid);
  }

  function escapeHtml(str) {
    return String(str ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  window.KovaakCharts = { renderRadarChart, renderTrendChart, renderSessionDriftChart };
})();
