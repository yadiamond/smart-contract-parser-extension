#!/usr/bin/env python3

import argparse
import json
import keyword
import os
import re
import sys
import urllib.error
import urllib.request
from pprint import pformat

ETHERSCAN_API = "https://api.etherscan.io/v2/api"

CHAINS = {
    1: {"name": "Ethereum", "rpc": "https://ethereum-rpc.publicnode.com"},
    11155111: {"name": "Sepolia", "rpc": "https://ethereum-sepolia-rpc.publicnode.com"},
    56: {"name": "BNB Smart Chain", "rpc": "https://bsc-rpc.publicnode.com"},
    137: {"name": "Polygon", "rpc": "https://polygon-bor-rpc.publicnode.com"},
    42161: {"name": "Arbitrum One", "rpc": "https://arbitrum-one-rpc.publicnode.com"},
    8453: {"name": "Base", "rpc": "https://base-rpc.publicnode.com"},
    10: {"name": "Optimism", "rpc": "https://optimism-rpc.publicnode.com"},
    43114: {"name": "Avalanche", "rpc": "https://avalanche-cchain-rpc.publicnode.com"},
}


def fail(message):
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    sys.exit(1)


def to_python_identifier(name):
    ident = re.sub(r"\W", "_", name or "")
    if not ident:
        ident = "func"
    if ident[0].isdigit():
        ident = "_" + ident
    if keyword.iskeyword(ident):
        ident += "_"
    return ident


def python_param_name(input_name, index):
    if not input_name:
        return "arg{}".format(index)
    return to_python_identifier(input_name)


