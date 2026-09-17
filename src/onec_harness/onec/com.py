from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from typing import Any

from onec_harness.settings import Settings


IDENTIFIER_RE = re.compile(r"^[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*$")
FIELD_RE = re.compile(r"^[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_.]*$")


class ComConnectorError(RuntimeError):
    pass


def _quoted(value: str) -> str:
    return value.replace('"', '""')


def derive_com_connection_string(settings: Settings) -> str:
    """Return a COM connection string, deriving it from /F or /S when possible."""
    if settings.onec_com_connection.strip():
        return settings.onec_com_connection.strip()

    raw = settings.onec_ib_connection.strip()
    file_match = re.search(r'(?i)(?:^|\s)/F\s+(?:"([^"]+)"|(\S+))', raw)
    server_match = re.search(r'(?i)(?:^|\s)/S\s+(?:"([^"]+)"|(\S+))', raw)
    parts: list[str] = []
    if file_match:
        path = file_match.group(1) or file_match.group(2) or ""
        parts.append(f'File="{_quoted(path)}"')
    elif server_match:
        value = server_match.group(1) or server_match.group(2) or ""
        if "\\" not in value:
            raise ComConnectorError("/S connection must have server\\infobase form or set ONEC_COM_CONNECTION")
        server, reference = value.split("\\", 1)
        parts.extend([f'Srvr="{_quoted(server)}"', f'Ref="{_quoted(reference)}"'])
    else:
        raise ComConnectorError("ONEC_COM_CONNECTION is not configured and ONEC_IB_CONNECTION cannot be derived")

    if settings.onec_user:
        parts.append(f'Usr="{_quoted(settings.onec_user)}"')
    if settings.onec_password:
        parts.append(f'Pwd="{_quoted(settings.onec_password)}"')
    return ";".join(parts) + ";"


