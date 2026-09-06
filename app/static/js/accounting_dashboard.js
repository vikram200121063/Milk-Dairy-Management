(function () {
    "use strict";

    var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (window.dashCountUp) {
        window.dashCountUp();
    }

    var dataEl = document.getElementById("acct-data");
    var data = dataEl ? JSON.parse(dataEl.textContent) : null;

    function fmtRupee(v) {
        var n = Math.round(Math.abs(Number(v)));
        return (v < 0 ? "-\u20B9" : "\u20B9") + n.toLocaleString("en-IN");
    }

    // ---------------------------------------------------------------
    // 1. Cursor-driven 3D tilt on the stat tiles.
    //    Each tile rotates toward the cursor and its children (already
    //    on separate translateZ planes in CSS) parallax as it moves.
    // ---------------------------------------------------------------
    if (!reduceMotion) {
        document.querySelectorAll(".acct-tile").forEach(function (tile) {
            tile.addEventListener("mousemove", function (e) {
                var r = tile.getBoundingClientRect();
                var px = (e.clientX - r.left) / r.width;
                var py = (e.clientY - r.top) / r.height;
                var rotY = (px - 0.5) * 16;   // left/right
                var rotX = (0.5 - py) * 16;   // up/down
                tile.style.transform =
                    "perspective(700px) rotateX(" + rotX.toFixed(2) + "deg) rotateY(" +
                    rotY.toFixed(2) + "deg) translateZ(10px)";
                tile.style.setProperty("--mx", (px * 100).toFixed(1) + "%");
                tile.style.setProperty("--my", (py * 100).toFixed(1) + "%");
            });
            tile.addEventListener("mouseleave", function () {
                tile.style.transform = "";
            });
        });
    }

    if (!data) {
        return;
    }

    // ---------------------------------------------------------------
    // 2. Profit gauge - arc length = profit margin as a share of revenue.
    // ---------------------------------------------------------------
    var arc = document.getElementById("acct-gauge-arc");
    if (arc) {
        var radius = parseFloat(arc.getAttribute("r"));
        var circumference = 2 * Math.PI * radius;
        var margin = data.margin || 0;                       // -100..100
        var fraction = Math.min(Math.abs(margin), 100) / 100;
        arc.style.strokeDasharray = circumference;
        arc.style.strokeDashoffset = circumference;          // start empty
        // Force layout, then animate to the target so the transition runs.
        void arc.getBoundingClientRect();
        window.setTimeout(function () {
            arc.style.strokeDashoffset = circumference * (1 - fraction);
        }, 80);
    }

    // ---------------------------------------------------------------
    // 3. Money flow: revenue on the left splits into cost, expenses,
    //    and what's left as profit. Band thickness is proportional to
    //    the amount, so the picture reads at a glance.
    // ---------------------------------------------------------------
    var flowEl = document.getElementById("acct-flow");
    if (flowEl) {
        var revenue = data.revenue || 0;
        var milkCost = data.milk_cost || 0;
        var otherExp = data.other_expenses || 0;
        var profit = data.net_profit || 0;

        // Scale against whichever is bigger: money in, or money out. When
        // the dairy runs at a loss the outflows exceed revenue, and this
        // keeps every band inside the frame instead of overflowing.
        var totalOut = milkCost + otherExp + Math.max(profit, 0);
        var scaleBase = Math.max(revenue, totalOut);

        if (scaleBase <= 0) {
            flowEl.innerHTML = '<div class="acct-flow-empty">No activity recorded in this range.</div>';
        } else {
            var W = 640, H = 260, PAD = 8;
            var usable = H - PAD * 2;
            var srcH = (revenue / scaleBase) * usable;
            var srcY = PAD + (usable - srcH) / 2;
            var leftX = 4, leftW = 26, rightX = W - 150, rightW = 26;

            var outs = [
                { label: "Milk Purchase Cost", amount: milkCost, color: "#FB7185", glow: "rgba(251,113,133,0.55)" },
                { label: "Other Expenses", amount: otherExp, color: "#FBBF24", glow: "rgba(251,191,36,0.5)" },
                {
                    label: profit >= 0 ? "Net Profit" : "Net Loss",
                    amount: Math.abs(profit),
                    color: profit >= 0 ? "#34D399" : "#F43F5E",
                    glow: profit >= 0 ? "rgba(52,211,153,0.55)" : "rgba(244,63,94,0.6)",
                },
            ].filter(function (o) { return o.amount > 0; });

            var gap = 14;
            var outTotal = outs.reduce(function (s, o) { return s + o.amount; }, 0);

            var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet">';
            svg += '<defs>';
            outs.forEach(function (o, i) {
                svg += '<linearGradient id="acct-g' + i + '" x1="0" y1="0" x2="1" y2="0">' +
                       '<stop offset="0%" stop-color="#6366F1" stop-opacity="0.55"/>' +
                       '<stop offset="100%" stop-color="' + o.color + '" stop-opacity="0.85"/></linearGradient>';
            });
            svg += '</defs>';

            // Source column (total revenue)
            svg += '<rect x="' + leftX + '" y="' + srcY + '" width="' + leftW + '" height="' + srcH +
                   '" rx="6" fill="#6366F1"/>';
            svg += '<text x="' + (leftX + leftW / 2) + '" y="' + (srcY - 12) +
                   '" class="acct-flow-label" text-anchor="middle">REVENUE IN</text>';
            svg += '<text x="' + (leftX + leftW / 2) + '" y="' + (srcY + srcH + 22) +
                   '" class="acct-flow-amount" text-anchor="middle">' + fmtRupee(revenue) + '</text>';

            // Bands, stacked top-to-bottom on both sides
            var srcCursor = srcY;
            var outStackH = (outTotal / scaleBase) * usable + gap * Math.max(outs.length - 1, 0);
            var dstCursor = PAD + Math.max(usable - outStackH, 0) / 2;

            outs.forEach(function (o, i) {
                var bandH = (o.amount / scaleBase) * usable;
                var s0 = srcCursor, s1 = srcCursor + bandH;
                var d0 = dstCursor, d1 = dstCursor + bandH;
                var x0 = leftX + leftW, x1 = rightX;
                var cx = (x0 + x1) / 2;

                svg += '<path class="acct-flow-band" style="animation-delay:' + (0.12 * i + 0.15) + 's" d="' +
                       'M' + x0 + ',' + s0 +
                       ' C' + cx + ',' + s0 + ' ' + cx + ',' + d0 + ' ' + x1 + ',' + d0 +
                       ' L' + x1 + ',' + d1 +
                       ' C' + cx + ',' + d1 + ' ' + cx + ',' + s1 + ' ' + x0 + ',' + s1 +
                       ' Z" fill="url(#acct-g' + i + ')"/>';

                svg += '<rect class="acct-flow-band" style="animation-delay:' + (0.12 * i + 0.25) + 's" x="' +
                       rightX + '" y="' + d0 + '" width="' + rightW + '" height="' + Math.max(bandH, 2) +
                       '" rx="5" fill="' + o.color + '"/>';

                var midY = d0 + bandH / 2;
                svg += '<text class="acct-flow-band acct-flow-amount" style="animation-delay:' + (0.12 * i + 0.3) + 's" x="' +
                       (rightX + rightW + 12) + '" y="' + (midY + 1) + '">' + fmtRupee(o.amount) + '</text>';
                svg += '<text class="acct-flow-band acct-flow-sub" style="animation-delay:' + (0.12 * i + 0.3) + 's" x="' +
                       (rightX + rightW + 12) + '" y="' + (midY + 15) + '">' + o.label + '</text>';

                srcCursor = s1;
                dstCursor = d1 + gap;
            });

            svg += "</svg>";
            flowEl.innerHTML = svg;
        }
    }

    // ---------------------------------------------------------------
    // 4. Grow the extruded bars and the balance fills from zero.
    // ---------------------------------------------------------------
    window.setTimeout(function () {
        document.querySelectorAll(".acct-bar").forEach(function (bar) {
            bar.style.height = bar.getAttribute("data-height") + "px";
        });
        document.querySelectorAll(".acct-balance-fill").forEach(function (fill) {
            fill.style.width = fill.getAttribute("data-width") + "%";
        });
    }, 60);
})();
