#!/usr/bin/env python3

import argparse
import json
import keyword
import os
import re
import sys
from pprint import pformat

import requests

ETHERSCAN_API = "https://api.etherscan.io/v2/api"

CHAIN_NAMES = {
    1: "Ethereum",
    11155111: "Sepolia",
    56: "BNB Smart Chain",
    137: "Polygon",
    42161: "Arbitrum One",
    8453: "Base",
    10: "Optimism",
    43114: "Avalanche",
}


def fail(message):
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    sys.exit(1)


def to_python_identifier(name):
    ident = re.sub(r"\W", "_", name or "")
    if not ident:
        ident = "arg"
    if ident[0].isdigit():
        ident = "_" + ident
    if keyword.iskeyword(ident):
        ident += "_"
    return ident


def unique_identifier(base_name, used_names):
    name = base_name
    suffix = 2
    while name in used_names:
        name = "{}_{}".format(base_name, suffix)
        suffix += 1
    used_names.add(name)
    return name


def fetch_contract_data(address, api_key, chain_id):
    try:
        response = requests.get(
            ETHERSCAN_API,
            params={
                "chainid": chain_id,
                "module": "contract",
                "action": "getsourcecode",
                "address": address,
                "apikey": api_key,
            },
            timeout=30,
        )
        response.raise_for_status()
    except requests.HTTPError:
        fail("Etherscan вернул HTTP-ошибку {}".format(response.status_code))
    except requests.RequestException as exc:
        fail("Нет доступа к Etherscan: {}".format(type(exc).__name__))
    try:
        payload = response.json()
    except ValueError:
        fail("Etherscan вернул ответ, не являющийся JSON")

    if payload.get("status") != "1":
        detail = payload.get("result") or payload.get("message") or "неизвестная ошибка"
        fail("Etherscan: {}".format(detail))

    matched_contracts = payload.get("result") or []
    if not matched_contracts:
        fail("Etherscan вернул пустой ответ для {}".format(address))
    contract_info = matched_contracts[0]

    abi_raw = contract_info.get("ABI") or ""
    if not abi_raw or abi_raw == "Contract source code not verified":
        fail("Контракт {} не верифицирован на Etherscan — ABI недоступен".format(address))
    try:
        abi = json.loads(abi_raw)
    except json.JSONDecodeError:
        fail("Не удалось разобрать ABI, полученный от Etherscan для {}".format(address))
    if not isinstance(abi, list):
        fail("ABI контракта {} имеет неожиданный формат".format(address))
    return contract_info, abi


def fetch_abi(address, api_key, chain_id):
    contract_info, abi = fetch_contract_data(address, api_key, chain_id)
    contract_name = (contract_info.get("ContractName") or "").strip()
    implementation = (contract_info.get("Implementation") or "").strip()
    visited_addresses = {address.lower()}

    while implementation and implementation.lower() != "0x" + "0" * 40:
        if not re.fullmatch(r"0[xX][0-9a-fA-F]{40}", implementation):
            fail("Etherscan вернул некорректный адрес implementation: {}".format(implementation))
        if implementation.lower() in visited_addresses:
            fail("Обнаружен циклический адрес implementation: {}".format(implementation))

        contract_info, implementation_abi = fetch_contract_data(implementation, api_key, chain_id)
        abi = merge_abis(abi, implementation_abi)
        visited_addresses.add(implementation.lower())
        implementation = (contract_info.get("Implementation") or "").strip()

    return abi, contract_name


def safe_contract_name(name, address):
    cleaned = re.sub(r"[^\w]", "_", name or "").strip("_")
    if not cleaned:
        cleaned = "Contract_" + address[2:12]
    if cleaned[0].isdigit():
        cleaned = "Contract_" + cleaned
    if keyword.iskeyword(cleaned):
        cleaned = "Contract_" + cleaned
    return cleaned


def canonical_abi_type(input_definition):
    abi_type = input_definition.get("type") or ""
    if not abi_type.startswith("tuple"):
        return abi_type
    components = input_definition.get("components") or []
    component_types = ",".join(canonical_abi_type(component) for component in components)
    return "({}){}".format(component_types, abi_type[len("tuple"):])


def merge_abis(*abis):
    merged = []
    seen_entries = set()

    for abi in abis:
        for entry in abi:
            if isinstance(entry, dict) and entry.get("type") == "function":
                inputs = entry.get("inputs") or []
                signature = "{}({})".format(
                    entry.get("name") or "",
                    ",".join(canonical_abi_type(input_definition) for input_definition in inputs),
                )
                entry_key = "function:{}".format(signature)
            else:
                entry_key = json.dumps(entry, sort_keys=True, ensure_ascii=False)

            if entry_key not in seen_entries:
                seen_entries.add(entry_key)
                merged.append(entry)

    return merged


