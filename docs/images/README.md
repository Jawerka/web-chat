# Скриншоты и иллюстрации

Здесь хранятся изображения для документации (README, wiki, презентации).

| Файл | Описание |
|------|----------|
| [ui-overview.png](ui-overview.png) | Главный экран чата: беседы, сообщения, composer, пресет |

При добавлении новых файлов используйте понятные имена (`gallery.png`, `settings.png`, …) и при необходимости обновляйте таблицу и ссылки в корневом [README.md](../../README.md).

В корневом README картинки вставляются через HTML (не `![...]()`), чтобы превью стабильно работало в GitHub и в IDE:

```html
<p align="center">
  <a href="docs/images/ui-overview.png">
    <img src="docs/images/ui-overview.png" alt="…" width="920" style="max-width: 100%; height: auto;" />
  </a>
</p>
```
