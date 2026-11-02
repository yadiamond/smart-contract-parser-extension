Багаутдинов Алмаз Шамилевич M3111 Lab3

# Smart Contract Parser

Расширение для VS Code: по адресу верифицированного смарт-контракта автоматически генерирует Python-файл с асинхронными методами вызова всех его функций (web3.py / AsyncWeb3).

## Как это работает

1. В боковой панели проводника появляется секция **Smart Contracts** с кнопкой ⊕.
2. При нажатии ⊕ расширение спрашивает:
   - сеть EVM (Ethereum, Sepolia, BNB Smart Chain, Polygon, Arbitrum One, Base, Optimism, Avalanche или произвольный chainid),
   - адрес контракта,
   - API-ключ Etherscan (при первом запуске; сохраняется в настройках).
3. Скрипт на Python запрашивает ABI через [Etherscan API v2](https://docs.etherscan.io/etherscan-v2) (`getsourcecode`) и генерирует файл `<ИмяКонтракта>_methods.py` в корне открытой папки. Для прокси дополнительно загружается ABI по адресу из поля `Implementation`; методы прокси и implementation объединяются без дубликатов.
4. Контракт появляется в дереве сайдбара со списком всех функций; клик по функции открывает сгенерированный файл на её строке.

Сгенерированный файл содержит:

- константы `PRIVATE_KEY`, `CONTRACT_ADDRESS` и полный `CONTRACT_ABI`;
- класс-обёртку с методом на каждую функцию ABI: view/pure — через `await ....call()`, изменяющие состояние — сборка транзакции, оценка газа (× 1.2), подпись и `send_raw_transaction`; у payable-функций есть параметр `msg_value`;
- отдельные имена для перегруженных функций; вызов выполняется по полной ABI-сигнатуре, включая tuple-параметры.

Все методы асинхронные — файл рассчитан на использование в бэкенде (FastAPI, aiohttp и т.п.). Экземпляр `AsyncWeb3` передаётся в конструктор класса.

## Требования

- [Python 3](https://www.python.org/) в `PATH` (путь настраивается).
- Бесплатный API-ключ Etherscan: [etherscan.io](https://etherscan.io/) → Settings → API keys. Один ключ работает для всех поддерживаемых сетей.
- Python-зависимости (`requests` для получения ABI и `web3` для запуска сгенерированного файла): `pip install -r requirements.txt`.

## Настройки

| Параметр | По умолчанию | Описание |
| --- | --- | --- |
| `smart-contract-parser.etherscanApiKey` | `""` | API-ключ Etherscan (запрашивается сам при первом использовании) |
| `smart-contract-parser.pythonPath` | `"python3"` | Путь до интерпретатора Python |

## Запуск из командной палитры

`Ctrl+Shift+P` / `Cmd+Shift+P` → **Smart Contracts: Generate Methods**.

## Ограничения

- Контракт должен быть верифицирован на Etherscan, иначе ABI недоступен.
- Транзакционные методы требуют заполненного `PRIVATE_KEY` и средств на адресе; не коммитьте настоящий ключ в git.

## Сборка из исходников

```bash
npm install
npm run compile
npx vsce package --allow-missing-repository
```

Полученный `.vsix` устанавливается в VS Code через расширения → «…» → Install from VSIX.