def fetch_abi(address, api_key, abi_file, chain_id):
    if abi_file:
        try:
            with open(abi_file, encoding="utf-8") as abi_handle:
                loaded = json.load(abi_handle)
        except OSError as exc:
            fail("Не удалось открыть файл ABI: {}".format(exc))
        except json.JSONDecodeError:
            fail("Файл ABI не является корректным JSON")
        if isinstance(loaded, dict):
            loaded = loaded.get("abi", [])
        if not isinstance(loaded, list):
            fail("В файле ABI должен лежать JSON-массив (или объект с полем abi)")
        return loaded, ""

    request_url = "{}?chainid={}&module=contract&action=getsourcecode&address={}&apikey={}".format(
        ETHERSCAN_API, chain_id, address, api_key)
    try:
        with urllib.request.urlopen(request_url, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        fail("Etherscan вернул HTTP-ошибку {}".format(exc.code))
    except urllib.error.URLError as exc:
        fail("Нет доступа к Etherscan: {}".format(exc.reason))
    except json.JSONDecodeError:
        fail("Etherscan вернул ответ, не являющийся JSON")

    if payload.get("status") != "1":
        detail = payload.get("result") or payload.get("message") or "неизвестная ошибка"
        fail("Etherscan: {}".format(detail))

    matched_contracts = payload.get("result") or []
    if not matched_contracts:
        fail("Etherscan вернул пустой ответ")
    contract_info = matched_contracts[0]

    abi_raw = contract_info.get("ABI") or ""
    contract_name = (contract_info.get("ContractName") or "").strip()
    if not abi_raw or abi_raw == "Contract source code not verified":
        fail("Контракт не верифицирован на Etherscan — ABI недоступен")
    try:
        abi = json.loads(abi_raw)
    except json.JSONDecodeError:
        fail("Не удалось разобрать ABI, полученный от Etherscan")
    if not isinstance(abi, list):
        fail("ABI имеет неожиданный формат")
    return abi, contract_name


def safe_contract_name(name, address):
    cleaned = re.sub(r"[^\w]", "_", name or "").strip("_")
    if not cleaned:
        cleaned = "Contract_" + address[2:12]
    if cleaned[0].isdigit():
        cleaned = "Contract_" + cleaned
    return cleaned


def generate_wrapper_file(abi, contract_name, address, out_dir, chain_name, rpc_url):
    function_entries = [entry for entry in abi if isinstance(entry, dict) and entry.get("type") == "function"]
    if not function_entries:
        fail("В ABI нет ни одной функции — генерировать нечего")

    class_name = safe_contract_name(contract_name, address)
    has_view_functions = any(entry.get("stateMutability") in ("view", "pure") for entry in function_entries)
    lines = []
    line_number = 0
    function_reports = []
    used_method_names = set()
    demo_method = None

    def add(line=""):
        nonlocal line_number
        lines.append(line)
        line_number += line.count("\n") + 1

    if has_view_functions:
        add("import asyncio")
        add("")
    add("from web3 import AsyncWeb3")
    if has_view_functions:
        add("from web3.providers.rpc import AsyncHTTPProvider")
    add("from eth_utils.address import to_checksum_address")
    add("")
    add('RPC_URL = "{}"'.format(rpc_url))
    add("")
    add('PRIVATE_KEY = ""')
    add("")
    add('TOKEN_ADDRESS = "{}"'.format(address))
    add("")
    add("CONTRACT_ABI = {}".format(pformat(abi, indent=1, width=110, sort_dicts=False)))
    add("")
    add("")
    add("class {}:".format(class_name))
    add("")
    add("    def __init__(self, web3: AsyncWeb3, private_key: str = PRIVATE_KEY, token_address: str = TOKEN_ADDRESS):")
    add("        self.web3 = web3")
    add("        self.private_key = private_key")
    add("        self.address = web3.eth.account.from_key(private_key).address if private_key else None")
    add("        self.token_contract = web3.eth.contract(address=to_checksum_address(token_address), abi=CONTRACT_ABI)")
    add("")
    add("    async def _send_transaction(self, function_call, msg_value: int = 0):")
    add("        if self.address is None:")
    add('            raise RuntimeError("PRIVATE_KEY не задан — отправка транзакций недоступна")')
    add("        tx_params = {")
    add("            'from': self.address,")
    add("            'nonce': await self.web3.eth.get_transaction_count(self.address),")
    add("            'gasPrice': await self.web3.eth.gas_price,")
    add("            'value': int(msg_value),")
    add("        }")
    add("        transaction = function_call.build_transaction(tx_params)")
    add("        tx_params['gas'] = int(await self.web3.eth.estimate_gas(transaction) * 1.2)")
    add("        signed = self.web3.eth.account.sign_transaction(transaction, self.private_key)")
    add("        return await self.web3.eth.send_raw_transaction(signed.raw_transaction)")

    for entry in function_entries:
        raw_name = entry.get("name") or "func"
        mutability = entry.get("stateMutability") or "nonpayable"
        is_view = mutability in ("view", "pure")
        is_payable = mutability == "payable"
        inputs = entry.get("inputs") or []

        base_name = to_python_identifier(raw_name)
        method_name = base_name
        collision_suffix = 2
        while method_name in used_method_names:
            method_name = "{}_{}".format(base_name, collision_suffix)
            collision_suffix += 1
        used_method_names.add(method_name)
        if is_view and demo_method is None:
            demo_method = method_name

        param_names = []
        for index, input_def in enumerate(inputs):
            param_name = python_param_name(input_def.get("name") or "", index)
            if param_name == "self":
                param_name += "_"
            param_names.append(param_name)
        call_args = ", ".join(param_names)

        def_params = ["self"] + param_names
        if is_payable:
            def_params.append("msg_value: int = 0")

        add("")
        add("    async def {}({}):".format(method_name, ", ".join(def_params)))
        function_reports.append({
            "signature": "{}({})".format(method_name, call_args),
            "kind": "view" if is_view else "tx",
            "line": line_number,
        })
        if is_view:
            add("        return await self.token_contract.functions.{}({}).call()".format(raw_name, call_args))
        else:
            send_extra_args = ", msg_value" if is_payable else ""
            add("        return await self._send_transaction(self.token_contract.functions.{}({}){})".format(
                raw_name, call_args, send_extra_args))

    if demo_method:
        add("")
        add("")
        add('if __name__ == "__main__":')
        add("")
        add("    async def _demo():")
        add("        w3 = AsyncWeb3(AsyncHTTPProvider(RPC_URL))")
        add("        contract = {}(w3)".format(class_name))
        add("        try:")
        add('            print("{}() =", await contract.{}())'.format(demo_method, demo_method))
        add("        except Exception as error:")
        add('            print("Не удалось вызвать {}(), впишите RPC_URL и проверьте адрес:", error)'.format(demo_method))
        add("")
        add("    asyncio.run(_demo())")
        add("")

    output_path = os.path.join(out_dir, "{}_methods.py".format(class_name))
    try:
        with open(output_path, "w", encoding="utf-8") as output_handle:
            output_handle.write("\n".join(lines) + "\n")
    except OSError as exc:
        fail("Не удалось записать файл: {}".format(exc))

    return {
        "ok": True,
        "file": os.path.abspath(output_path),
        "name": contract_name or class_name,
        "address": address,
        "chain": chain_name,
        "functions": function_reports,
    }


def main():
    parser = argparse.ArgumentParser(description="Генератор Python-обёртки методов смарт-контракта")
    parser.add_argument("--address", required=True, help="Адрес контракта (0x...)")
    parser.add_argument("--chainid", type=int, default=1, help="Chain ID сети EVM (1 — Ethereum mainnet)")
    parser.add_argument("--apikey", default="", help="API-ключ Etherscan")
    parser.add_argument("--out", default=".", help="Каталог для сохранения сгенерированного файла")
    parser.add_argument("--abi-file", default=None,
                        help="Локальный JSON-файл с ABI вместо запроса к Etherscan (режим отладки)")
    args = parser.parse_args()

    address = args.address.strip()
    if not re.fullmatch(r"0[xX][0-9a-fA-F]{40}", address):
        fail("Адрес должен начинаться с 0x и содержать 40 шестнадцатеричных символов")
    if not args.abi_file and not args.apikey:
        fail("Не задан API-ключ Etherscan (--apikey)")

    chain = CHAINS.get(args.chainid)
    chain_name = chain["name"] if chain else "chain-{}".format(args.chainid)
    rpc_url = chain["rpc"] if chain else ""

    abi, contract_name = fetch_abi(address, args.apikey, args.abi_file, args.chainid)
    result = generate_wrapper_file(abi, contract_name, address, args.out, chain_name, rpc_url)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
