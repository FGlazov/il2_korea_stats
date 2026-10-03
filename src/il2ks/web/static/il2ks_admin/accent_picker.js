// Adds a colour picker next to the "#RRGGBB" text box of the site settings form. The text box stays the source of truth.
document.addEventListener("DOMContentLoaded", function () {
  var text = document.getElementById("id_accent_color");
  if (!text) { return; }
  var picker = document.createElement("input");
  picker.type = "color";
  picker.value = /^#[0-9a-f]{6}$/i.test(text.value) ? text.value : "#1a73e8";
  picker.style.marginLeft = "0.5em";
  picker.style.verticalAlign = "middle";
  picker.addEventListener("input", function () { text.value = picker.value.toUpperCase(); });
  text.addEventListener("input", function () {
    if (/^#[0-9a-f]{6}$/i.test(text.value)) { picker.value = text.value; }
  });
  text.parentNode.insertBefore(picker, text.nextSibling);
});
