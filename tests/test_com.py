from pathlib import Path

import pytest

from onec_harness.onec.com import ComConnector, ComConnectorError, derive_com_connection_string
from onec_harness.settings import Settings


class FakeSelection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.index = -1

    def Next(self) -> bool:
        self.index += 1
        if self.index >= len(self.rows):
            return False
        for key, value in self.rows[self.index].items():
            setattr(self, key, value)
        return True


class FakeResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def Choose(self) -> FakeSelection:
        return FakeSelection(self.rows)


class FakeQuery:
    def __init__(self, text: str, rows: list[dict[str, object]]) -> None:
        self.text = text
        self.rows = rows
        self.parameters: dict[str, object] = {}

    def SetParameter(self, key: str, value: object) -> None:
        self.parameters[key] = value

    def Execute(self) -> FakeResult:
        return FakeResult(self.rows)


class FakeConnection:
    def __init__(self) -> None:
        self.query: FakeQuery | None = None

    def NewObject(self, kind: str, text: str) -> FakeQuery:
        assert kind == "Query"
        self.query = FakeQuery(text, [{"Код": "001", "Наименование": "Тест"}])
        return self.query

    def String(self, value: object) -> str:
        return str(value)


class FakeConnector:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.connection_string = ""

    def Connect(self, connection_string: str) -> FakeConnection:
        self.connection_string = connection_string
        return self.connection


def test_derive_file_com_connection() -> None:
    settings = Settings(
        onec_ib_connection='/F "C:\\1c\\demo"',
        onec_user="Robot",
        onec_password="secret",
    )

    value = derive_com_connection_string(settings)

    assert 'File="C:\\1c\\demo"' in value
    assert 'Usr="Robot"' in value
    assert 'Pwd="secret"' in value


def test_query_through_fake_com_connector(tmp_path: Path) -> None:
    connection = FakeConnection()
    connector = FakeConnector(connection)
    settings = Settings(onec_com_connection='File="C:\\fake";', onec_workspace=tmp_path)
    runtime = ComConnector(settings, dispatch_factory=lambda _: connector)

    rows = runtime.query(
        "ВЫБРАТЬ Код, Наименование ИЗ Справочник.Товары",
        fields=["Код", "Наименование"],
        parameters={"Фильтр": "x"},
    )

    assert rows == [{"Код": "001", "Наименование": "Тест"}]
    assert connection.query is not None
    assert connection.query.parameters == {"Фильтр": "x"}


def test_query_rejects_non_select() -> None:
    settings = Settings(onec_com_connection='File="C:\\fake";')
    runtime = ComConnector(settings, dispatch_factory=lambda _: FakeConnector(FakeConnection()))

    with pytest.raises(ComConnectorError, match="read-only"):
        runtime.query("УДАЛИТЬ ИЗ Справочник.Товары", fields=["Код"])


def test_runtime_writes_are_disabled_by_default() -> None:
    settings = Settings(onec_com_connection='File="C:\\fake";')
    runtime = ComConnector(settings, dispatch_factory=lambda _: FakeConnector(FakeConnection()))

    with pytest.raises(ComConnectorError, match="writes are disabled"):
        runtime.create_catalog_item("Товары", {"Наименование": "Тест"})
