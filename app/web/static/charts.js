/* Sands-sorter charts. Pulls JSON from /reports/* and renders into <canvas> elements.
 * Global Chart.js theme is set in base.html (Inter font, slate gridlines,
 * dark tooltip, etc). This file just defines per-chart shapes + palette. */
(function () {
  const fmtMoney = (cents) => "$" + (cents / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  // Refined palette — premium-feeling but accessible.
  const C = {
    income:    "rgba(16, 185, 129, 0.82)",   // emerald-500
    incomeBg:  "rgba(16, 185, 129, 0.16)",
    expense:   "rgba(100, 116, 139, 0.78)",  // slate-500
    expenseBg: "rgba(100, 116, 139, 0.12)",
    net:       "#4f46e5",                     // brand-600
    grid:      "rgba(226, 232, 240, 0.7)",   // slate-200
  };
  const SERIES = ["#6366f1", "#10b981", "#f97316", "#a855f7", "#0ea5e9", "#ef4444", "#14b8a6"];

  async function loadJSON(url) {
    const response = await fetch(url, { credentials: "same-origin" });
    if (!response.ok) throw new Error("Failed to load " + url + ": " + response.status);
    return response.json();
  }

  function moneyAxis() {
    return {
      ticks: {
        callback: (v) => "$" + Number(v).toLocaleString(),
      },
      grid: { color: C.grid, drawBorder: false },
      border: { display: false },
    };
  }

  function categoryAxis() {
    return {
      grid: { display: false },
      border: { display: false },
    };
  }

  async function renderMonthly(canvas, scopeId, fy) {
    const url = `/reports/${scopeId}/monthly.json` + (fy ? `?fy=${fy}` : "");
    const data = await loadJSON(url);
    new Chart(canvas, {
      type: "bar",
      data: {
        labels: data.labels,
        datasets: [
          {
            label: "Income",
            data: data.income_cents.map((c) => c / 100),
            backgroundColor: C.income,
            borderRadius: 5,
            stack: "amount",
          },
          {
            label: "Expense",
            data: data.expense_cents.map((c) => c / 100),
            backgroundColor: C.expense,
            borderRadius: 5,
            stack: "amount",
          },
          {
            label: "Net",
            data: data.net_cents.map((c) => c / 100),
            type: "line",
            borderColor: C.net,
            backgroundColor: "transparent",
            tension: 0.3,
            borderWidth: 2,
            pointRadius: 3,
            pointBackgroundColor: C.net,
            pointHoverRadius: 5,
          },
        ],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { position: "bottom" },
          tooltip: {
            callbacks: { label: (ctx) => `${ctx.dataset.label}: ${fmtMoney(ctx.parsed.y * 100)}` },
          },
          title: { display: true, text: "Monthly cashflow", padding: { bottom: 12 }, font: { size: 13, weight: '600' }, color: "#0f172a" },
        },
        scales: { y: moneyAxis(), x: categoryAxis() },
      },
    });
  }

  async function renderCategory(canvas, scopeId, fy) {
    const url = `/reports/${scopeId}/by_category.json` + (fy ? `?fy=${fy}` : "");
    const data = await loadJSON(url);
    new Chart(canvas, {
      type: "doughnut",
      data: {
        labels: data.labels,
        datasets: [{
          data: data.totals_cents.map((c) => c / 100),
          backgroundColor: data.labels.map((_, i) => SERIES[i % SERIES.length]),
          borderColor: "white",
          borderWidth: 2,
          spacing: 2,
        }],
      },
      options: {
        cutout: "62%",
        responsive: true,
        plugins: {
          legend: { position: "right" },
          tooltip: { callbacks: { label: (ctx) => ctx.label + ": " + fmtMoney(ctx.parsed * 100) } },
          title: { display: true, text: "Spend by category", padding: { bottom: 12 }, font: { size: 13, weight: '600' }, color: "#0f172a" },
        },
      },
    });
  }

  async function renderTopVendors(canvas, scopeId, fy) {
    const url = `/reports/${scopeId}/top_vendors.json` + (fy ? `?fy=${fy}` : "");
    const data = await loadJSON(url);
    new Chart(canvas, {
      type: "bar",
      data: {
        labels: data.labels,
        datasets: [{
          label: "Spend",
          data: data.totals_cents.map((c) => c / 100),
          backgroundColor: data.labels.map((_, i) => SERIES[i % SERIES.length] + "cc"),
          borderRadius: 5,
        }],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => fmtMoney(ctx.parsed.x * 100) } },
          title: { display: true, text: "Top vendors", padding: { bottom: 12 }, font: { size: 13, weight: '600' }, color: "#0f172a" },
        },
        scales: { x: moneyAxis(), y: categoryAxis() },
      },
    });
  }

  async function renderVendorTrend(canvas, scopeId, fy) {
    const url = `/reports/${scopeId}/vendor_trend.json` + (fy ? `?fy=${fy}` : "");
    const data = await loadJSON(url);
    new Chart(canvas, {
      type: "line",
      data: {
        labels: data.labels,
        datasets: data.datasets.map((s, i) => ({
          label: s.name,
          data: s.totals_cents.map((c) => c / 100),
          borderColor: SERIES[i % SERIES.length],
          backgroundColor: SERIES[i % SERIES.length] + "26",
          tension: 0.3,
          borderWidth: 2,
          pointRadius: 2.5,
          pointHoverRadius: 5,
          fill: false,
        })),
      },
      options: {
        responsive: true,
        plugins: {
          legend: { position: "bottom" },
          tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${fmtMoney(ctx.parsed.y * 100)}` } },
          title: { display: true, text: "Per-vendor monthly trend", padding: { bottom: 12 }, font: { size: 13, weight: '600' }, color: "#0f172a" },
        },
        scales: { y: moneyAxis(), x: categoryAxis() },
        interaction: { intersect: false, mode: "index" },
      },
    });
  }

  async function renderOverview(canvas, fy) {
    const url = `/reports/overview.json` + (fy ? `?fy=${fy}` : "");
    const data = await loadJSON(url);
    new Chart(canvas, {
      type: "line",
      data: {
        labels: data.labels,
        datasets: data.datasets.map((s, i) => ({
          label: s.scope,
          data: s.net_cents.map((c) => c / 100),
          borderColor: SERIES[i % SERIES.length],
          backgroundColor: "transparent",
          tension: 0.3,
          borderWidth: 2,
          pointRadius: 2.5,
          pointHoverRadius: 5,
        })),
      },
      options: {
        responsive: true,
        plugins: {
          legend: { position: "bottom" },
          tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${fmtMoney(ctx.parsed.y * 100)}` } },
        },
        scales: { y: moneyAxis(), x: categoryAxis() },
        interaction: { intersect: false, mode: "index" },
      },
    });
  }

  window.SandsCharts = {
    renderMonthly,
    renderCategory,
    renderTopVendors,
    renderVendorTrend,
    renderOverview,
  };
})();
