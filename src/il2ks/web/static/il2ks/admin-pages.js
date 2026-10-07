/* il2ks-template: static/il2ks/admin-pages.js v1 - copy this line along when you override */
// The Preview button under each Markdown box asks the server for the sanitised HTML (the code the public page uses).
document.addEventListener('click', function (event) {
  var button = event.target.closest && event.target.closest('.md-preview-button');
  if (!button) return;
  var area = button.closest('div, td').querySelector('textarea[data-markdown]');
  var box = button.parentNode.nextElementSibling;
  var body = new FormData();
  body.append('source', area.value);
  fetch(button.dataset.url, {
    method: 'POST', body: body, credentials: 'same-origin',
    headers: {'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value},
  })
    .then(function (response) { return response.ok ? response.text() : Promise.reject(new Error('preview')); })
    .then(function (html) { box.innerHTML = html; box.hidden = false; })
    .catch(function () { box.textContent = button.dataset.failed; box.hidden = false; });
});
