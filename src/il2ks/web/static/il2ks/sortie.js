/* il2ks-template: static/il2ks/sortie.js v1.0 - copy this line along when you override */
/* Sortie page: the damage section is a collapsed <details id="damage">; open it when the page is opened with, or
   navigated to, the #damage anchor (a link to it must show the table). No JS: the section stays collapsed but works. */
(function () {
  function openDamage() {
    if (window.location.hash !== "#damage") return;
    var section = document.getElementById("damage");
    if (section && section.tagName === "DETAILS") {
      section.open = true;
      section.scrollIntoView();
    }
  }
  openDamage();
  window.addEventListener("hashchange", openDamage);
})();
