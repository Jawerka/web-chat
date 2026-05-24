/**
 * Подписи статусов чата (UI).
 * Согласованы с `app/services/user_progress.py` (этапы WS `progress`).
 * При смене текстов на сервере — сверить оба файла.
 */
/* global window */

const PROGRESS_STAGE_LABELS = {
  submit: 'Сообщение принято',
  llm_thinking: 'Размышляю…',
  llm_typing: 'Печатаю ответ…',
  llm_tools: 'Выбираю действие…',
  sd_render: 'Генерация изображения…',
  sd_upscale: 'Увеличение изображения…',
  doc_read: 'Чтение документа…',
  gallery: 'Поиск в галерее…',
  save_media: 'Сохраняю результат…',
};

/** Человекочитаемые подписи MCP tools (не показывать snake_case в UI). */
const TOOL_USER_LABELS = {
  generate_image: 'Генерация изображения…',
  img2img: 'Перерисовка изображения…',
  upscale_images: 'Увеличение изображения…',
  extract_text: 'Чтение документа…',
  get_gallery: 'Поиск в галерее…',
};

window.PROGRESS_STAGE_LABELS = PROGRESS_STAGE_LABELS;
window.TOOL_USER_LABELS = TOOL_USER_LABELS;
