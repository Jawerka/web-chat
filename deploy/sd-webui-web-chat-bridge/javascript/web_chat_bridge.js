/**
 * web-chat bridge: long-poll queue + apply to img2img.
 */

function webChatBridgeRoot() {
    if (typeof gradioApp === "function") {
        return gradioApp();
    }
    return document;
}

function webChatBridgeLog(msg) {
    console.log("[web-chat-bridge] " + msg);
}

function webChatBridgeWarn(msg) {
    console.warn("[web-chat-bridge] " + msg);
}

function webChatBridgeFindWrap(elemId) {
    const root = webChatBridgeRoot();
    if (!root) return null;
    return root.querySelector("#" + elemId) || root.getElementById(elemId);
}

function webChatBridgeFindInput(elemId) {
    const wrap = webChatBridgeFindWrap(elemId);
    if (!wrap) return null;
    return (
        wrap.querySelector("textarea") ||
        wrap.querySelector('input[type="text"]') ||
        wrap.querySelector("input") ||
        wrap
    );
}

function webChatBridgeSyncInput(input) {
    if (!input) return;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    if (typeof updateInput === "function") {
        updateInput(input);
    } else if (typeof triggerChange === "function") {
        triggerChange(input);
    }
}

function webChatBridgeSetValue(elemId, value) {
    const input = webChatBridgeFindInput(elemId);
    if (!input) return false;
    input.value = value;
    webChatBridgeSyncInput(input);
    return true;
}

function webChatBridgeClick(elemId) {
    const wrap = webChatBridgeFindWrap(elemId);
    if (!wrap) return false;
    const btn =
        wrap.tagName === "BUTTON"
            ? wrap
            : wrap.querySelector("button:not([disabled])") || wrap.querySelector("button");
    if (!btn) return false;
    btn.click();
    return true;
}

function webChatBridgeEnsureImg2imgTab() {
    if (typeof switch_to_img2img === "function") {
        try {
            switch_to_img2img();
            return true;
        } catch (_err) {
            /* fall through */
        }
    }
    const root = webChatBridgeRoot();
    const selectors = [
        "#tab_img2img button",
        "#tab_img2img",
        "#img2img_tab",
        'button[aria-label="img2img"]',
    ];
    for (let i = 0; i < selectors.length; i += 1) {
        const el = root.querySelector(selectors[i]);
        if (el) {
            el.click();
            return true;
        }
    }
    return false;
}

function webChatBridgeClickSendToImg2img() {
    webChatBridgeClick("web_chat_bridge_send_i2i");
    return [];
}

var webChatBridgeApplying = false;
var webChatBridgeWaitAbort = null;

function webChatBridgeShouldWatch() {
    return !document.hidden;
}

function webChatBridgeApplyPending(filename, attempt) {
    if (webChatBridgeApplying) return;

    const tryNo = attempt || 0;
    webChatBridgeEnsureImg2imgTab();

    if (!webChatBridgeFindWrap("web_chat_bridge_apply_queued")) {
        if (tryNo < 12) {
            setTimeout(function () {
                webChatBridgeApplyPending(filename, tryNo + 1);
            }, 250);
            return;
        }
        webChatBridgeWarn("apply button not found after retries");
        return;
    }

    webChatBridgeApplying = true;
    webChatBridgeLog("applying queued import: " + (filename || ""));

    webChatBridgeSetValue(
        "web_chat_bridge_status",
        "Applying " + (filename || "import") + "…"
    );

    if (!webChatBridgeClick("web_chat_bridge_apply_queued")) {
        webChatBridgeApplying = false;
        webChatBridgeWarn("apply button click failed");
        return;
    }

    setTimeout(function () {
        webChatBridgeApplying = false;
    }, 3000);
}

function webChatBridgeCheckPending() {
    if (!webChatBridgeShouldWatch() || webChatBridgeApplying) return;

    fetch("/web-chat-bridge/pending", { credentials: "same-origin" })
        .then(function (res) {
            return res.json();
        })
        .then(function (data) {
            if (data && data.pending) {
                webChatBridgeApplyPending(data.filename, 0);
            }
        })
        .catch(function () {
            /* ignore */
        });
}

function webChatBridgeWaitLoop() {
    if (!webChatBridgeShouldWatch()) {
        setTimeout(webChatBridgeWaitLoop, 1500);
        return;
    }

    if (webChatBridgeApplying) {
        setTimeout(webChatBridgeWaitLoop, 400);
        return;
    }

    if (webChatBridgeWaitAbort) {
        try {
            webChatBridgeWaitAbort.abort();
        } catch (_err) {
            /* ignore */
        }
    }

    const controller = new AbortController();
    webChatBridgeWaitAbort = controller;

    fetch("/web-chat-bridge/wait-pending?timeout=22", {
        credentials: "same-origin",
        signal: controller.signal,
    })
        .then(function (res) {
            return res.json();
        })
        .then(function (data) {
            webChatBridgeWaitAbort = null;
            if (data && data.pending) {
                webChatBridgeApplyPending(data.filename, 0);
            }
            webChatBridgeWaitLoop();
        })
        .catch(function (err) {
            webChatBridgeWaitAbort = null;
            if (err && err.name === "AbortError") {
                return;
            }
            webChatBridgeWarn("wait-pending error: " + err);
            setTimeout(webChatBridgeWaitLoop, 2000);
        });
}

function webChatBridgeStartWatchers() {
    webChatBridgeEnsureImg2imgTab();
    webChatBridgeCheckPending();
    webChatBridgeWaitLoop();
    setInterval(webChatBridgeCheckPending, 2500);
}

onUiLoaded(function () {
    webChatBridgeStartWatchers();

    document.addEventListener("visibilitychange", function () {
        if (!document.hidden) {
            webChatBridgeCheckPending();
        }
    });

    const root = webChatBridgeRoot();
    ["#tab_img2img", "#tab_txt2img", "#tabs"].forEach(function (sel) {
        root.querySelector(sel)?.addEventListener("click", function () {
            setTimeout(webChatBridgeCheckPending, 400);
        });
    });
});
