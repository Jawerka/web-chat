/**
 * Тема и размер шрифта (P5.4).
 * Подключается до chat.js; API: WebChatAppearance.*
 */
(function () {
  'use strict';

  function loadTheme() {
    const dark = localStorage.getItem('webchat_theme') === 'dark'
      || (!localStorage.getItem('webchat_theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.body.classList.toggle('dark-theme', dark);
  }

  function updateThemeToggleLabel(app) {
    if (!app.$.themeToggleLabel) return;
    const dark = document.body.classList.contains('dark-theme');
    app.$.themeToggleLabel.textContent = dark ? 'Тёмная тема' : 'Светлая тема';
  }

  function loadFontSize(app) {
    const saved = parseInt(localStorage.getItem('webchat_font_size') || '', 10);
    if (app.$.fontSizeInput && !Number.isNaN(saved)) {
      app.$.fontSizeInput.value = String(saved);
    }
    applyFontSize(app);
  }

  function applyFontSize(app) {
    if (!app.$.fontSizeInput) return;
    const n = parseInt(app.$.fontSizeInput.value, 10) || 14;
    const clamped = Math.max(8, Math.min(20, n));
    app.$.fontSizeInput.value = String(clamped);
    document.documentElement.style.setProperty('--font-size', `${clamped}px`);
    localStorage.setItem('webchat_font_size', String(clamped));
    WebChatComposer.autoResizeInput(app);
  }

  function changeFontSize(app, delta) {
    if (!app.$.fontSizeInput) return;
    const current = parseInt(app.$.fontSizeInput.value, 10) || 14;
    app.$.fontSizeInput.value = String(current + delta);
    applyFontSize(app);
  }

  function toggleTheme(app) {
    const dark = !document.body.classList.contains('dark-theme');
    document.body.classList.toggle('dark-theme', dark);
    localStorage.setItem('webchat_theme', dark ? 'dark' : 'light');
    updateThemeToggleLabel(app);
  }

  window.WebChatAppearance = {
    loadTheme,
    updateThemeToggleLabel,
    loadFontSize,
    applyFontSize,
    changeFontSize,
    toggleTheme,
  };
})();
