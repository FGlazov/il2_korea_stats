/* il2ks-template: static/il2ks/localtime.js v1 - copy this line along when you override */
/* Shows real-world times in the viewer's own time zone and the viewer's own date/time conventions (FR-WEB-17, TD-15).
 *
 * The server renders <time datetime="2026-09-19T22:34:00Z" data-il2-time="datetime">2026-09-19 22:34 UTC</time>
 * (kinds: datetime, date, time, clock), identical for every viewer. This script rewrites the text with
 * Intl.DateTimeFormat in the page language (<html lang>, else the browser's locale) and the browser's time zone: "Sep 19, 2026,
 * 10:34 PM" for English, "19.09.2026, 22:34" for German. The zone is not repeated next to the times: the footer names it once
 * (data-il2-tz-note). The tooltip carries the UTC time; the element gets data-il2-local. It also runs again on content htmx
 * swaps in, and hides elements marked data-il2-until="<ISO time>" once that moment has passed (the "next tour starts" line,
 * which the cached page cannot hide itself). Without this script the page keeps its UTC text. Game-world dates and clocks
 * and durations are never <time> elements and are not touched. */
(function () {
  "use strict";
  var lang = document.documentElement.lang || undefined;
  var zone;
  try { zone = new Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) { return; }
  if (!zone) { return; }

  // What each kind of <time> shows, as Intl styles (so every locale picks its own order, separators and 12/24 h clock).
  var styles = {
    datetime: { dateStyle: "medium", timeStyle: "short" },
    date: { dateStyle: "medium" },
    time: { timeStyle: "short" },
    clock: { timeStyle: "medium" }
  };

  var formatters = {};
  function formatter(kind, timeZone) {
    var key = kind + "|" + timeZone;
    if (!formatters[key]) {
      var options = Object.assign({ timeZone: timeZone }, styles[kind] || styles.datetime);
      try { formatters[key] = new Intl.DateTimeFormat(lang, options); }
      catch (e) { formatters[key] = new Intl.DateTimeFormat(undefined, options); }
    }
    return formatters[key];
  }

  function convert(scope) {
    var root = scope && scope.querySelectorAll ? scope : document;
    var nodes = root.querySelectorAll("time[data-il2-time]:not([data-il2-local])");
    nodes.forEach(function (node) {
      var date = new Date(node.getAttribute("datetime") || "");
      if (isNaN(date.getTime())) { return; }
      var kind = node.getAttribute("data-il2-time") || "datetime";
      var long = kind === "clock" || kind === "time" ? "clock" : "datetime";
      node.textContent = formatter(kind, zone).format(date);
      node.title = formatter(long, "UTC").format(date) + " UTC";
      node.setAttribute("data-il2-local", zone);
    });
    root.querySelectorAll("[data-il2-until]").forEach(function (node) {
      var until = new Date(node.getAttribute("data-il2-until") || "");
      if (!isNaN(until.getTime()) && until.getTime() <= Date.now()) { node.hidden = true; }
    });
    note();
  }

  // The footer note says UTC without this script and names the zone with it.
  function note() {
    document.querySelectorAll("[data-il2-tz-note]").forEach(function (el) {
      var template = el.getAttribute("data-local-text");
      if (template) { el.textContent = template.replace("__TZ__", zone); }
    });
  }

  convert(document);
  document.addEventListener("htmx:load", function (event) { convert(event.target instanceof Element ? event.target : document); });
  document.addEventListener("htmx:afterSwap", function (event) { convert(event.target instanceof Element ? event.target : document); });
})();
