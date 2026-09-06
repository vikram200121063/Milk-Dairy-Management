(function () {
    "use strict";

    window.dashCountUp = function () {
        var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

        function countUp(el) {
            var target = parseFloat(el.getAttribute("data-count-to"));
            var prefix = el.getAttribute("data-prefix") || "";
            var suffix = el.getAttribute("data-suffix") || "";
            var decimals = parseInt(el.getAttribute("data-decimals") || "0", 10);

            if (reduceMotion || !isFinite(target)) {
                el.textContent = prefix + target.toLocaleString("en-IN", {
                    minimumFractionDigits: decimals, maximumFractionDigits: decimals
                }) + suffix;
                return;
            }

            var duration = 900;
            var start = null;

            function tick(ts) {
                if (start === null) start = ts;
                var progress = Math.min((ts - start) / duration, 1);
                var eased = 1 - Math.pow(1 - progress, 3); // ease-out-cubic
                var value = target * eased;
                el.textContent = prefix + value.toLocaleString("en-IN", {
                    minimumFractionDigits: decimals, maximumFractionDigits: decimals
                }) + suffix;
                if (progress < 1) {
                    requestAnimationFrame(tick);
                }
            }
            requestAnimationFrame(tick);
        }

        document.querySelectorAll("[data-count-to]").forEach(countUp);
    };
})();
