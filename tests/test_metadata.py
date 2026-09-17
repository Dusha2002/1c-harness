from pathlib import Path

from onec_harness.metadata import ConfigurationIndex


def test_metadata_index_finds_objects_modules_and_symbols(tmp_path: Path) -> None:
    catalog_xml = tmp_path / "Catalogs" / "Products.xml"
    module = tmp_path / "Catalogs" / "Products" / "Ext" / "ObjectModule.bsl"
    catalog_xml.parent.mkdir(parents=True)
    catalog_xml.write_text("<MetaDataObject />", encoding="utf-8")
    module.parent.mkdir(parents=True)
    module.write_text(
        "Процедура ПередЗаписью(Отказ)\nКонецПроцедуры\n\nФункция Цена()\nВозврат 0;\nКонецФункции",
        encoding="utf-8",
    )

    index = ConfigurationIndex.build(tmp_path)

    objects = index.find_objects("Products")
    assert len(objects) == 1
    assert objects[0].kind == "catalog"
    assert objects[0].name == "Products"
    assert objects[0].modules == ("Catalogs/Products/Ext/ObjectModule.bsl",)

    symbols = index.find_symbols("передзап")
    assert len(symbols) == 1
    assert symbols[0].name == "ПередЗаписью"
    assert symbols[0].line == 1
