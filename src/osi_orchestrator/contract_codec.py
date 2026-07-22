"""JSON-compatible encoding and decoding for canonical orchestrator contracts."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from types import UnionType
from typing import Any, Mapping, Union, get_args, get_origin, get_type_hints
from uuid import UUID

from .contracts import CANONICAL_CONTRACTS, SCHEMA_VERSION, JsonObject

_CONTRACT_TYPES = {contract.__name__: contract for contract in CANONICAL_CONTRACTS}


class ContractDecodeError(ValueError):
    """Raised when a serialized contract cannot be safely reconstructed."""


def encode_contract(contract: object) -> JsonObject:
    """Encode a canonical contract into a JSON-compatible tagged object."""
    contract_type = type(contract)
    if contract_type not in CANONICAL_CONTRACTS:
        raise TypeError(f"Unsupported contract type: {contract_type.__name__}")
    payload = {item.name: _encode_value(getattr(contract, item.name)) for item in fields(contract)}
    return {"contract_type": contract_type.__name__, "payload": payload}


def decode_contract(document: Mapping[str, Any]) -> object:
    """Decode and validate a tagged canonical contract document."""
    contract_name = document.get("contract_type")
    payload = document.get("payload")
    if not isinstance(contract_name, str) or contract_name not in _CONTRACT_TYPES:
        raise ContractDecodeError("Unknown or missing contract_type")
    if not isinstance(payload, Mapping):
        raise ContractDecodeError("Contract payload must be an object")
    schema_version = payload.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise ContractDecodeError(
            f"Unsupported schema version {schema_version!r}; expected {SCHEMA_VERSION!r}"
        )

    contract_type = _CONTRACT_TYPES[contract_name]
    hints = get_type_hints(contract_type)
    expected_fields = {item.name for item in fields(contract_type)}
    unknown_fields = set(payload) - expected_fields
    if unknown_fields:
        names = ", ".join(sorted(str(name) for name in unknown_fields))
        raise ContractDecodeError(f"Unknown fields for {contract_name}: {names}")

    try:
        kwargs = {
            item.name: _decode_value(payload[item.name], hints[item.name])
            for item in fields(contract_type)
            if item.name in payload
        }
        return contract_type(**kwargs)
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractDecodeError(f"Invalid {contract_name} payload: {exc}") from exc


def _encode_value(value: object) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_encode_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _encode_value(item) for key, item in value.items()}
    if is_dataclass(value):
        return {item.name: _encode_value(getattr(value, item.name)) for item in fields(value)}
    return value


def _decode_value(value: Any, annotation: Any) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is tuple:
        item_type = args[0]
        if not isinstance(value, list):
            raise TypeError("Expected a list for tuple field")
        return tuple(_decode_value(item, item_type) for item in value)

    if origin in {dict, Mapping}:
        if not isinstance(value, Mapping):
            raise TypeError("Expected an object for mapping field")
        value_type = args[1] if len(args) == 2 else Any
        return {str(key): _decode_value(item, value_type) for key, item in value.items()}

    if origin in {Union, UnionType}:
        non_none = [item for item in args if item is not type(None)]
        if value is None and len(non_none) != len(args):
            return None
        last_error: Exception | None = None
        for candidate in non_none:
            try:
                return _decode_value(value, candidate)
            except (TypeError, ValueError) as exc:
                last_error = exc
        if last_error is not None:
            raise last_error

    if annotation is UUID:
        if not isinstance(value, str):
            raise TypeError("Expected UUID string")
        return UUID(value)
    if annotation is datetime:
        if not isinstance(value, str):
            raise TypeError("Expected datetime string")
        return datetime.fromisoformat(value)
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return annotation(value)
    if annotation is Any:
        return value
    if annotation in {str, int, float, bool} and not isinstance(value, annotation):
        raise TypeError(f"Expected {annotation.__name__}")
    return value
