# Jellyfin Kinopoisk Renamer

Автоматический органайзер и переименовыватель медиафайлов для Jellyfin и qBittorrent.

## Особенности
- Поиск названий через Kinopoisk API (поддержка транслита и зарубежных названий).
- Сортировка сериалов по папкам сезонов (`Season 01`).
- Упаковка фильмов в отдельные каталоги.
- Синхронное переименование субтитров и постеров.
- Автоматическое выставление прав (PUID/PGID).
- Отправка команды на обновление библиотеки в Jellyfin API.
- Поддержка запуска через CLI, в связке с qBittorrent или в Docker.

## Быстрый старт

### 1. Настройка конфигурации
Скопируйте `config.example.json` в `config.json` и заполните ваши ключи:
```json
{
  "kinopoisk_api_key": "ВАШ_КЛЮЧ_КИНОПОИСКА",
  "jellyfin_url": "http://localhost:8096",
  "jellyfin_api_key": "ВАШ_КЛЮЧ_JELLYFIN",
  "puid": 1000,
  "pgid": 1000
}
```

Файл `config.json` добавлен в `.gitignore` и не попадет в репозиторий.

### 2. Использование из консоли
- Тестовый запуск без изменений (dry-run):
  ```bash
  python3 jellyfin_renamer.py --dry-run /path/to/media
  ```
- Реальное переименование:
  ```bash
  python3 jellyfin_renamer.py /path/to/media
  ```

### 3. Интеграция с qBittorrent
В настройках qBittorrent включите вызов программы по завершении загрузки:
```bash
/path/to/auto_renamer.sh "%F" "%L" "%N"
```
