/* il2ks-template: static/il2ks/theme-init.js v1.0 - copy this line along when you override */
/* Runs in <head> before first paint: marks the page as JS-enabled and applies a remembered theme (no flash).
 * Storage can be blocked (private windows, site data off), so every access is inside try/catch.
 * ?theme=dark|light forces a theme for this view only (handy for screenshots and for sharing a look). */
(function () {
  var root = document.documentElement;
  root.classList.add("js");
  try {
    var choice = null;
    try { choice = window.localStorage.getItem("il2ks-theme"); } catch (e) { /* storage unavailable */ }
    var forced = new URLSearchParams(window.location.search).get("theme");
    if (forced === "dark" || forced === "light") { choice = forced; }
    if (choice === "dark" || choice === "light") { root.setAttribute("data-theme", choice); }
  } catch (e) { /* keep the OS default */ }
})();
