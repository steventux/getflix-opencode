/* Small enhancements on top of HTMX. */
(function () {
  "use strict";

  function focusSearch() {
    const input = document.getElementById("search-input");
    if (!input) return;
    input.focus();
    input.select();
  }

  document.addEventListener("keydown", function (event) {
    const target = event.target;
    const typing =
      target &&
      (target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.isContentEditable);

    if (event.key === "/" && !typing && !event.metaKey && !event.ctrlKey) {
      event.preventDefault();
      focusSearch();
    }

    if (event.key === "Escape" && typing && target.id === "search-input") {
      target.blur();
    }
  });

  // Keep the query in the address bar so a search can be shared or reloaded.
  document.body.addEventListener("htmx:afterSwap", function (event) {
    const detail = event.detail || {};
    if (detail.target && detail.target.id === "results") {
      const form = document.querySelector(".search-form");
      const input = form ? form.querySelector("input[name='q']") : null;
      if (input && input.value) {
        const url = new URL(window.location.href);
        url.searchParams.set("q", input.value);
        window.history.replaceState({}, "", url);
      }
    }
  });

  // Surface HTMX errors in the console rather than silently failing.
  document.body.addEventListener("htmx:responseError", function (event) {
    console.error("Getflix request failed", event.detail);
  });
})();
