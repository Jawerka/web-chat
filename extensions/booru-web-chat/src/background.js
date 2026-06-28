const DEFAULT_WEB_CHAT_URL = "http://192.168.88.44:8090";
const DEFAULT_TITLE = "Новая беседа";
const DEFAULT_PRESET = "img2img";
const SESSION_COOKIE_NAME = "webchat_session";

/**
 * @param {string} url
 * @returns {string}
 */
function normalizeBaseUrl(url) {
  return String(url || "").trim().replace(/\/+$/, "");
}

async function getSettings() {
  const stored = await chrome.storage.sync.get({
    webChatBaseUrl: DEFAULT_WEB_CHAT_URL,
    presetSlug: DEFAULT_PRESET,
  });
  return {
    webChatBaseUrl: normalizeBaseUrl(stored.webChatBaseUrl),
    presetSlug: stored.presetSlug || DEFAULT_PRESET,
  };
}

/**
 * @param {number} tabId
 * @param {string} text
 * @param {string} color
 */
async function setBadge(tabId, text, color) {
  await chrome.action.setBadgeText({ tabId, text });
  await chrome.action.setBadgeBackgroundColor({ tabId, color });
}

/**
 * @param {string} url
 * @param {string} [mime]
 * @returns {string}
 */
function deriveFilename(url, mime) {
  try {
    const name = new URL(url).pathname.split("/").pop();
    if (name && name.includes(".")) return name.split("?")[0];
  } catch {
    // ignore
  }
  const ext = mime?.includes("png") ? "png" : mime?.includes("webp") ? "webp" : "jpg";
  return `image.${ext}`;
}

/**
 * Download in the post tab so Referer/cookies match what the CDN expects.
 * @param {number} tabId
 * @param {string} imageUrl
 * @returns {Promise<{ base64: string, mime: string }>}
 */
async function downloadImageViaTab(tabId, imageUrl) {
  const [result] = await chrome.scripting.executeScript({
    target: { tabId },
    func: async (url) => {
      const res = await fetch(url);
      if (!res.ok) {
        return { ok: false, status: res.status };
      }
      const blob = await res.blob();
      const buffer = await blob.arrayBuffer();
      const bytes = new Uint8Array(buffer);
      let binary = "";
      const chunkSize = 0x8000;
      for (let i = 0; i < bytes.length; i += chunkSize) {
        binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
      }
      return {
        ok: true,
        base64: btoa(binary),
        mime: blob.type || "application/octet-stream",
      };
    },
    args: [imageUrl],
  });

  const data = result?.result;
  if (!data?.ok) {
    throw new Error(`Failed to download image from booru (HTTP ${data?.status ?? "?"})`);
  }
  return { base64: data.base64, mime: data.mime };
}

/**
 * @param {string} base64
 * @param {string} mime
 * @returns {Blob}
 */
function base64ToBlob(base64, mime) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new Blob([bytes], { type: mime });
}

/**
 * @param {string} baseUrl
 * @returns {Promise<boolean>}
 */
async function hasSessionCookie(baseUrl) {
  const parsed = new URL(`${normalizeBaseUrl(baseUrl)}/`);
  const cookie = await chrome.cookies.get({
    url: `${parsed.origin}/`,
    name: SESSION_COOKIE_NAME,
  });
  if (cookie?.value) return true;

  const host = parsed.hostname;
  const port = parsed.port || "";
  const candidates = await chrome.cookies.getAll({ name: SESSION_COOKIE_NAME });
  return candidates.some((candidate) => {
    const cookieHost = candidate.domain.replace(/^\./, "");
    const hostMatches =
      cookieHost === host || host === cookieHost || host.endsWith(`.${cookieHost}`);
    if (!hostMatches || !candidate.value) return false;
    if (candidate.port && port && candidate.port !== port) return false;
    return true;
  });
}

/**
 * @param {string} apiBase
 * @param {{ text: string, title: string, presetSlug: string, base64: string, mime: string, filename: string }} payload
 */
async function createChatDirect(apiBase, payload) {
  const form = new FormData();
  form.append("text", payload.text);
  form.append("title", payload.title);
  form.append("preset_slug", payload.presetSlug);
  form.append("image", base64ToBlob(payload.base64, payload.mime), payload.filename);

  const res = await fetch(`${apiBase}/api/conversations/from-image`, {
    method: "POST",
    body: form,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401) {
      throw new Error(
        `Web-chat requires login (${apiBase}) — sign in in this browser, or disable AUTH on the server`
      );
    }
    const detail = typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return body;
}

/**
 * @param {string} baseUrl
 * @returns {Promise<chrome.tabs.Tab | undefined>}
 */
async function findWebChatTab(baseUrl) {
  const origin = new URL(`${baseUrl}/`).origin;
  const tabs = await chrome.tabs.query({});
  return tabs.find((tab) => {
    if (!tab.url) return false;
    try {
      return new URL(tab.url).origin === origin;
    } catch {
      return false;
    }
  });
}

/**
 * @param {number} tabId
 * @returns {Promise<void>}
 */
function waitForTabLoad(tabId) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      reject(new Error("Timed out waiting for web-chat tab to load"));
    }, 15000);

    function listener(updatedTabId, info) {
      if (updatedTabId !== tabId || info.status !== "complete") return;
      clearTimeout(timeout);
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    }

    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId).then((tab) => {
      if (tab.status === "complete") {
        clearTimeout(timeout);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    }).catch(reject);
  });
}

