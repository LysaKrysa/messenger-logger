"use strict";
(() => {
  const toggle = document.getElementById("nav-toggle");
  const sidebar = document.getElementById("workspace-navigation");
  const main = document.getElementById("workspace-main");
  const backdrop = document.getElementById("nav-backdrop");
  if (toggle && sidebar && main && backdrop) {
    const desktop = matchMedia("(min-width: 1024px)");
    const close = document.getElementById("nav-close");
    let opened = false;
    const setOpen = (value, restore = true) => {
      opened = value && !desktop.matches;
      document.body.classList.toggle("nav-open", opened);
      toggle.setAttribute("aria-expanded", String(opened));
      toggle.setAttribute("aria-label", opened ? "Close navigation" : "Open navigation");
      backdrop.hidden = !opened;
      main.inert = opened;
      if (opened) {
        sidebar.setAttribute("role", "dialog");
        sidebar.setAttribute("aria-modal", "true");
        close.focus({preventScroll: true});
      } else {
        sidebar.removeAttribute("role");
        sidebar.removeAttribute("aria-modal");
        if (restore && !desktop.matches) toggle.focus({preventScroll: true});
      }
    };
    document.body.classList.add("nav-ready");
    toggle.addEventListener("click", () => setOpen(!opened));
    close.addEventListener("click", () => setOpen(false));
    backdrop.addEventListener("click", () => setOpen(false));
    sidebar.querySelectorAll("nav a").forEach(link => link.addEventListener("click", () => setOpen(false, false)));
    desktop.addEventListener("change", () => setOpen(false, false));
    document.addEventListener("keydown", event => {
      if (!opened) return;
      if (event.key === "Escape") { event.preventDefault(); setOpen(false); return; }
      if (event.key !== "Tab") return;
      const targets = [...sidebar.querySelectorAll('a[href], button:not([disabled]), input:not([type="hidden"])')]
        .filter(element => element.getClientRects().length);
      const first = targets[0], last = targets[targets.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
  }
  document.querySelectorAll("[data-copy]").forEach(button => {
    button.addEventListener("click", async () => {
      const input = document.getElementById(button.dataset.copy);
      try { await navigator.clipboard.writeText(input.value); button.textContent = "Copied"; }
      catch { input.focus(); input.select(); }
    });
  });
  document.querySelectorAll("form[data-confirm]").forEach(form => {
    form.addEventListener("submit", event => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });
  if (document.body.dataset.refresh === "true") {
    let dirty = false;
    document.addEventListener("input", () => { dirty = true; });
    setInterval(() => {
      if (!dirty && !document.hidden && !document.body.classList.contains("nav-open") &&
          !document.querySelector("details[open]") &&
          !document.activeElement?.matches("input,textarea,select")) window.location.reload();
    }, 15000);
  }
})();
