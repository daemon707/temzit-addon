# Temzit Home Assistant Add-on repository

Этот репозиторий можно добавить в Home Assistant как **Add-on repository** через магазин дополнений. HACS сам по себе ставит интеграции и frontend-расширения, а add-ons обычно подключаются через Add-on Store / Supervisor repository URL. Документация Home Assistant описывает именно такой способ подключения пользовательского app repository. [web:97][web:101]

## Как подключить

1. Загрузите содержимое этого репозитория на GitHub.
2. В Home Assistant откройте **Settings -> Add-ons -> Add-on Store**.
3. Нажмите меню в правом верхнем углу -> **Repositories**.
4. Добавьте URL вашего GitHub-репозитория.
5. Найдите **Temzit MQTT Bridge** и установите add-on.

## Базовые настройки add-on

- `temzit_host`: `192.168.2.20`
- `mqtt_host`: `192.168.1.50`
- `mqtt_port`: `1883`
- `mqtt_user`: `addons`
- `mqtt_pass`: ваш пароль MQTT

После запуска add-on начнет публиковать MQTT Discovery сущности для Home Assistant. [web:32][web:33]
