/**
 * Заголовки бесед: убрать шаблонные префиксы в списке (Генерация изображений …).
 */
(function () {
  'use strict';

  /** @type {readonly string[]} */
  const GENERIC_PREFIXES = [
    'генерация изображений',
    'генерация изображения',
    'создание изображений',
    'создание изображения',
    'генерация картинок',
    'генерация картинки',
    'создание картинки',
    'создание картинок',
    'генерация',
    'создание',
  ];

  const PREFIX_SEP_RE = /^[\s:—–\-….]+/u;

  /**
   * @param {string | null | undefined} title
   * @returns {string}
   */
  function stripGenericConvTitlePrefix(title) {
    const text = String(title || '').trim();
    if (!text) return '';

    const lower = text.toLocaleLowerCase('ru');
    for (const prefix of GENERIC_PREFIXES) {
      if (!lower.startsWith(prefix)) continue;
      const rest = text.slice(prefix.length).replace(PREFIX_SEP_RE, '').trim();
      if (rest.length >= 2 && rest !== '...' && rest !== '…') return rest;
      return text;
    }
    return text;
  }

  /**
   * Короткое имя для сайдбара; полный title остаётся в tooltip и настройках.
   * @param {string | null | undefined} title
   * @returns {string}
   */
  function formatConvTitleForList(title) {
    const stripped = stripGenericConvTitlePrefix(title);
    return stripped || String(title || '').trim();
  }

  window.WebChatConvTitleFormat = {
    stripGenericConvTitlePrefix,
    formatConvTitleForList,
  };
})();
