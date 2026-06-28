/**
 * Лёгкие toast-уведомления (copy, export, info).
 */
(function () {
  'use strict';

  let stackEl = null;
  const timers = new WeakMap();

  function ensureStack() {
    if (stackEl && document.body.contains(stackEl)) return stackEl;
    stackEl = document.getElementById('toast-stack');
    if (!stackEl) {
      stackEl = document.createElement('div');
      stackEl.id = 'toast-stack';
      stackEl.className = 'toast-stack';
      stackEl.setAttribute('aria-live', 'polite');
      stackEl.setAttribute('aria-relevant', 'additions');
      document.body.appendChild(stackEl);
    }
    return stackEl;
  }

  function show(message, kind = 'info', autoHideMs = 2800) {
    const text = String(message || '').trim();
    if (!text) return;
    const stack = ensureStack();
    const el = document.createElement('div');
    el.className = `toast toast--${kind}`;
    el.setAttribute('role', 'status');
    el.textContent = text;
    stack.appendChild(el);
    requestAnimationFrame(() => el.classList.add('is-visible'));
    const timer = setTimeout(() => {
      el.classList.remove('is-visible');
      setTimeout(() => el.remove(), 220);
    }, autoHideMs);
    timers.set(el, timer);
  }

  window.WebChatToast = { show };
})();
