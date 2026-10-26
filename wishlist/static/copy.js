// Copy buttons: <button data-copy-target="input-id" data-copied-label="Copied!">.
// navigator.clipboard only exists on HTTPS or localhost, so fall back to
// selecting the input and execCommand("copy") on plain-HTTP homelab setups.
document.addEventListener("click", function (event) {
  var button = event.target.closest("[data-copy-target]");
  if (!button) return;
  var input = document.getElementById(button.dataset.copyTarget);
  if (!input) return;

  function done() {
    var label = button.dataset.originalLabel || button.textContent;
    button.dataset.originalLabel = label;
    button.textContent = button.dataset.copiedLabel || label;
    setTimeout(function () { button.textContent = label; }, 2000);
  }

  function fallback() {
    input.focus();
    input.select();
    input.setSelectionRange(0, input.value.length);
    try {
      if (document.execCommand("copy")) done();
    } catch (e) { /* leave the text selected for manual copy */ }
  }

  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(input.value).then(done, fallback);
  } else {
    fallback();
  }
});
