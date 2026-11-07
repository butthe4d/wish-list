// Item form, after "Find pictures": the shop's name/description differ from what the
// member typed. The server renders each as a .shop-suggestion block under its field (a
// "Replace" button that works without JS). Here they become one popup with a tick box per
// field: Replace writes the ticked values into the fields; Keep mine (also Escape and the
// backdrop) leaves the fields alone. Either way the inline blocks go away.
// Loaded by the item form itself, so htmx runs it again after each boosted swap.
(function () {
  var QUOTE_OPEN = "«", QUOTE_CLOSE = "»", ARROW = " → ";

  function fieldOf(form, block) {
    var name = block.getAttribute("data-field");
    if (name !== "name" && name !== "description") return null;
    return form.querySelector("[name='" + name + "']:not([type='hidden'])");
  }

  function shopValue(block) {
    var input = block.querySelector("input[type='hidden'][name^='suggest_']");
    return input ? input.value : "";
  }

  function row(block, field) {
    var label = document.createElement("label");
    var box = document.createElement("input");
    box.type = "checkbox";
    box.checked = true;
    box.value = block.getAttribute("data-field");
    var text = document.createElement("span");
    text.textContent = (block.getAttribute("data-label") || "") + ": " +
      QUOTE_OPEN + field.value + QUOTE_CLOSE + ARROW + QUOTE_OPEN + shopValue(block) + QUOTE_CLOSE;
    label.append(box, text);
    return label;
  }

  function open() {
    var template = document.getElementById("suggest-dialog");
    var blocks = Array.prototype.slice.call(document.querySelectorAll(".shop-suggestion"));
    if (!template || !blocks.length || document.querySelector("dialog.suggest-dialog")) return;
    var form = blocks[0].closest("form");
    var dlg = template.content.firstElementChild.cloneNode(true);
    if (!form || typeof dlg.showModal !== "function") return;  // no <dialog>: inline blocks stay
    var list = dlg.querySelector(".suggest-list");
    var pairs = [];
    blocks.forEach(function (block) {
      var field = fieldOf(form, block);
      if (!field) return;
      var label = row(block, field);
      pairs.push({ block: block, field: field, box: label.querySelector("input") });
      list.append(label);
    });
    if (!pairs.length) return;

    dlg.addEventListener("click", function (event) {
      if (event.target === dlg) dlg.close();  // the backdrop (outside the content): Keep mine
    });
    dlg.addEventListener("close", function () {
      var replace = dlg.returnValue === "replace";
      pairs.forEach(function (p) {
        if (replace && p.box.checked) p.field.value = shopValue(p.block);
        if (!replace || p.box.checked) p.block.remove();
      });
      dlg.remove();
    });
    form.after(dlg);
    dlg.showModal();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", open);
  else open();
})();
