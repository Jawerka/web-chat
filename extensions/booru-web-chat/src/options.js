const DEFAULT_WEB_CHAT_URL = "http://192.168.88.44:8090";
const DEFAULT_PRESET = "img2img";

const form = document.getElementById("options-form");
const urlInput = document.getElementById("web-chat-url");
const presetInput = document.getElementById("preset-slug");
const statusEl = document.getElementById("status");

/**
 * @param {string} url
 * @returns {string}
 */
function normalizeBaseUrl(url) {
  return String(url || "").trim().replace(/\/+$/, "");
}

/**
 * @param {string} baseUrl
 * @returns {string}
 */
function hostPattern(baseUrl) {
  const parsed = new URL(baseUrl);
  return `${parsed.protocol}//${parsed.host}/*`;
}

/**
 * @param {string} message
 * @param {boolean} [isError]
 */
function showStatus(message, isError = false) {
  statusEl.hidden = false;
  statusEl.textContent = message;
  statusEl.style.color = isError ? "#b91c1c" : "#047857";
}

async function loadOptions() {
  const stored = await chrome.storage.sync.get({
    webChatBaseUrl: DEFAULT_WEB_CHAT_URL,
    presetSlug: DEFAULT_PRESET,
  });
  urlInput.value = stored.webChatBaseUrl;
  presetInput.value = stored.presetSlug;
}

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const webChatBaseUrl = normalizeBaseUrl(urlInput.value);
  const presetSlug = presetInput.value.trim() || DEFAULT_PRESET;

  try {
    const pattern = hostPattern(webChatBaseUrl);
    const granted = await chrome.permissions.request({ origins: [pattern] });
    if (!granted) {
      showStatus("Host permission was not granted for the web-chat URL.", true);
      return;
    }

    await chrome.storage.sync.set({ webChatBaseUrl, presetSlug });
    showStatus("Saved.");
  } catch (err) {
    showStatus(err instanceof Error ? err.message : "Failed to save options.", true);
  }
});

loadOptions();
