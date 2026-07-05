/**
 * Character Aliases (@alias): autocomplete in positive prompts + list reload hook.
 * Loads aliases.json via A1111 file= route (same pattern as tag-autocomplete).
 */
/* global gradioApp, onUiUpdate, updateInput */

(function () {
  "use strict";

  const LOG = "[character-aliases]";
  const PATH_FILE = "tmp/character_aliases_path.txt";
  const AUTOCOMPLETE_FILE = "tmp/character_aliases_autocomplete.txt";
  const DEBOUNCE_MS = 120;

  let aliases = [];
  let autocompleteEnabled = true;
  let autocompleteIndex = -1;
  let autocompleteMatches = [];
  let boundAreas = new WeakSet();
  let debounceTimer = null;

  function appRoot() {
    try {
      return typeof gradioApp === "function" ? gradioApp() : document;
    } catch {
      return document;
    }
  }

  async function readWebuiFile(relativePath, asJson) {
    const url = `file=${relativePath}?${Date.now()}`;
    const res = await fetch(url);
    if (res.status !== 200) {
      return null;
    }
    if (asJson) {
      try {
        return await res.json();
      } catch {
        return null;
      }
    }
    return (await res.text()).trim();
  }

  function normalizeQuery(q) {
    return String(q || "")
      .toLowerCase()
      .replace(/\s+/g, "_");
  }

  function filterAliases(prefix) {
    const q = normalizeQuery(prefix);
    if (!q) {
      return aliases.slice(0, 16);
    }
    return aliases
      .filter((m) => {
        const alias = String(m.alias || "").toLowerCase();
        const label = String(m.label || "").toLowerCase();
        return alias.startsWith(q) || label.includes(q.replace(/_/g, " "));
      })
      .slice(0, 16);
  }

  function getPromptTextareas() {
    const root = appRoot();
    const selectors = [
      "#txt2img_prompt > label > textarea",
      "#img2img_prompt > label > textarea",
      ".prompt > label > textarea",
    ];
    const out = new Set();
    selectors.forEach((sel) => {
      root.querySelectorAll(sel).forEach((el) => {
        if (el && el.tagName === "TEXTAREA") {
          out.add(el);
        }
      });
    });
    return [...out];
  }

  function ensurePopup() {
    let popup = document.getElementById("character_aliases_popup");
    if (popup) return popup;
    popup = document.createElement("div");
    popup.id = "character_aliases_popup";
    popup.className = "character-aliases-popup hidden";
    popup.addEventListener("mousedown", (e) => e.preventDefault());
    document.body.appendChild(popup);
    return popup;
  }

  function hidePopup() {
    const popup = document.getElementById("character_aliases_popup");
    if (!popup) return;
    popup.classList.add("hidden");
    popup.innerHTML = "";
    autocompleteIndex = -1;
    autocompleteMatches = [];
  }

  function highlightItems(popup) {
    popup.querySelectorAll(".character-alias-item").forEach((el, i) => {
      el.classList.toggle("selected", i === autocompleteIndex);
    });
  }

  function truncate(text, max) {
    const s = String(text || "");
    return s.length <= max ? s : s.slice(0, max - 1) + "…";
  }

  function renderPopup(textarea, matches) {
    const popup = ensurePopup();
    if (!matches.length) {
      hidePopup();
      return;
    }
    autocompleteMatches = matches;
    if (autocompleteIndex < 0 || autocompleteIndex >= matches.length) {
      autocompleteIndex = 0;
    }
    popup.innerHTML = matches
      .map((m, i) => {
        const alias = m.alias || "";
        const label = m.label || "";
        const preview = truncate(m.body || "", 72);
        return `<button type="button" class="character-alias-item${i === autocompleteIndex ? " selected" : ""}" data-index="${i}">
          <span class="character-alias-name">@${alias}</span>
          <span class="character-alias-meta">${label ? label + " · " : ""}${preview}</span>
        </button>`;
      })
      .join("");

    popup.querySelectorAll(".character-alias-item").forEach((btn) => {
      btn.addEventListener("mousedown", (e) => {
        e.preventDefault();
        const idx = Number(btn.dataset.index);
        applySelection(textarea, idx);
        hidePopup();
      });
    });

    const rect = textarea.getBoundingClientRect();
    popup.style.left = `${rect.left + window.scrollX}px`;
    popup.style.top = `${rect.bottom + window.scrollY + 4}px`;
    popup.style.minWidth = `${Math.max(rect.width, 280)}px`;
    popup.classList.remove("hidden");
  }

  function parseAtChunk(textarea) {
    const value = textarea.value;
    const pos = textarea.selectionStart ?? value.length;
    const before = value.slice(0, pos);
    const at = before.lastIndexOf("@");
    if (at < 0) return null;
    if (at > 0 && before[at - 1] === "@") return null;
    const chunk = before.slice(at + 1);
    if (/[\n,]/.test(chunk)) return null;
    return { at, pos, chunk };
  }

  function syncInput(textarea) {
    if (typeof updateInput === "function") {
      updateInput(textarea);
      return;
    }
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function normalizeBodyForInsert(body) {
    const text = String(body || "").trim();
    if (!text) return "";
    return text.replace(/,\s*$/, "");
  }

  function buildInsertText(body, after) {
    const normalized = normalizeBodyForInsert(body);
    if (!normalized) return "";
    const afterLead = after.replace(/^\s*/, "");
    if (!afterLead) {
      return normalized;
    }
    if (afterLead.startsWith(",")) {
      return normalized;
    }
    return `${normalized}, `;
  }

  function applySelection(textarea, index) {
    const m = autocompleteMatches[index];
    if (!m) return;
    const parsed = parseAtChunk(textarea);
    if (!parsed) return;
    const before = textarea.value.slice(0, parsed.at);
    const after = textarea.value.slice(parsed.pos);
    const insert = buildInsertText(m.body, after);
    if (!insert) return;
    textarea.value = before + insert + after;
    const cursor = before.length + insert.length;
    textarea.selectionStart = textarea.selectionEnd = cursor;
    syncInput(textarea);
    textarea.focus();
  }

  function onInput(textarea) {
    if (!autocompleteEnabled) return;
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      const parsed = parseAtChunk(textarea);
      if (!parsed) {
        hidePopup();
        return;
      }
      const matches = filterAliases(parsed.chunk);
      renderPopup(textarea, matches);
    }, DEBOUNCE_MS);
  }

  function onKeydown(textarea, e) {
    const popup = document.getElementById("character_aliases_popup");
    if (!popup || popup.classList.contains("hidden") || !autocompleteMatches.length) {
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      autocompleteIndex = Math.min(autocompleteIndex + 1, autocompleteMatches.length - 1);
      highlightItems(popup);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      autocompleteIndex = Math.max(autocompleteIndex - 1, 0);
      highlightItems(popup);
    } else if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      applySelection(textarea, autocompleteIndex);
      hidePopup();
    } else if (e.key === "Escape") {
      hidePopup();
    }
  }

  function bindTextarea(textarea) {
    if (boundAreas.has(textarea)) return;
    boundAreas.add(textarea);
    textarea.addEventListener("input", () => onInput(textarea));
    textarea.addEventListener("keydown", (e) => onKeydown(textarea, e));
    textarea.addEventListener("blur", () => setTimeout(hidePopup, 180));
  }

  function setupAutocomplete() {
    getPromptTextareas().forEach(bindTextarea);
  }

  async function reloadAliases() {
    const enabledFlag = await readWebuiFile(AUTOCOMPLETE_FILE, false);
    autocompleteEnabled = enabledFlag !== "0";

    const jsonPath = await readWebuiFile(PATH_FILE, false);
    if (!jsonPath) {
      aliases = [];
      console.warn(LOG, "aliases path not published yet");
      return;
    }

    const data = await readWebuiFile(jsonPath, true);
    aliases = Array.isArray(data) ? data : [];
    console.log(LOG, "loaded", aliases.length, "aliases from", jsonPath);
  }

  function init() {
    reloadAliases().then(setupAutocomplete);
  }

  window.characterAliasesReloadList = function characterAliasesReloadList() {
    reloadAliases();
    return [];
  };

  if (typeof onUiUpdate === "function") {
    onUiUpdate(init);
  } else if (typeof onUiLoaded === "function") {
    onUiLoaded(init);
  } else {
    document.addEventListener("DOMContentLoaded", init);
  }
})();
