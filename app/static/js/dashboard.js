(function () {
    "use strict";

    var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    var dataEl = document.getElementById("dash-data");
    var data = dataEl ? JSON.parse(dataEl.textContent) : null;

    // ---- Animated count-up numbers (shared utility, see count-up.js) ----
    if (window.dashCountUp) {
        window.dashCountUp();
    }

    // ---- Charts ----
    if (data && window.Chart) {
        var indigo = "#4F46E5", violet = "#7C3AED", cyan = "#0891B2", cyanLight = "#22D3EE";
        var green = "#059669", rose = "#E11D48";
        var animDuration = reduceMotion ? 0 : 900;

        function fmtRupee(v) {
            return "\u20B9" + Number(v).toLocaleString("en-IN");
        }

        function gradient(ctx, chartArea, colorTop, colorBottom) {
            var g = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
            g.addColorStop(0, colorTop);
            g.addColorStop(1, colorBottom);
            return g;
        }

        var revenueCanvas = document.getElementById("dash-revenue-chart");
        if (revenueCanvas) {
            new Chart(revenueCanvas.getContext("2d"), {
                type: "bar",
                data: {
                    labels: data.trends.labels,
                    datasets: [
                        {
                            label: "Revenue",
                            data: data.trends.revenue,
                            backgroundColor: indigo,
                            borderRadius: 6,
                            maxBarThickness: 22,
                            order: 2,
                        },
                        {
                            label: "Expense",
                            data: data.trends.expense,
                            type: "line",
                            borderColor: rose,
                            backgroundColor: rose,
                            tension: 0.35,
                            pointRadius: 3,
                            pointBackgroundColor: rose,
                            borderWidth: 2,
                            order: 1,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: { duration: animDuration, easing: "easeOutCubic" },
                    interaction: { mode: "index", intersect: false },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: { label: function (c) { return c.dataset.label + ": " + fmtRupee(c.parsed.y); } },
                        },
                    },
                    scales: {
                        y: { beginAtZero: true, grid: { color: "rgba(91,100,133,0.08)" }, ticks: { callback: fmtRupee, font: { size: 10 } } },
                        x: { grid: { display: false }, ticks: { font: { size: 10 } } },
                    },
                },
            });
        }

        var milkCanvas = document.getElementById("dash-milk-chart");
        if (milkCanvas) {
            var ctx = milkCanvas.getContext("2d");
            new Chart(ctx, {
                type: "line",
                data: {
                    labels: data.trends.labels,
                    datasets: [
                        {
                            label: "Milk Collected (L)",
                            data: data.trends.milk_quantity,
                            borderColor: cyan,
                            backgroundColor: function (context) {
                                var chart = context.chart;
                                var chartArea = chart.chartArea;
                                if (!chartArea) return null;
                                return gradient(chart.ctx, chartArea, "rgba(34,211,238,0.45)", "rgba(34,211,238,0.02)");
                            },
                            fill: true,
                            tension: 0.4,
                            pointRadius: 3,
                            pointBackgroundColor: cyan,
                            borderWidth: 2.5,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: { duration: animDuration, easing: "easeOutCubic" },
                    plugins: {
                        legend: { display: false },
                        tooltip: { callbacks: { label: function (c) { return c.parsed.y + " L"; } } },
                    },
                    scales: {
                        y: { beginAtZero: true, grid: { color: "rgba(91,100,133,0.08)" }, ticks: { font: { size: 10 } } },
                        x: { grid: { display: false }, ticks: { font: { size: 10 } } },
                    },
                },
            });
        }
    }
})();