/**
 * Same-origin fetch from a web-chat tab so the session cookie is sent.
 * @param {number} tabId
 * @param {string} apiBase
 * @param {{ text: string, title: string, presetSlug: string, base64: string, mime: string, filename: string }} payload
 */
async function createChatViaTab(tabId, apiBase, payload) {
  const [result] = await chrome.scripting.executeScript({
    target: { tabId },
    func: async (base, data) => {
      const binary = atob(data.base64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i += 1) {
        bytes[i] = binary.charCodeAt(i);
      }

      const form = new FormData();
      form.append("text", data.text);
      form.append("title", data.title);
      form.append("preset_slug", data.presetSlug);
      form.append("image", new Blob([bytes], { type: data.mime }), data.filename);

      const res = await fetch(`${base}/api/conversations/from-image`, {
        method: "POST",
        body: form,
        credentials: "include",
      });
      const body = await res.json().catch(() => ({}));
      return { ok: res.ok, status: res.status, body };
    },
    args: [apiBase, payload],
  });

  const data = result?.result;
  if (!data?.ok) {
    if (data?.status === 401) {
      throw new Error(
        `Not logged in to web-chat (${apiBase}) — open web-chat in this browser and sign in first`
      );
    }
    const detail = typeof data?.body?.detail === "string" ? data.body.detail : `HTTP ${data?.status ?? "?"}`;
    throw new Error(detail);
  }

  return data.body;
}

/**
 * @param {{ text: string, imageUrl: string }} pageData
 * @param {{ webChatBaseUrl: string, presetSlug: string }} settings
 * @param {{ base64: string, mime: string }} imageData
 * @returns {Promise<{ data: Record<string, unknown>, openTab: boolean }>}
 */
async function createChatMultipart(pageData, settings, imageData) {
  const payload = {
    text: pageData.text,
    title: DEFAULT_TITLE,
    presetSlug: settings.presetSlug,
    base64: imageData.base64,
    mime: imageData.mime,
    filename: deriveFilename(pageData.imageUrl, imageData.mime),
  };

  if (!(await hasSessionCookie(settings.webChatBaseUrl))) {
    const data = await createChatDirect(settings.webChatBaseUrl, payload);
    return { data, openTab: false };
  }

  let helperTabId = null;
  try {
    let webChatTab = await findWebChatTab(settings.webChatBaseUrl);
    if (!webChatTab?.id) {
      webChatTab = await chrome.tabs.create({
        url: `${settings.webChatBaseUrl}/`,
        active: false,
      });
      helperTabId = webChatTab.id ?? null;
      if (!helperTabId) {
        throw new Error("Failed to open web-chat tab for authenticated request");
      }
      await waitForTabLoad(helperTabId);
    }

    const data = await createChatViaTab(webChatTab.id, settings.webChatBaseUrl, payload);
    return { data, openTab: true };
  } finally {
    if (helperTabId) {
      chrome.tabs.remove(helperTabId).catch(() => {});
    }
  }
}

chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id) return;

  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["dist/inject.js"],
    });

    const [result] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: async () => {
        const { extractPage } = globalThis.__booruWebChat;
        const response = extractPage();
        if (response.ok) {
          await navigator.clipboard.writeText(response.text);
        }
        return response;
      },
    });

    const pageData = result?.result;
    if (!pageData?.ok) {
      console.error("[booru-web-chat]", pageData?.error || "Page extraction failed");
      await setBadge(tab.id, "!", "#f44336");
      return;
    }

    const settings = await getSettings();
    const imageData = await downloadImageViaTab(tab.id, pageData.imageUrl);
    const { data, openTab } = await createChatMultipart(pageData, settings, imageData);

    if (openTab) {
      const chatPath = data.chat_url || `/?conv=${data.conversation_id}`;
      const chatUrl = chatPath.startsWith("http")
        ? chatPath
        : `${settings.webChatBaseUrl}${chatPath.startsWith("/") ? chatPath : `/${chatPath}`}`;
      await chrome.tabs.create({ url: chatUrl });
    }

    await setBadge(tab.id, String(pageData.count), "#4caf50");
  } catch (err) {
    console.error("[booru-web-chat]", err);
    await setBadge(tab.id, "!", "#f44336");
  }

  setTimeout(() => chrome.action.setBadgeText({ tabId: tab.id, text: "" }), 2500);
});
