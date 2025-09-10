(function() {
  const basket_entry_count = document.getElementsByClassName('lingo--basket-items')[0]
  if (basket_entry_count) {
    {% if basket.lines|length %}
      basket_entry_count.textContent = "{{ basket.lines|length }}"
      basket_entry_count.style.display = "inline-block"
    {% else %}
      basket_entry_count.textContent = ""
      basket_entry_count.style.display = "none"
    {% endif %}
  }
})();
