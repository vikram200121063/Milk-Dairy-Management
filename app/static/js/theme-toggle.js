(function () {
    "use strict";

    var STORAGE_KEY = "om-dairy-theme";

    function getPreferred() {
        var stored = localStorage.getItem(STORAGE_KEY);
        if (stored === "dark" || stored === "light") return stored;
        // Respect the OS/browser preference as the default.
        if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) return "dark";
        return "light";
    }

    function apply(theme) {
        document.documentElement.setAttribute("data-theme", theme);
        localStorage.setItem(STORAGE_KEY, theme);
        // Update every toggle button's icon on the page (there may be one in
        // the desktop nav and one in the mobile-collapsed nav).
        document.querySelectorAll(".theme-toggle-icon").forEach(function (el) {
            el.innerHTML = theme === "dark" ? SUN_ICON : MOON_ICON;
        });
    }

    // Simple inline SVG icons so we don't need an icon library.
    var MOON_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 0 1 12.21 3a7 7 0 1 0 8.79 9.79z"/></svg>';
    var SUN_ICON  = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>';

    // Apply immediately (before paint) so there's no flash of the wrong theme.
    apply(getPreferred());

    // Wire up the toggle button(s) once the DOM is ready.
    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll(".theme-toggle-btn").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var current = document.documentElement.getAttribute("data-theme") || "light";
                apply(current === "dark" ? "light" : "dark");
            });
        });
    });
})();