def generate_wrapper_file(abi, contract_name, address, out_dir, chain_name):
    function_entries = [
        entry for entry in abi
        if isinstance(entry, dict) and entry.get("type") == "function"
    ]
    if not function_entries:
        fail("В ABI нет ни одной функции — генерировать нечего")

    class_name = safe_contract_name(contract_name, address)
    lines = []
    line_number = 0
    function_reports = []
    used_method_names = {"__init__", "_send_transaction"}

    def add(line=""):
        nonlocal line_number
        lines.append(line)
        line_number += 1

    add("from web3 import AsyncWeb3")
    add("from eth_utils.address import to_checksum_address")
    add("")
    add('PRIVATE_KEY = ""')
    add("CONTRACT_ADDRESS = {!r}".format(address))
    add("CONTRACT_ABI = {}".format(pformat(abi, indent=1, width=110, sort_dicts=False)))
    add("")
    add("")
    add("class {}:".format(class_name))
    add("    def __init__(")
    add("        self,")
    add("        web3: AsyncWeb3,")
    add("        private_key: str = PRIVATE_KEY,")
    add("        contract_address: str = CONTRACT_ADDRESS,")
    add("    ):")
    add("        self.web3 = web3")
    add("        self.private_key = private_key")
    add("        self.address = web3.eth.account.from_key(private_key).address if private_key else None")
    add("        self.contract = web3.eth.contract(")
    add("            address=to_checksum_address(contract_address),")
    add("            abi=CONTRACT_ABI,")
    add("        )")
    add("")
    add("    async def _send_transaction(self, function_call, msg_value: int = 0):")
    add("        if self.address is None:")
    add('            raise RuntimeError("PRIVATE_KEY не задан — отправка транзакций недоступна")')
    add("        transaction = await function_call.build_transaction({")
    add('            "from": self.address,')
    add('            "nonce": await self.web3.eth.get_transaction_count(self.address),')
    add('            "gasPrice": await self.web3.eth.gas_price,')
    add('            "value": int(msg_value),')
    add("        })")
    add('        transaction["gas"] = int(await self.web3.eth.estimate_gas(transaction) * 1.2)')
    add("        signed = self.web3.eth.account.sign_transaction(transaction, self.private_key)")
    add("        return await self.web3.eth.send_raw_transaction(signed.raw_transaction)")

    for entry in function_entries:
        raw_name = entry.get("name") or "function"
        mutability = entry.get("stateMutability") or "nonpayable"
        is_view = mutability in ("view", "pure")
        is_payable = mutability == "payable"
        inputs = entry.get("inputs") or []

        method_name = unique_identifier(to_python_identifier(raw_name), used_method_names)
        used_param_names = {"self", "msg_value"}
        param_names = []
        for index, input_definition in enumerate(inputs):
            base_name = to_python_identifier(input_definition.get("name") or "arg{}".format(index))
            param_names.append(unique_identifier(base_name, used_param_names))

        def_params = ["self"] + param_names
        if is_payable:
            def_params.append("msg_value: int = 0")

        abi_signature = "{}({})".format(
            raw_name,
            ",".join(canonical_abi_type(input_definition) for input_definition in inputs),
        )
        call_args = ", ".join(param_names)

        add("")
        add("    async def {}({}):".format(method_name, ", ".join(def_params)))
        function_reports.append({
            "signature": "{}({})".format(method_name, ", ".join(def_params[1:])),
            "kind": "view" if is_view else "tx",
            "line": line_number,
        })
        add("        function_call = self.contract.get_function_by_signature({!r})({})".format(
            abi_signature, call_args))
        if is_view:
            add("        return await function_call.call()")
        elif is_payable:
            add("        return await self._send_transaction(function_call, msg_value=msg_value)")
        else:
            add("        return await self._send_transaction(function_call)")

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
    args = parser.parse_args()

    address = args.address.strip()
    if not re.fullmatch(r"0[xX][0-9a-fA-F]{40}", address):
        fail("Адрес должен начинаться с 0x и содержать 40 шестнадцатеричных символов")
    if not args.apikey:
        fail("Не задан API-ключ Etherscan (--apikey)")

    chain_name = CHAIN_NAMES.get(args.chainid, "chain-{}".format(args.chainid))
    abi, contract_name = fetch_abi(address, args.apikey, args.chainid)
    result = generate_wrapper_file(abi, contract_name, address, args.out, chain_name)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
