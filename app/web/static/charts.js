/* Sands-sorter charts. Pulls JSON from /reports/* and renders into <canvas> elements. */
(function () {
  const fmtMoney = (cents) => "$" + (cents / 100).toFixed(2);

  async function loadJSON(url) {
    const response = await fetch(url, { credentials: "same-origin" });
    if (!response.ok) throw new Error("Failed to load " + url + ": " + response.status);
    return response.json();
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
            backgroundColor: "rgba(21,128,61,0.7)",
          },
          {
            label: "Expense",
            data: data.expense_cents.map((c) => c / 100),
            backgroundColor: "rgba(100,116,139,0.7)",
          },
          {
            label: "Net",
            data: data.net_cents.map((c) => c / 100),
            type: "line",
            borderColor: "#1d4ed8",
            backgroundColor: "transparent",
            tension: 0.2,
          },
        ],
      },
      options: { responsive: true, plugins: { legend: { position: "bottom" } } },
    });
  }

  async function renderCategory(canvas, scopeId, fy) {
    const url = `/reports/${scopeId}/by_category.json` + (fy ? `?fy=${fy}` : "");
    const data = await loadJSON(url);
    new Chart(canvas, {
      type: "doughnut",
      data: {
        labels: data.labels,
        datasets: [{ data: data.totals_cents.map((c) => c / 100) }],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { position: "right" },
          tooltip: { callbacks: { label: (ctx) => ctx.label + ": " + fmtMoney(ctx.parsed * 100) } },
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
        datasets: [
          {
            label: "Spend",
            data: data.totals_cents.map((c) => c / 100),
            backgroundColor: "rgba(100,116,139,0.7)",
          },
        ],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        plugins: { legend: { display: false } },
      },
    });
  }

  async function renderVendorTrend(canvas, scopeId, fy) {
    const url = `/reports/${scopeId}/vendor_trend.json` + (fy ? `?fy=${fy}` : "");
    const data = await loadJSON(url);
    const palette = ["#1d4ed8", "#15803d", "#b91c1c", "#a16207", "#7c3aed"];
    new Chart(canvas, {
      type: "line",
      data: {
        labels: data.labels,
        datasets: data.datasets.map((s, i) => ({
          label: s.name,
          data: s.totals_cents.map((c) => c / 100),
          borderColor: palette[i % palette.length],
          backgroundColor: "transparent",
          tension: 0.2,
        })),
      },
      options: { responsive: true, plugins: { legend: { position: "bottom" } } },
    });
  }

  async function renderOverview(canvas, fy) {
    const url = `/reports/overview.json` + (fy ? `?fy=${fy}` : "");
    const data = await loadJSON(url);
    const palette = ["#1d4ed8", "#15803d", "#b91c1c", "#a16207", "#7c3aed", "#0891b2", "#db2777"];
    new Chart(canvas, {
      type: "line",
      data: {
        labels: data.labels,
        datasets: data.datasets.map((s, i) => ({
          label: s.scope,
          data: s.net_cents.map((c) => c / 100),
          borderColor: palette[i % palette.length],
          backgroundColor: "transparent",
          tension: 0.2,
        })),
      },
      options: { responsive: true, plugins: { legend: { position: "bottom" } } },
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
