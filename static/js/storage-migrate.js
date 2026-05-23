/**
 * Версионирование localStorage (P2.5): одноразовые миграции при обновлении UI.
 */
(function () {
  const VERSION_KEY = 'webchat_storage_schema_v';
  const CURRENT_VERSION = 2;

  const COMPOSER_DRAFTS_KEY = 'webchat_composer_drafts_v1';
  const PRESET_DRAFTS_KEY = 'webchat_preset_drafts_v1';

  function migrateToV1() {
    if (localStorage.getItem('webchat_macro_context_full') === '1') {
      if (!localStorage.getItem('webchat_macro_context_mode')) {
        localStorage.setItem('webchat_macro_context_mode', 'full');
      }
      localStorage.removeItem('webchat_macro_context_full');
    }
  }

  function normalizeComposerDrafts() {
    try {
      const raw = localStorage.getItem(COMPOSER_DRAFTS_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        localStorage.removeItem(COMPOSER_DRAFTS_KEY);
        return;
      }
      let changed = false;
      const out = {};
      for (const [convId, entry] of Object.entries(parsed)) {
        if (!convId || typeof entry !== 'object' || entry === null) {
          changed = true;
          continue;
        }
        const text = typeof entry.text === 'string' ? entry.text : '';
        const attachments = Array.isArray(entry.attachments)
          ? entry.attachments.filter((a) => a && typeof a === 'object' && a.id)
          : [];
        const updatedAt = Number.isFinite(entry.updatedAt) ? entry.updatedAt : Date.now();
        if (!text.trim() && !attachments.length) {
          changed = true;
          continue;
        }
        out[convId] = { text, attachments, updatedAt };
      }
      const next = JSON.stringify(out);
      if (changed || next !== raw) {
        localStorage.setItem(COMPOSER_DRAFTS_KEY, next);
      }
    } catch {
      localStorage.removeItem(COMPOSER_DRAFTS_KEY);
    }
  }

  function normalizePresetDrafts() {
    try {
      const raw = localStorage.getItem(PRESET_DRAFTS_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        localStorage.removeItem(PRESET_DRAFTS_KEY);
        return;
      }
      let changed = false;
      const out = {};
      for (const [presetId, entry] of Object.entries(parsed)) {
        if (!presetId || typeof entry !== 'object' || entry === null) {
          changed = true;
          continue;
        }
        const text = typeof entry.text === 'string' ? entry.text : '';
        const synced = Boolean(entry.synced);
        const updatedAt = Number.isFinite(entry.updatedAt) ? entry.updatedAt : Date.now();
        out[presetId] = { text, synced, updatedAt };
      }
      const next = JSON.stringify(out);
      if (changed || next !== raw) {
        localStorage.setItem(PRESET_DRAFTS_KEY, next);
      }
    } catch {
      localStorage.removeItem(PRESET_DRAFTS_KEY);
    }
  }

  function migrateToV2() {
    normalizeComposerDrafts();
    normalizePresetDrafts();
  }

  function runStorageMigrations() {
    let version = parseInt(localStorage.getItem(VERSION_KEY) || '0', 10);
    if (!Number.isFinite(version) || version < 0) {
      version = 0;
    }
    if (version < 1) {
      migrateToV1();
      version = 1;
    }
    if (version < 2) {
      migrateToV2();
      version = 2;
    }
    if (version < CURRENT_VERSION) {
      localStorage.setItem(VERSION_KEY, String(CURRENT_VERSION));
    } else if (localStorage.getItem(VERSION_KEY) === null) {
      localStorage.setItem(VERSION_KEY, String(CURRENT_VERSION));
    }
  }

  runStorageMigrations();
  window.runWebchatStorageMigrations = runStorageMigrations;
})();
