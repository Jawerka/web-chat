/**
 * Ранний bootstrap темы (anti-FOUC). Без изменения CSS-переменных.
 * data-theme-mode: chat | gallery | login
 */
(function () {
  'use strict';

  const STORAGE_KEY = 'webchat_theme';
  const script = document.currentScript;
  const mode = (script && script.getAttribute('data-theme-mode')) || 'chat';
  const metaThemeId = script && script.getAttribute('data-meta-theme-color-id');
  const META_COLORS = { light: '#f0f2f5', dark: '#0f1419' };

  function resolveDark() {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === 'dark'
      || (!stored && window.matchMedia('(prefers-color-scheme: dark)').matches);
  }

  function applyHtml(dark) {
    if (mode === 'gallery') {
      document.documentElement.classList.toggle('gallery-dark', dark);
    } else if (mode === 'login') {
      document.documentElement.classList.toggle('login-dark', dark);
      if (metaThemeId) {
        const meta = document.getElementById(metaThemeId);
        if (meta) {
          meta.setAttribute('content', dark ? META_COLORS.dark : META_COLORS.light);
        }
      }
    }
  }

  function applyBody(dark) {
    if (document.body) {
      document.body.classList.toggle('dark-theme', dark);
    }
  }

  function applyTheme(dark) {
    applyHtml(dark);
    applyBody(dark);
  }

  function boot() {
    applyTheme(resolveDark());
  }

  boot();

  if (!document.body) {
    document.addEventListener('DOMContentLoaded', () => applyBody(resolveDark()));
  }

  if (mode !== 'chat') {
    window.addEventListener('storage', (e) => {
      if (e.key !== STORAGE_KEY) return;
      applyTheme(e.newValue === 'dark');
    });
  }

  window.WebChatThemeBoot = {
    STORAGE_KEY,
    resolveDark,
    applyTheme,
    mode,
  };
})();
