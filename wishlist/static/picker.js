// Item form picture picker: typing a direct image link selects the "paste a link" option.
// Without JS the member picks that radio by hand.
document.addEventListener("input", function (event) {
  var field = event.target;
  if (!field.matches || !field.matches("input[name='image_url']") || !field.form) return;
  var radio = field.form.querySelector("input[name='picture'][value='url']");
  if (radio && field.value.trim()) radio.checked = true;
});
