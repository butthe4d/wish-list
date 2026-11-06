// Item detail dialog: a click on <a data-detail href="..."> fetches that page, takes
// its .item-detail element and shows it in <dialog id="detail">. The link is a real
// page, so without JS (or on any fetch problem) it simply navigates there. Forms in
// the dialog submit normally (full page). Escape, the backdrop and [data-detail-close]
// close it.
(function () {
  var opening = false;

  function dialog() {
    return document.getElementById("detail");
  }

  function close(dlg) {
    if (dlg.open) dlg.close();
  }

  // Capture phase: runs before htmx's boosted-link handler on the anchor itself.
  document.addEventListener("click", function (event) {
    var link = event.target.closest("a[data-detail]");
    var dlg = dialog();
    if (!link || !dlg || typeof dlg.showModal !== "function") return;
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    event.stopPropagation();
    if (opening) return;
    opening = true;
    var href = link.href;

    fetch(href, { credentials: "same-origin", headers: { Accept: "text/html" } })
      .then(function (response) {
        if (!response.ok) throw new Error("status " + response.status);
        return response.text();
      })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var detail = doc.querySelector(".item-detail");
        if (!detail) throw new Error("no detail");
        dlg.replaceChildren(document.importNode(detail, true));
        dlg.showModal();
      })
      .catch(function () {
        window.location.href = href;
      })
      .finally(function () {
        opening = false;
      });
  }, true);

  document.addEventListener("click", function (event) {
    var dlg = dialog();
    if (!dlg || !dlg.open) return;
    if (event.target === dlg) {  // click on the backdrop (outside the content)
      close(dlg);
      return;
    }
    var closer = event.target.closest("[data-detail-close]");
    if (closer && dlg.contains(closer)) {
      event.preventDefault();
      event.stopPropagation();
      close(dlg);
    }
  }, true);

  document.addEventListener("close", function (event) {
    if (event.target === dialog()) event.target.replaceChildren();
  }, true);
})();
