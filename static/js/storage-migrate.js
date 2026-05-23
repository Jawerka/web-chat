/**
 * Версионирование localStorage (P2.5): одноразовые миграции при обновлении UI.
 */
(function () {
  const VERSION_KEY = 'webchat_storage_schema_v';
  const CURRENT_VERSION = 1;

  function migrateToV1() {
    if (localStorage.getItem('webchat_macro_context_full') === '1') {
      if (!localStorage.getItem('webchat_macro_context_mode')) {
        localStorage.setItem('webchat_macro_context_mode', 'full');
      }
      localStorage.removeItem('webchat_macro_context_full');
    }
  }

  function runStorageMigrations() {
    const raw = localStorage.getItem(VERSION_KEY);
    let version = parseInt(raw || '0', 10);
    if (!Number.isFinite(version) || version < 0) {
      version = 0;
    }
    if (version < 1) {
      migrateToV1();
      version = 1;
    }
    if (version < CURRENT_VERSION) {
      localStorage.setItem(VERSION_KEY, String(CURRENT_VERSION));
    } else if (raw === null) {
      localStorage.setItem(VERSION_KEY, String(CURRENT_VERSION));
    }
  }

  runStorageMigrations();
  window.runWebchatStorageMigrations = runStorageMigrations;
})();
