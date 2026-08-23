/*
 * Everything HTMX does not: reading a local file into the textarea, the
 * clipboard, the blob download, and the Ctrl/Cmd+Enter submit shortcut.
 * Every request, every poll and every stage transition is an HTMX attribute
 * in the templates, not a line in here.
 *
 * Listeners are attached to `document` and delegate down to whichever
 * element is live at the time, rather than being bound to specific elements
 * once at load: HTMX replaces `#stage` (and, inside it, `#job-status`)
 * wholesale on every swap, and anything bound directly to the old nodes
 * would simply stop firing once they are gone.
 */

(function () {
  "use strict";

  const numberFormat = new Intl.NumberFormat("es-ES");

  function hasContent(text) {
    return /\S/.test(text);
  }

  /* Los signos sueltos (" . ", " ... ") no cuentan como palabra. */
  function countWords(text) {
    const trimmed = text.trim();
    if (!trimmed) return 0;
    return trimmed.split(/\s+/).filter((token) => /[\p{L}\p{N}]/u.test(token)).length;
  }

  function countParagraphs(text) {
    return text
      .split(/\n{2,}/)
      .map((p) => p.trim())
      .filter(Boolean).length;
  }

  function describeText(text) {
    const words = countWords(text);
    const paragraphs = countParagraphs(text);
    return [
      `${numberFormat.format(words)} ${words === 1 ? "palabra" : "palabras"}`,
      `${numberFormat.format(paragraphs)} ${paragraphs === 1 ? "párrafo" : "párrafos"}`,
    ].join(" · ");
  }

  function refreshCompose() {
    const textarea = document.getElementById("manuscript-text");
    const summary = document.getElementById("text-summary");
    const submitButton = document.getElementById("submit-button");
    if (!textarea) return;

    const value = textarea.value;
    if (summary) summary.textContent = describeText(value);
    if (submitButton) submitButton.disabled = !hasContent(value);
  }

  function focusNewHeading() {
    const heading = document.querySelector('#stage h1[tabindex="-1"]');
    if (heading) heading.focus();
  }

  async function handleFileChosen(input) {
    const file = input.files && input.files[0];
    const textarea = document.getElementById("manuscript-text");
    const filenameField = document.getElementById("filename-field");
    const fileNameHint = document.getElementById("file-name");
    if (!file || !textarea) return;

    try {
      textarea.value = await file.text();
      if (filenameField) filenameField.value = file.name;
      if (fileNameHint) {
        fileNameHint.hidden = false;
        fileNameHint.textContent = `Cargado: ${file.name}`;
      }
    } catch {
      if (fileNameHint) {
        fileNameHint.hidden = false;
        fileNameHint.textContent = "No se ha podido leer el archivo.";
      }
    }
    refreshCompose();
  }

  function handleClear() {
    const textarea = document.getElementById("manuscript-text");
    const fileInput = document.getElementById("file-input");
    const filenameField = document.getElementById("filename-field");
    const fileNameHint = document.getElementById("file-name");

    if (textarea) textarea.value = "";
    if (fileInput) fileInput.value = "";
    if (filenameField) filenameField.value = "";
    if (fileNameHint) {
      fileNameHint.hidden = true;
      fileNameHint.textContent = "";
    }
    refreshCompose();
    if (textarea) textarea.focus();
  }

  function submitShortcut(event) {
    if (event.key !== "Enter" || !(event.ctrlKey || event.metaKey)) return;
    const form = document.getElementById("compose-form");
    if (!form) return;
    const submitButton = document.getElementById("submit-button");
    if (submitButton && submitButton.disabled) return;
    event.preventDefault();
    form.requestSubmit();
  }

  async function handleCopy(button) {
    const source = document.getElementById(button.dataset.source);
    if (!source) return;

    const original = button.textContent;
    try {
      await navigator.clipboard.writeText(source.value);
      button.textContent = "¡Copiado!";
    } catch {
      button.textContent = "No se pudo copiar";
    }
    setTimeout(() => {
      button.textContent = original;
    }, 2000);
  }

  function handleDownload(button) {
    const source = document.getElementById(button.dataset.source);
    if (!source) return;

    const blob = new Blob([source.value], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = button.dataset.filename || "manuscrito-corregido.txt";
    link.rel = "noopener";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  document.addEventListener("input", (event) => {
    if (event.target.id === "manuscript-text") refreshCompose();
  });

  document.addEventListener("change", (event) => {
    if (event.target.id === "file-input") handleFileChosen(event.target);
  });

  document.addEventListener("click", (event) => {
    const target = event.target.closest("button");
    if (!target) return;
    if (target.id === "clear-button") handleClear();
    if (target.id === "copy-button") handleCopy(target);
    if (target.id === "download-button") handleDownload(target);
  });

  document.addEventListener("keydown", submitShortcut);

  document.addEventListener("DOMContentLoaded", refreshCompose);
  document.body.addEventListener("htmx:afterSettle", () => {
    refreshCompose();
    focusNewHeading();
  });
})();
