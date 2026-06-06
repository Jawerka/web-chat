"""
PNG Rename from Metadata

Описание программы:
-------------------
Программа автоматически переименовывает изображения (PNG, JPG, JPEG) на основе метаданных.
Имя файла формируется по шаблону: "MM-DD HH-MM [md5_5символов] - Seed.расширение"
где Seed извлекается из параметров изображения (EXIF).

Основной функционал:
- Извлечение Seed из метаданных изображения
- Переименование файлов по дате модификации и хэшу
- Удаление дубликатов на основе MD5-хэша содержимого изображения
- Рекурсивная обработка папок

Требования:
------------
- Python 3.9+
- pillow >= 10.2.0

Установка зависимостей:
-----------------------
pip install pillow

Или через poetry:
poetry add pillow

Использование:
-------------
Измените DEFAULT_PATHS в коде, указав пути к папкам с изображениями.
Запустите скрипт: python main.py
"""

from PIL import Image
import os
import time
import hashlib
import uuid
import ctypes
from ctypes import wintypes

Image.MAX_IMAGE_PIXELS = 200000000

# Допустимые расширения изображений
GOOD_EXTENSIONS = ['.png', '.jpg', '.jpeg']

# Список путей, которые будут обработаны
DEFAULT_PATHS = [
    r"D:\User\Downloads"
]

# Показываем, какие пути будут обработаны
for path in DEFAULT_PATHS:
    print(f'Будет обработан путь: {path}')
time.sleep(2)

# ---- далее ваш исходный код без изменений ----
def renamer(path: str):
    """
    Переименовывает изображение по шаблону:
    "MM-DD HH-MM [md5_первые_5_символов] - Seed.расширение"
    Если файл с таким именем уже существует, текущий удаляется как дубликат.
    """
    filename = os.path.basename(path)
    ext = os.path.splitext(filename)[1].lower()

    if ext not in GOOD_EXTENSIONS:
        return

    path_to_dir = os.path.dirname(path)

    try:
        im = Image.open(path)
        im.load()
    except Exception as e:
        print(f"Ошибка при открытии изображения {path}: {e}")
        return

    modification_timestamp = int(os.stat(path).st_mtime)
    modification_time = time.gmtime(modification_timestamp + 3 * 3600)  # Преобразуем в UTC+3
    formatted_date = time.strftime("%m-%d %H-%M", modification_time)

    try:
        seed = im.info['parameters'].split('Seed: ')[1].split(', ')[0]
    except (KeyError, IndexError):
        return

    md5_hash = hashlib.md5(im.tobytes()).hexdigest()
    short_hash = md5_hash[:5]

    new_name = f'{formatted_date} {short_hash} - {seed}{ext}'
    new_path = os.path.join(path_to_dir, new_name)

    if os.path.exists(new_path):
        if os.path.abspath(path) != os.path.abspath(new_path):
            try:
                os.remove(path)
                print(f'Удалён дубликат: {path}')
            except Exception as e:
                print(f'Ошибка при удалении дубликата {path}: {e}')
        return

    try:
        os.rename(path, new_path)
        print(f'Переименовано: {filename} → {new_name}')
    except Exception as e:
        print(f"Ошибка при переименовании {path}: {e}")


def removal_duplicate(path: str):
    hashes = {}       # Хэш: путь к первому уникальному файлу
    duplicates = []   # Пути к файлам-дубликатам

    def walk_and_hash(current_path: str):
        try:
            items = os.listdir(current_path)
        except Exception as e:
            print(f"Ошибка при доступе к {current_path}: {e}")
            return

        for name in items:
            full_path = os.path.join(current_path, name)

            if os.path.isdir(full_path):
                walk_and_hash(full_path)
                continue

            ext = os.path.splitext(name)[1].lower()
            if ext not in GOOD_EXTENSIONS:
                continue

            try:
                with Image.open(full_path) as im:
                    im.load()
                    file_hash = hashlib.md5(im.tobytes()).hexdigest()
            except Exception as e:
                print(f"Ошибка при обработке файла {full_path}: {e}")
                continue

            if file_hash in hashes:
                duplicates.append(full_path)
            else:
                hashes[file_hash] = full_path

    walk_and_hash(path)

    for dup_path in duplicates:
        try:
            os.remove(dup_path)
            print(f"Удалён дубликат: {dup_path}")
        except Exception as e:
            print(f"Ошибка при удалении {dup_path}: {e}")


def walk_on_path(path: str):
    try:
        items = os.listdir(path)
    except Exception as e:
        print(f"Ошибка при доступе к {path}: {e}")
        return

    for name in items:
        full_path = os.path.join(path, name)

        if os.path.isdir(full_path):
            walk_on_path(full_path)
        else:
            try:
                renamer(full_path)
            except Exception as err:
                print(f"Ошибка при обработке файла {full_path}: {err}")


# Запуск обработки для всех путей
for path in DEFAULT_PATHS:
    if not os.path.exists(path):
        print(f'\nПуть не существует: {path}')
        continue

    print(f"\nУдаление дубликатов в {path}")
    removal_duplicate(path)

    print(f"\nПереименование файлов в {path}")
    walk_on_path(path)

print("\nОбработка завершена.")
