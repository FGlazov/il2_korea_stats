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
    // Under the header; a header near the bottom of the screen gets it above instead, and never past either edge.
    var height = tip.offsetHeight;
    var top = box.bottom;
    if (top + height > window.innerHeight - margin && box.top - height >= margin) { top = box.top - height; }
    tip.style.top = Math.max(margin, Math.min(top, window.innerHeight - height - margin)) + "px";
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

  // A list refresh by htmx (a ticked extra column, a sort, a filter) replaces the whole results region, and a new table
  // starts scrolled to the left: the new columns come in on the right, so a visitor who scrolled over would lose the
  // place. Remember each table's horizontal scroll and the focused extra-columns checkbox before the swap, put them back
  // after it (same page only: another page's table has other columns).
  var saved = null;
  document.addEventListener("htmx:beforeSwap", function (event) {
    var target = event.detail && event.detail.target;
    if (!target || target.id !== "results") { saved = null; return; }
    var active = document.activeElement;
    var info = event.detail.pathInfo, same = true;
    try { same = !info || new URL(info.finalRequestPath, window.location.href).pathname === window.location.pathname; } catch (e) { /* keep */ }
    saved = {
      same: same,
      left: Array.prototype.map.call(target.querySelectorAll(".table-wrap"), function (box) { return box.scrollLeft; }),
      focus: active && active.id && active.closest("#columns-picker") ? active.id : ""
    };
  });
  document.addEventListener("htmx:afterSwap", function (event) {
    var state = saved;
    saved = null;
    var region = document.getElementById("results");
    if (!state || !region) { return; }
    if (state.same) {
      region.querySelectorAll(".table-wrap").forEach(function (box, index) {
        if (state.left[index]) { box.scrollLeft = state.left[index]; }
      });
    }
    if (state.focus) {
      var again = document.getElementById(state.focus);
      if (again && document.activeElement !== again) { again.focus({ preventScroll: true }); }
    }
  });

  // "Extra columns" count (chosen/available, e.g. 5/21): the server renders it from the URL, this keeps it right while boxes
  // are ticked and after a swap (the details element is hx-preserve'd, so its checkboxes and count live on).
  function updateColumnCount() {
    var picker = document.getElementById("columns-picker");
    if (!picker) { return; }
    var total = picker.querySelectorAll("input[type=checkbox]").length;
    var chosen = picker.querySelectorAll("input[type=checkbox]:checked").length;
    var badge = picker.querySelector(".columns-picker__count");
    var text = picker.querySelector(".columns-picker__count-text");
    if (badge) { badge.textContent = chosen + "/" + total; }
    if (text && text.dataset.template) {
      text.textContent = text.dataset.template.replace("@chosen@", chosen).replace("@total@", total);
    }
  }
  document.addEventListener("change", function (event) {
    if (event.target instanceof Element && event.target.closest("#columns-picker")) { updateColumnCount(); }
  });
  document.addEventListener("htmx:afterSwap", updateColumnCount);

  // Whole-row links (.stretched-link) cover the row with a pseudo-element, but a table's sticky first column is its own
  // positioned box (the containing block of that pseudo-element) and the overlay only covers that cell: a click elsewhere
  // on the row has to follow the link by script. A pure-CSS overlay over the whole row is not possible next to a sticky
  // cell (an overlay wider than the cell would stretch the table's scroll width). Plain click: the link's own click;
  // ctrl/meta/shift-click and middle-click (an `auxclick`): a new tab, opened inside the trusted event. Not on another
  // link or control in the row, not while text is selected.
  function rowLink(event) {
    var target = event.target;
    if (!(target instanceof Element) || event.defaultPrevented) { return null; }
    var row = target.closest(".data-table tbody tr");
    if (!row || target.closest("a, button, input, select, textarea, label, summary")) { return null; }
    return row.querySelector("a.stretched-link");
  }
  document.addEventListener("mousedown", function (event) {
    if (event.button === 1 && rowLink(event)) { event.preventDefault(); } // no autoscroll cursor on a middle-click
  });
  document.addEventListener("auxclick", function (event) {
    var link = event.button === 1 ? rowLink(event) : null;
    if (!link) { return; }
    event.preventDefault();
    window.open(link.href, "_blank", "noopener");
  });
  document.addEventListener("click", function (event) {
    if (event.button !== 0 || event.altKey) { return; }
    var link = rowLink(event);
    if (!link) { return; }
    var selection = window.getSelection && window.getSelection();
    if (selection && !selection.isCollapsed) { return; }
    if (event.ctrlKey || event.metaKey || event.shiftKey) {
      event.preventDefault();
      window.open(link.href, "_blank", "noopener");
    } else {
      link.click();
    }
  });
})();
