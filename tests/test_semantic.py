from pathlib import Path

from onec_harness.metadata import ConfigurationIndex
from onec_harness.semantic import MetadataEditor
from onec_harness.semantic_extra import ExtendedMetadataEditor
from onec_harness.semantic_forms import FormMetadataEditor
from onec_harness.semantic_registers import RegisterMetadataEditor
from onec_harness.workspace import Workspace


CONFIG = '''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" version="2.17">
  <Configuration uuid="00000000-0000-0000-0000-000000000001">
    <Properties><Name>HarnessTest</Name></Properties>
    <ChildObjects/>
  </Configuration>
</MetaDataObject>
'''


def test_create_catalog_registers_object_and_snapshot(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)
    editor = MetadataEditor(workspace)

    change = editor.create_catalog("Оборудование", synonym="Оборудование")

    catalog = workspace.read_text("Catalogs/Оборудование.xml")
    configuration = workspace.read_text("Configuration.xml")
    assert "<Name>Оборудование</Name>" in catalog
    assert "<Catalog>Оборудование</Catalog>" in configuration
    assert change.snapshot_id
    assert (tmp_path / ".onec-harness" / "snapshots" / f"{change.snapshot_id}.json").exists()
    index = ConfigurationIndex.build(tmp_path)
    assert any(item.kind == "catalog" and item.name == "Оборудование" for item in index.objects)


def test_add_attribute_to_created_catalog(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)
    editor = MetadataEditor(workspace)
    editor.create_catalog("Оборудование")

    change = editor.add_attribute(
        "catalog",
        "Оборудование",
        "СерийныйНомер",
        value_type="string",
        string_length=80,
    )

    text = workspace.read_text("Catalogs/Оборудование.xml")
    assert "<Name>СерийныйНомер</Name>" in text
    assert "<v8:Length>80</v8:Length>" in text
    assert change.snapshot_id


def test_create_document_can_allow_posting(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)

    MetadataEditor(workspace).create_document("Заявка", posting=True)

    text = workspace.read_text("Documents/Заявка.xml")
    assert "<Posting>Allow</Posting>" in text
    assert "<Document>Заявка</Document>" in workspace.read_text("Configuration.xml")


def test_create_enum_with_values_is_indexed(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)
    editor = ExtendedMetadataEditor(workspace)

    change = editor.create_enum("Статусы", values=["Новый", {"name": "Закрыт", "synonym": "Закрыт"}])

    text = workspace.read_text("Enums/Статусы.xml")
    assert "<Enum " in text
    assert "<Name>Новый</Name>" in text
    assert "<Name>Закрыт</Name>" in text
    assert "<Enum>Статусы</Enum>" in workspace.read_text("Configuration.xml")
    assert change.snapshot_id
    index = ConfigurationIndex.build(tmp_path)
    assert any(item.kind == "enum" and item.name == "Статусы" for item in index.objects)


def test_add_enum_value(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)
    editor = ExtendedMetadataEditor(workspace)
    editor.create_enum("Статусы", values=["Новый"])

    editor.add_enum_value("Статусы", "Отменен", synonym="Отменен")

    text = workspace.read_text("Enums/Статусы.xml")
    assert "<Name>Отменен</Name>" in text


def test_add_typed_tabular_section_to_document(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)
    editor = ExtendedMetadataEditor(workspace)
    editor.create_document("Заявка")

    change = editor.add_tabular_section(
        "document",
        "Заявка",
        "Товары",
        columns=[
            {"name": "Товар", "value_type": "CatalogRef.Товары"},
            {"name": "Количество", "value_type": "number", "digits": 15, "fraction_digits": 3},
        ],
    )

    text = workspace.read_text("Documents/Заявка.xml")
    assert "DocumentTabularSection.Заявка.Товары" in text
    assert "DocumentTabularSectionRow.Заявка.Товары" in text
    assert "<Name>Товар</Name>" in text
    assert "cfg:CatalogRef.Товары" in text
    assert "<Name>Количество</Name>" in text
    assert "<v8:FractionDigits>3</v8:FractionDigits>" in text
    assert change.snapshot_id


def test_create_information_register_with_typed_fields(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)
    editor = RegisterMetadataEditor(workspace)

    change = editor.create_information_register(
        "Цены",
        dimensions=[
            {
                "name": "Товар",
                "value_type": "CatalogRef.Товары",
                "main_filter": True,
                "deny_incomplete_values": True,
            }
        ],
        resources=[{"name": "Цена", "value_type": "number", "digits": 15, "fraction_digits": 2}],
        periodicity="Day",
    )

    text = workspace.read_text("InformationRegisters/Цены.xml")
    assert "InformationRegisterRecord.Цены" in text
    assert "InformationRegisterRecordManager.Цены" in text
    assert "<InformationRegisterPeriodicity>Day</InformationRegisterPeriodicity>" in text
    assert "<WriteMode>Independent</WriteMode>" in text
    assert "<Dimension " in text and "<Name>Товар</Name>" in text
    assert "<MainFilter>true</MainFilter>" in text
    assert "<DenyIncompleteValues>true</DenyIncompleteValues>" in text
    assert "<Resource " in text and "<Name>Цена</Name>" in text
    assert "<v8:FractionDigits>2</v8:FractionDigits>" in text
    assert "<InformationRegister>Цены</InformationRegister>" in workspace.read_text("Configuration.xml")
    assert change.snapshot_id
    index = ConfigurationIndex.build(tmp_path)
    assert any(item.kind == "information_register" and item.name == "Цены" for item in index.objects)


