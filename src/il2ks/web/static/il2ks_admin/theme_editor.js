/* il2ks-template: static/il2ks_admin/theme_editor.js v1 - copy this line along when you override */
// Adds a colour picker and a "reset to default" button next to every colour box of the site settings form. The text
// boxes stay the source of truth (they are what gets submitted), so the form also works without this script.
document.addEventListener("DOMContentLoaded", function () {
  var HEX = /^#[0-9a-f]{6}$/i;
  var root = document.querySelector("[data-theme-editor]");
  if (!root) { return; }
  var resetTitle = root.getAttribute("data-reset-title") || "Reset";
  root.querySelectorAll("input.il2-theme__input").forEach(function (text) {
    var fallback = text.getAttribute("data-default") || "";
    var picker = document.createElement("input");
    picker.type = "color";
    picker.className = "il2-theme__picker";
    picker.tabIndex = -1;
    picker.setAttribute("aria-hidden", "true");
    function show() {
      picker.value = HEX.test(text.value) ? text.value : (HEX.test(fallback) ? fallback : "#808080");
      picker.classList.toggle("is-default", !HEX.test(text.value));
    }
    picker.addEventListener("input", function () {
      text.value = picker.value.toUpperCase();
      show();
    });
    text.addEventListener("input", show);
    var reset = document.createElement("button");
    reset.type = "button";
    reset.className = "il2-theme__reset";
    reset.textContent = "×";
    reset.title = resetTitle;
    reset.setAttribute("aria-label", resetTitle);
    reset.addEventListener("click", function () { text.value = ""; show(); });
    text.parentNode.appendChild(picker);
    text.parentNode.appendChild(reset);
    show();
  });
});