class ComConnector:
    """Small, auditable V83.COMConnector adapter.

    pywin32 is imported lazily so the package remains testable on Linux. Runtime
    mutations are disabled unless both the caller and settings explicitly allow
    them. Read helpers use the 1C query language rather than database SQL.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        dispatch_factory: Callable[[str], Any] | None = None,
        allow_writes: bool | None = None,
    ) -> None:
        self.settings = settings
        self.dispatch_factory = dispatch_factory
        self.allow_writes = settings.onec_runtime_allow_writes if allow_writes is None else allow_writes
        self._connector: Any | None = None
        self._connection: Any | None = None

    @property
    def configured(self) -> bool:
        try:
            return bool(derive_com_connection_string(self.settings))
        except ComConnectorError:
            return False

    @staticmethod
    def _invoke(obj: Any, names: Sequence[str], *args: Any) -> Any:
        last_error: Exception | None = None
        for name in names:
            try:
                member = getattr(obj, name)
                return member(*args)
            except Exception as exc:  # COM dispatch errors vary by pywin32/platform version.
                last_error = exc
        joined = "/".join(names)
        raise ComConnectorError(f"1C COM method {joined} failed: {last_error}") from last_error

    @staticmethod
    def _get(obj: Any, names: Sequence[str]) -> Any:
        last_error: Exception | None = None
        for name in names:
            try:
                return getattr(obj, name)
            except Exception as exc:
                last_error = exc
        joined = "/".join(names)
        raise ComConnectorError(f"1C COM property {joined} is unavailable: {last_error}") from last_error

    @staticmethod
    def _set(obj: Any, name: str, value: Any) -> None:
        try:
            setattr(obj, name, value)
        except Exception as exc:
            raise ComConnectorError(f"Cannot set 1C property {name!r}: {exc}") from exc

    def connect(self) -> Any:
        if self._connection is not None:
            return self._connection

        factory = self.dispatch_factory
        if factory is None:
            if os.name != "nt":
                raise ComConnectorError("V83.COMConnector is available only on Windows; use a Windows host with 1C installed")
            try:
                import pythoncom  # type: ignore[import-not-found]
                import win32com.client  # type: ignore[import-not-found]
            except ImportError as exc:
                raise ComConnectorError("pywin32 is required for COM runtime integration: pip install -e .[windows]") from exc
            pythoncom.CoInitialize()
            factory = win32com.client.Dispatch

        try:
            self._connector = factory(self.settings.onec_com_progid)
        except Exception as exc:
            raise ComConnectorError(f"Cannot create {self.settings.onec_com_progid}: {exc}") from exc
        connection_string = derive_com_connection_string(self.settings)
        self._connection = self._invoke(self._connector, ("Connect",), connection_string)
        return self._connection

    def _to_python(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        connection = self.connect()
        try:
            return self._invoke(connection, ("String", "Строка"), value)
        except ComConnectorError:
            return str(value)

    @staticmethod
    def _field(name: str) -> str:
        value = name.strip()
        if not FIELD_RE.fullmatch(value):
            raise ComConnectorError(f"Unsafe/invalid 1C field name: {name!r}")
        return value

    @staticmethod
    def _identifier(name: str) -> str:
        value = name.strip()
        if not IDENTIFIER_RE.fullmatch(value):
            raise ComConnectorError(f"Unsafe/invalid 1C metadata name: {name!r}")
        return value

    def query(
        self,
        text: str,
        *,
        fields: Sequence[str],
        parameters: Mapping[str, Any] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        normalized = text.lstrip().casefold()
        if not (normalized.startswith("выбрать") or normalized.startswith("select")):
            raise ComConnectorError("runtime query must be read-only and start with ВЫБРАТЬ/SELECT")
        if not fields:
            raise ComConnectorError("At least one result field is required")
        if not 1 <= limit <= 10_000:
            raise ComConnectorError("limit must be between 1 and 10000")
        requested_fields = [self._field(field) for field in fields]

        connection = self.connect()
        query = self._invoke(connection, ("NewObject", "НовыйОбъект"), "Query", text)
        for key, value in (parameters or {}).items():
            parameter_name = self._identifier(str(key))
            self._invoke(query, ("SetParameter", "УстановитьПараметр"), parameter_name, value)
        result = self._invoke(query, ("Execute", "Выполнить"))
        selection = self._invoke(result, ("Choose", "Select", "Выбрать"))

        rows: list[dict[str, Any]] = []
        while len(rows) < limit and bool(self._invoke(selection, ("Next", "Следующий"))):
            row: dict[str, Any] = {}
            for field in requested_fields:
                try:
                    row[field] = self._to_python(getattr(selection, field))
                except Exception as exc:
                    raise ComConnectorError(f"Cannot read query field {field!r}: {exc}") from exc
            rows.append(row)
        return rows

    def _select_object(
        self,
        source: str,
        name: str,
        *,
        fields: Sequence[str],
        filters: Mapping[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        name = self._identifier(name)
        selected_fields = [self._field(field) for field in fields]
        if not selected_fields:
            raise ComConnectorError("At least one field is required")
        if not 1 <= limit <= 1000:
            raise ComConnectorError("semantic read limit must be between 1 and 1000")

        select_part = ",\n    ".join(f"T.{field} КАК {field.replace('.', '_')}" for field in selected_fields)
        parameters: dict[str, Any] = {}
        where_parts: list[str] = []
        for index, (field, value) in enumerate((filters or {}).items()):
            safe_field = self._field(str(field))
            parameter_name = f"P{index}"
            where_parts.append(f"T.{safe_field} = &{parameter_name}")
            parameters[parameter_name] = value
        where = "\nГДЕ " + " И ".join(where_parts) if where_parts else ""
        text = f"ВЫБРАТЬ ПЕРВЫЕ {limit}\n    {select_part}\nИЗ {source}.{name} КАК T{where}"
        result_fields = [field.replace(".", "_") for field in selected_fields]
        return self.query(text, fields=result_fields, parameters=parameters, limit=limit)

    def catalog_items(
        self,
        name: str,
        *,
        fields: Sequence[str] = ("Ссылка", "Код", "Наименование"),
        filters: Mapping[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self._select_object("Справочник", name, fields=fields, filters=filters, limit=limit)

    def document_items(
        self,
        name: str,
        *,
        fields: Sequence[str] = ("Ссылка", "Дата", "Номер"),
        filters: Mapping[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self._select_object("Документ", name, fields=fields, filters=filters, limit=limit)

    def register_records(
        self,
        kind: str,
        name: str,
        *,
        fields: Sequence[str],
        filters: Mapping[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        normalized = kind.strip().lower().replace("-", "_")
        sources = {
            "information": "РегистрСведений",
            "information_register": "РегистрСведений",
            "accumulation": "РегистрНакопления",
            "accumulation_register": "РегистрНакопления",
        }
        if normalized not in sources:
            raise ComConnectorError(f"Unsupported register kind: {kind!r}")
        return self._select_object(sources[normalized], name, fields=fields, filters=filters, limit=limit)

    def _require_writes(self) -> None:
        if not self.allow_writes:
            raise ComConnectorError("COM runtime writes are disabled; enable ONEC_RUNTIME_ALLOW_WRITES and explicit write mode")

    def create_catalog_item(self, name: str, attributes: Mapping[str, Any]) -> str:
        self._require_writes()
        name = self._identifier(name)
        connection = self.connect()
        catalogs = self._get(connection, ("Catalogs", "Справочники"))
        manager = self._get(catalogs, (name,))
        item = self._invoke(manager, ("CreateItem", "СоздатьЭлемент"))
        for key, value in attributes.items():
            self._set(item, self._identifier(str(key)), value)
        self._invoke(item, ("Write", "Записать"))
        reference = self._get(item, ("Ref", "Ссылка"))
        return str(self._to_python(reference))

    def create_document(self, name: str, attributes: Mapping[str, Any], *, post: bool = False) -> str:
        self._require_writes()
        name = self._identifier(name)
        connection = self.connect()
        documents = self._get(connection, ("Documents", "Документы"))
        manager = self._get(documents, (name,))
        document = self._invoke(manager, ("CreateDocument", "СоздатьДокумент"))
        for key, value in attributes.items():
            self._set(document, self._identifier(str(key)), value)
        if post:
            modes = self._get(connection, ("DocumentWriteMode", "РежимЗаписиДокумента"))
            mode = self._get(modes, ("Posting", "Проведение"))
            self._invoke(document, ("Write", "Записать"), mode)
        else:
            self._invoke(document, ("Write", "Записать"))
        reference = self._get(document, ("Ref", "Ссылка"))
        return str(self._to_python(reference))

    def call_exported(self, method: str, args: Sequence[Any] = ()) -> Any:
        """Call an exported global-context method. Classified as a write-capable action."""
        self._require_writes()
        method = self._identifier(method)
        connection = self.connect()
        result = self._invoke(connection, (method,), *args)
        return self._to_python(result)