def test_create_balance_accumulation_register(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)
    editor = RegisterMetadataEditor(workspace)

    change = editor.create_accumulation_register(
        "ОстаткиТоваров",
        register_type="Balances",
        dimensions=[{"name": "Товар", "value_type": "CatalogRef.Товары"}],
        resources=[{"name": "Количество", "value_type": "number", "digits": 15, "fraction_digits": 3}],
    )

    text = workspace.read_text("AccumulationRegisters/ОстаткиТоваров.xml")
    assert "AccumulationRegisterRecord.ОстаткиТоваров" in text
    assert "AccumulationRegisterRecordKey.ОстаткиТоваров" in text
    assert "<RegisterType>Balance</RegisterType>" in text
    assert '<xr:StandardAttribute name="RecordType">' in text
    assert "<Name>Количество</Name>" in text
    assert "<v8:FractionDigits>3</v8:FractionDigits>" in text
    assert "<AccumulationRegister>ОстаткиТоваров</AccumulationRegister>" in workspace.read_text("Configuration.xml")
    assert change.snapshot_id
    index = ConfigurationIndex.build(tmp_path)
    assert any(item.kind == "accumulation_register" and item.name == "ОстаткиТоваров" for item in index.objects)


def test_create_turnovers_accumulation_register_omits_record_type_standard_attribute(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)
    editor = RegisterMetadataEditor(workspace)

    editor.create_accumulation_register("ПродажиОбороты", register_type="Turnovers")

    text = workspace.read_text("AccumulationRegisters/ПродажиОбороты.xml")
    assert "<RegisterType>Turnovers</RegisterType>" in text
    assert '<xr:StandardAttribute name="RecordType">' not in text


def test_create_default_catalog_form(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)
    editor = FormMetadataEditor(workspace)
    editor.create_catalog("Товары")

    change = editor.create_managed_form(
        "catalog",
        "Товары",
        "ФормаЭлемента",
        purpose="Object",
        set_default=True,
    )

    parent = workspace.read_text("Catalogs/Товары.xml")
    form = workspace.read_text("Catalogs/Товары/Forms/ФормаЭлемента/Ext/Form.xml")
    assert "<Form>ФормаЭлемента</Form>" in parent
    assert "<DefaultObjectForm>Catalog.Товары.Form.ФормаЭлемента</DefaultObjectForm>" in parent
    assert "cfg:CatalogObject.Товары" in form
    assert "<MainAttribute>true</MainAttribute>" in form
    assert workspace.resolve("Catalogs/Товары/Forms/ФормаЭлемента/Ext/Form/Module.bsl").exists()
    assert change.snapshot_id


def test_add_input_and_command_to_managed_form(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)
    editor = FormMetadataEditor(workspace)
    editor.create_document("Заявка")
    editor.create_managed_form("document", "Заявка", "ФормаДокумента", purpose="Object")

    editor.add_form_input(
        "document",
        "Заявка",
        "ФормаДокумента",
        "Комментарий",
        data_path="Объект.Комментарий",
        title="Комментарий",
    )
    editor.add_form_command(
        "document",
        "Заявка",
        "ФормаДокумента",
        "Проверить",
        action="ПроверитьОбработка",
        default_button=True,
        handler_body="Сообщить(\"Проверено\");",
    )

    form = workspace.read_text("Documents/Заявка/Forms/ФормаДокумента/Ext/Form.xml")
    module = workspace.read_text("Documents/Заявка/Forms/ФормаДокумента/Ext/Form/Module.bsl")
    assert '<InputField name="Комментарий"' in form
    assert "<DataPath>Объект.Комментарий</DataPath>" in form
    assert '<Command name="Проверить"' in form
    assert "<Action>ПроверитьОбработка</Action>" in form
    assert '<Button name="Проверить"' in form
    assert "<DefaultButton>true</DefaultButton>" in form
    assert "Процедура ПроверитьОбработка(Команда)" in module
    assert 'Сообщить("Проверено");' in module


def test_create_information_register_record_form(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)
    editor = FormMetadataEditor(workspace)
    editor.create_information_register("Цены")

    editor.create_managed_form(
        "information_register",
        "Цены",
        "ФормаЗаписи",
        purpose="Record",
        set_default=True,
    )

    parent = workspace.read_text("InformationRegisters/Цены.xml")
    form = workspace.read_text("InformationRegisters/Цены/Forms/ФормаЗаписи/Ext/Form.xml")
    assert "<DefaultRecordForm>InformationRegister.Цены.Form.ФормаЗаписи</DefaultRecordForm>" in parent
    assert "cfg:InformationRegisterRecordManager.Цены" in form
