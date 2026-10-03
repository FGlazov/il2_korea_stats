/* il2ks-template: static/il2ks/setup.js v1 - copy this line along when you override */
/* First-run setup page: a small convenience; the page works without it.
 * Windows cannot tell il2ks the name of its time zone, but the browser on this very computer can, so if the time zone
 * is only a guess ("data-guessed"), preselect the browser's IANA name (when the list has it). */
(function () {
  "use strict";
  var select = document.querySelector("select[name=timezone][data-guessed='1']");
  if (!select || !window.Intl || !Intl.DateTimeFormat) { return; }
  var zone;
  try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) { return; }
  if (!zone) { return; }
  for (var i = 0; i < select.options.length; i++) {
    if (select.options[i].value === zone) { select.selectedIndex = i; return; }
  }
})();
