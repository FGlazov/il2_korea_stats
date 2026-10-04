/* il2ks-template: static/il2ks/il2ks.js v1 - copy this line along when you override */
/* Small progressive enhancements. Everything works without this file (and without htmx).
 * Loaded with `defer`, after vendor/htmx.min.js. */
(function () {
  "use strict";
  var root = document.documentElement;

  // htmx present: hide the "Apply" buttons of filter bars, the filters submit on change instead.
  if (window.htmx) { root.classList.add("has-htmx"); }

  // Theme toggle: flips between light and dark, starting from whatever is showing now; remembered in localStorage.
  function isDark() {
    var explicit = root.getAttribute("data-theme");
    if (explicit) { return explicit === "dark"; }
    return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  }
  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!(target instanceof Element)) { return; }

    var toggle = target.closest("[data-theme-toggle]");
    if (toggle) {
      var next = isDark() ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { window.localStorage.setItem("il2ks-theme", next); } catch (e) { /* storage unavailable */ }
      return;
    }

    // Pico's <details class="dropdown"> stays open until toggled: close any menu when clicking elsewhere.
    var inside = target.closest("details.dropdown");
    document.querySelectorAll("details.dropdown[open]").forEach(function (menu) {
      if (menu !== inside) { menu.removeAttribute("open"); }
    });
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      document.querySelectorAll("details.dropdown[open]").forEach(function (menu) { menu.removeAttribute("open"); });
    }
  });

  // A table wrapper that scrolls sideways (a phone, a wide table) must be reachable by keyboard, or its hidden columns
  // are not (WCAG 2.1.1, axe scrollable-region-focusable): give those, and only those, a tab stop.
  function markScrollable() {
    document.querySelectorAll(".table-wrap").forEach(function (box) {
      if (box.scrollWidth > box.clientWidth + 1 || box.scrollHeight > box.clientHeight + 1) {
        if (!box.hasAttribute("tabindex")) { box.setAttribute("tabindex", "0"); }
      } else if (box.getAttribute("tabindex") === "0") {
        box.removeAttribute("tabindex");
      }
    });
  }
  markScrollable();
  window.addEventListener("load", markScrollable);
  window.addEventListener("resize", markScrollable);
  document.addEventListener("htmx:afterSettle", markScrollable);
  document.addEventListener("toggle", markScrollable, true);
})();
