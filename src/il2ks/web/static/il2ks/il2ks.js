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
      // Column descriptions: Escape hides the open one until the pointer or focus comes back.
      document.querySelectorAll("th.has-hint").forEach(function (th) {
        th.removeAttribute("data-hint-open");
        th.classList.remove("is-open");
        if (th.matches(":hover, :focus-within")) { th.setAttribute("data-hint-dismissed", ""); }
      });
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

  // Column descriptions (components/col_th.html): the tooltip is position: fixed (a scrolling .table-wrap would clip an
  // absolute one); place it under its header, inside the viewport. CSS shows it on hover and focus; a tap or click on
  // the marker (the only way on a touch screen, a tap on a sort link sorts) toggles .is-open.
  function placeHint(th) {
    var tip = th.querySelector(".col-tip");
    if (!tip) { return; }
    th.removeAttribute("data-hint-dismissed");
    var box = th.getBoundingClientRect();
    var margin = 8;
    var width = tip.offsetWidth || Math.min(320, window.innerWidth - 2 * margin);
    var left = Math.max(margin, Math.min(box.left, window.innerWidth - width - margin));
    tip.style.left = left + "px";
    tip.style.top = Math.max(margin, box.bottom) + "px";
  }
  function showHint(event) {
    var target = event.target;
    var th = target instanceof Element ? target.closest("th.has-hint") : null;
    if (th) {
      th.setAttribute("data-hint-open", "");  // measure with the tooltip displayed, then place it
      placeHint(th);
      th.removeAttribute("data-hint-open");
    }
  }
  function hideHint(event) {
    var target = event.target;
    var th = target instanceof Element ? target.closest("th.has-hint") : null;
    if (th && !th.matches(":hover, :focus-within")) { th.removeAttribute("data-hint-dismissed"); }
  }
  document.addEventListener("pointerover", showHint);
  document.addEventListener("focusin", showHint);
  document.addEventListener("pointerout", hideHint);
  document.addEventListener("focusout", hideHint);
  document.addEventListener("click", function (event) {
    var target = event.target;
    var marker = target instanceof Element ? target.closest(".col-hint__marker") : null;
    var open = marker ? marker.closest("th.has-hint") : null;
    document.querySelectorAll("th.has-hint.is-open").forEach(function (th) { if (th !== open) { th.classList.remove("is-open"); } });
    if (open) {
      var show = !open.classList.contains("is-open");
      open.classList.toggle("is-open", show);
      if (show) { open.removeAttribute("data-hint-dismissed"); placeHint(open); }
    }
  });
  // A scroll or resize moves the header away from its fixed tooltip: close it.
  function closeHints() {
    document.querySelectorAll("th.has-hint.is-open").forEach(function (th) { th.classList.remove("is-open"); });
  }
  window.addEventListener("resize", closeHints);
  document.addEventListener("scroll", closeHints, true);
})();
