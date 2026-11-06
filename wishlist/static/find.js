// "Find pictures" on the item form. Enter in the shop-link field searches (the form
// works without JS: there Enter saves). Submitting a search disables the button and marks
// it busy so a double tap cannot start a second search.
(function () {
  function busy(button) {
    // Deferred: a disabled submitter would be left out of the form data.
    setTimeout(function () {
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
    }, 0);
  }

  document.addEventListener("keydown", function (event) {
    var field = event.target;
    if (event.key !== "Enter" || event.isComposing) return;
    if (!field.matches || !field.matches("input[data-enter-find]") || !field.form) return;
    var button = field.form.querySelector("button[name='action'][value='find']");
    if (!button || button.disabled) return;
    event.preventDefault();
    button.click();  // a real click: htmx 2.0 reads the clicked button, not event.submitter
  });

  document.addEventListener("submit", function (event) {
    var button = event.submitter;
    if (button && button.matches && button.matches("button[name='action'][value='find']")) busy(button);
  });

  // Back/forward cache: never come back to a stuck button.
  window.addEventListener("pageshow", function () {
    document.querySelectorAll("button[name='action'][aria-busy]").forEach(function (b) {
      b.disabled = false;
      b.removeAttribute("aria-busy");
    });
  });
})();
