/* Phlox product site: progressive navigation, palette preview, and code copying. */
(function () {
  "use strict";
  document.documentElement.classList.add("js");

  const toggle = document.getElementById("navToggle");
  const nav = document.getElementById("nav");
  const mobile = window.matchMedia("(max-width: 900px)");
  function setMenu(open) {
    nav.classList.toggle("open", open);
    toggle.setAttribute("aria-expanded", String(open));
    nav.inert = mobile.matches && !open;
  }
  if (toggle && nav) {
    setMenu(false);
    toggle.addEventListener("click", () => setMenu(!nav.classList.contains("open")));
    nav.addEventListener("click", (event) => {
      if (event.target.closest("a")) setMenu(false);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && nav.classList.contains("open")) {
        setMenu(false);
        toggle.focus();
      }
    });
    document.addEventListener("click", (event) => {
      if (!event.target.closest(".site-header")) setMenu(false);
    });
    mobile.addEventListener("change", () => setMenu(false));
  }

  // Only animate after an observer is available; content stays visible without JS.
  const reveals = document.querySelectorAll(".reveal");
  if (
    "IntersectionObserver" in window &&
    !window.matchMedia("(prefers-reduced-motion: reduce)").matches
  ) {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("in");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.05 },
    );
    reveals.forEach((element) => {
      element.classList.add("will-reveal");
      observer.observe(element);
    });
  }

  const status = document.createElement("p");
  status.className = "sr-only";
  status.setAttribute("role", "status");
  document.body.appendChild(status);
  document.querySelectorAll(".copy-btn").forEach((button) => {
    let timer;
    button.addEventListener("click", async () => {
      const target = document.getElementById(button.dataset.copyTarget);
      if (!target) return;
      const text = target.textContent;
      let copied = false;
      try {
        await navigator.clipboard.writeText(text);
        copied = true;
      } catch {
        const input = document.createElement("textarea");
        input.value = text;
        input.className = "sr-only";
        document.body.appendChild(input);
        input.select();
        try {
          copied = document.execCommand("copy");
        } catch {
          /* Show manual-copy guidance below. */
        }
        input.remove();
        button.focus();
      }
      clearTimeout(timer);
      button.textContent = copied ? "Copied" : "Select code";
      button.classList.toggle("copied", copied);
      status.textContent = copied
        ? `${button.getAttribute("aria-label")}: copied.`
        : "Copy unavailable. Select the code and copy it manually.";
      timer = setTimeout(() => {
        button.textContent = "Copy";
        button.classList.remove("copied");
      }, 1800);
    });
  });

  const demo = document.getElementById("theme-demo");
  const previewName = document.getElementById("preview-name");
  const choices = document.querySelectorAll("[data-preview]");
  choices.forEach((button) => {
    button.disabled = false;
    button.addEventListener("click", () => {
      demo.dataset.previewTheme = button.dataset.preview;
      previewName.textContent = button.lastElementChild.textContent;
      choices.forEach((choice) => choice.setAttribute("aria-pressed", String(choice === button)));
    });
  });

  // Derive sections from the actual navigation so added links remain supported.
  if (nav && "IntersectionObserver" in window) {
    const links = [...nav.querySelectorAll('a[href^="#"]')];
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          links.forEach((link) => {
            if (link.hash === `#${entry.target.id}`) link.setAttribute("aria-current", "location");
            else link.removeAttribute("aria-current");
          });
        });
      },
      { rootMargin: "-20% 0px -65% 0px" },
    );
    links.forEach((link) => {
      const section = document.querySelector(link.hash);
      if (section) observer.observe(section);
    });
  }
})();
