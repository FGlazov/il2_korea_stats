/* il2ks-template: static/il2ks/localtime.js v1 - copy this line along when you override */
/* Shows real-world times in the viewer's own time zone (FR-WEB-17, TD-15).
 *
 * The server renders <time datetime="2026-09-19T22:34:00Z" data-il2-time="datetime">2026-09-19 22:34 UTC</time>
 * (kinds: datetime, date, time, clock), identical for every viewer. This script rewrites the text in the browser's
 * time zone as yyyy-mm-dd hh:mm (24 h, latin digits, so it reads like the UTC text), puts the UTC time in the tooltip,
 * and marks the element with data-il2-local. It also runs again on content htmx swaps in. Without this script the
 * page keeps its UTC text. Game-world dates and clocks and durations are never <time> elements and are not touched. */
(function () {
  "use strict";
  var lang = document.documentElement.lang || undefined;
  var zone;
  try { zone = new Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) { return; }
  if (!zone) { return; }

  var formatters = {};
  function parts(date, timeZone) {
    var key = timeZone;
    if (!formatters[key]) {
      var options = {
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", timeZone: timeZone
      };
      try { formatters[key] = new Intl.DateTimeFormat((lang || "en") + "-u-nu-latn", options); }
      catch (e) { formatters[key] = new Intl.DateTimeFormat("en-u-nu-latn", options); }
    }
    var out = {};
    formatters[key].formatToParts(date).forEach(function (p) { out[p.type] = p.value; });
    if (out.hour === "24") { out.hour = "00"; }
    return out;
  }

  function text(date, kind, timeZone) {
    var p = parts(date, timeZone);
    var day = p.year + "-" + p.month + "-" + p.day;
    var hm = p.hour + ":" + p.minute;
    if (kind === "date") { return day; }
    if (kind === "time") { return hm; }
    if (kind === "clock") { return hm + ":" + p.second; }
    return day + " " + hm;
  }

  var converted = false;
  function convert(scope) {
    var nodes = (scope && scope.querySelectorAll ? scope : document).querySelectorAll("time[data-il2-time]:not([data-il2-local])");
    nodes.forEach(function (node) {
      var date = new Date(node.getAttribute("datetime") || "");
      if (isNaN(date.getTime())) { return; }
      var kind = node.getAttribute("data-il2-time") || "datetime";
      var long = kind === "clock" || kind === "time" ? "clock" : "datetime";
      node.textContent = text(date, kind, zone);
      node.title = text(date, long, "UTC") + " UTC";
      node.setAttribute("data-il2-local", zone);
      converted = true;
    });
    if (converted) { note(); }
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
