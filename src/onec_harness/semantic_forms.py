from __future__ import annotations

import re
import uuid
from pathlib import Path
from xml.sax.saxutils import escape

from onec_harness.semantic import NAMESPACE_LINE, SemanticChange, SemanticMetadataError
from onec_harness.semantic_registers import RegisterMetadataEditor


FORM_NAMESPACES = (
    'xmlns="http://v8.1c.ru/8.3/xcf/logform" '
    'xmlns:app="http://v8.1c.ru/8.2/managed-application/core" '
    'xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config" '
    'xmlns:dcscor="http://v8.1c.ru/8.1/data-composition-system/core" '
    'xmlns:dcssch="http://v8.1c.ru/8.1/data-composition-system/schema" '
    'xmlns:dcsset="http://v8.1c.ru/8.1/data-composition-system/settings" '
    'xmlns:ent="http://v8.1c.ru/8.1/data/enterprise" '
    'xmlns:lf="http://v8.1c.ru/8.2/managed-application/logform" '
    'xmlns:style="http://v8.1c.ru/8.1/data/ui/style" '
    'xmlns:sys="http://v8.1c.ru/8.1/data/ui/fonts/system" '
    'xmlns:v8="http://v8.1c.ru/8.1/data/core" '
    'xmlns:v8ui="http://v8.1c.ru/8.1/data/ui" '
    'xmlns:web="http://v8.1c.ru/8.1/data/ui/colors/web" '
    'xmlns:win="http://v8.1c.ru/8.1/data/ui/colors/windows" '
    'xmlns:xr="http://v8.1c.ru/8.3/xcf/readable" '
    'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
)

FORM_MODULE_SKELETON = """#Область ОбработчикиСобытийФормы

#КонецОбласти

#Область ОбработчикиСобытийЭлементовФормы

#КонецОбласти

#Область ОбработчикиКомандФормы

#КонецОбласти
"""


class FormMetadataEditor(RegisterMetadataEditor):
    """High-level, snapshotted operations over managed forms."""

    _FORM_RULES: dict[str, dict[str, object]] = {
        "catalog": {
            "folder": "Catalogs",
            "prefix": "Catalog",
            "purposes": {
                "Object": ("Объект", "CatalogObject", None, True, "DefaultObjectForm"),
                "Folder": ("Объект", "CatalogObject", None, True, "DefaultFolderForm"),
                "List": ("Список", "DynamicList", "Catalog", False, "DefaultListForm"),
                "Choice": ("Список", "DynamicList", "Catalog", False, "DefaultChoiceForm"),
                "FolderChoice": ("Список", "DynamicList", "Catalog", False, "DefaultFolderChoiceForm"),
                "Custom": (None, None, None, False, None),
            },
        },
        "document": {
            "folder": "Documents",
            "prefix": "Document",
            "purposes": {
                "Object": ("Объект", "DocumentObject", None, True, "DefaultObjectForm"),
                "List": ("Список", "DynamicList", "Document", False, "DefaultListForm"),
                "Choice": ("Список", "DynamicList", "Document", False, "DefaultChoiceForm"),
                "Custom": (None, None, None, False, None),
            },
        },
        "enum": {
            "folder": "Enums",
            "prefix": "Enum",
            "purposes": {
                "List": ("Список", "DynamicList", "Enum", False, "DefaultListForm"),
                "Choice": ("Список", "DynamicList", "Enum", False, "DefaultChoiceForm"),
                "Custom": (None, None, None, False, None),
            },
        },
        "information_register": {
            "folder": "InformationRegisters",
            "prefix": "InformationRegister",
            "purposes": {
                "Record": ("Запись", "InformationRegisterRecordManager", None, True, "DefaultRecordForm"),
                "List": ("Список", "DynamicList", "InformationRegister", False, "DefaultListForm"),
                "RecordSet": ("НаборЗаписей", "InformationRegisterRecordSet", None, True, None),
                "Custom": (None, None, None, False, None),
            },
        },
        "accumulation_register": {
            "folder": "AccumulationRegisters",
            "prefix": "AccumulationRegister",
            "purposes": {
                "List": ("Список", "DynamicList", "AccumulationRegister", False, "DefaultListForm"),
                "RecordSet": ("НаборЗаписей", "AccumulationRegisterRecordSet", None, True, None),
                "Custom": (None, None, None, False, None),
            },
        },
    }

    _PURPOSE_ALIASES = {
        "object": "Object", "объект": "Object", "элемент": "Object",
        "folder": "Folder", "группа": "Folder",
        "list": "List", "список": "List",
        "choice": "Choice", "выбор": "Choice",
        "folderchoice": "FolderChoice", "выборгруппы": "FolderChoice",
        "record": "Record", "запись": "Record",
        "recordset": "RecordSet", "наборзаписей": "RecordSet",
        "custom": "Custom", "произвольная": "Custom",
    }

    @classmethod
    def _form_rule(cls, kind: str, purpose: str) -> tuple[str, str, str, tuple[object, ...]]:
        normalized_kind = kind.strip().lower().replace("-", "_")
        rule = cls._FORM_RULES.get(normalized_kind)
        if rule is None:
            raise SemanticMetadataError(f"Managed forms are not supported for metadata kind: {kind!r}")
        compact = re.sub(r"[\s_-]", "", purpose).casefold() or "custom"
        normalized_purpose = cls._PURPOSE_ALIASES.get(compact)
        if normalized_purpose is None:
            normalized_purpose = next(
                (name for name in ("Object", "Folder", "List", "Choice", "FolderChoice", "Record", "RecordSet", "Custom")
                 if name.casefold() == compact),
                None,
            )
        purposes = rule["purposes"]
        if normalized_purpose is None or not isinstance(purposes, dict) or normalized_purpose not in purposes:
            allowed = ", ".join(purposes) if isinstance(purposes, dict) else ""
            raise SemanticMetadataError(f"Unsupported form purpose {purpose!r}; allowed: {allowed}")
        purpose_rule = purposes[normalized_purpose]
        if not isinstance(purpose_rule, tuple):
            raise SemanticMetadataError("Invalid form rule")
        return normalized_kind, str(rule["folder"]), str(rule["prefix"]), purpose_rule

    @staticmethod
    def _descriptor_xml(name: str, synonym: str, version: str) -> str:
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject {NAMESPACE_LINE} version="{version}">
\t<Form uuid="{uuid.uuid4()}">
\t\t<Properties>
\t\t\t<Name>{name}</Name>
\t\t\t<Synonym><v8:item><v8:lang>ru</v8:lang><v8:content>{escape(synonym)}</v8:content></v8:item></Synonym>
\t\t\t<Comment/><FormType>Managed</FormType><IncludeHelpInContents>false</IncludeHelpInContents>
\t\t\t<UsePurposes>
\t\t\t\t<v8:Value xsi:type="app:ApplicationUsePurpose">PlatformApplication</v8:Value>
\t\t\t\t<v8:Value xsi:type="app:ApplicationUsePurpose">MobilePlatformApplication</v8:Value>
\t\t\t</UsePurposes>
\t\t</Properties>
\t</Form>
</MetaDataObject>
'''

    @staticmethod
    def _main_attribute(object_name: str, rule: tuple[object, ...]) -> str:
        attr_name, attr_type, table_kind, saved_data, _ = rule
        if attr_name is None or attr_type is None:
            return ""
        if attr_type == "DynamicList":
            type_xml = "cfg:DynamicList"
            tail = (
                '\n\t\t\t<Settings xsi:type="DynamicList">'
                f"\n\t\t\t\t<MainTable>{table_kind}.{object_name}</MainTable>"
                "\n\t\t\t</Settings>"
            )
        else:
            type_xml = f"cfg:{attr_type}.{object_name}"
            tail = "\n\t\t\t<SavedData>true</SavedData>" if saved_data else ""
        return f'''\n\t<Attributes>
\t\t<Attribute name="{attr_name}" id="1">
\t\t\t<Type><v8:Type>{type_xml}</v8:Type></Type>
\t\t\t<MainAttribute>true</MainAttribute>{tail}
\t\t</Attribute>
\t</Attributes>'''

    @classmethod
    def _form_xml(cls, object_name: str, synonym: str, version: str, rule: tuple[object, ...]) -> str:
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<Form {FORM_NAMESPACES} version="{version}">
\t<Title><v8:item><v8:lang>ru</v8:lang><v8:content>{escape(synonym)}</v8:content></v8:item></Title>
\t<AutoTitle>false</AutoTitle>
\t<AutoCommandBar name="ФормаКоманднаяПанель" id="-1"><Autofill>true</Autofill></AutoCommandBar>
\t<ChildItems/>{cls._main_attribute(object_name, rule)}
</Form>
'''

    @staticmethod
    def _insert_child_object(text: str, entry: str) -> str:
        if entry in text:
            raise SemanticMetadataError(f"Child object already registered: {entry}")
        self_closing = list(re.finditer(r"(?m)^(?P<i>[ \t]*)<ChildObjects\s*/>\s*$", text))
        if self_closing:
            match = self_closing[-1]
            indent = match.group("i")
            block = f"{indent}<ChildObjects>\n{indent}\t{entry}\n{indent}</ChildObjects>"
            return text[:match.start()] + block + text[match.end():]
        closings = list(re.finditer(r"(?m)^(?P<i>[ \t]*)</ChildObjects>\s*$", text))
        if not closings:
            raise SemanticMetadataError("Metadata object has no ChildObjects section")
        closing = closings[-1]
        indent = closing.group("i")
        return text[:closing.start()] + f"{indent}\t{entry}\n" + text[closing.start():]

    @staticmethod
    def _set_property(text: str, name: str, value: str) -> str:
        match = re.search(rf"<{re.escape(name)}(?:\s*/>|>.*?</{re.escape(name)}>)", text, re.S)
        if match is None:
            raise SemanticMetadataError(f"Metadata object has no {name} property")
        replacement = f"<{name}>{escape(value)}</{name}>"
        return text[:match.start()] + replacement + text[match.end():]

    @staticmethod
    def _form_paths(folder: str, object_name: str, form_name: str) -> tuple[Path, Path, Path]:
        descriptor = Path(folder) / object_name / "Forms" / f"{form_name}.xml"
        base = Path(folder) / object_name / "Forms" / form_name / "Ext"
        return descriptor, base / "Form.xml", base / "Form" / "Module.bsl"

    def create_managed_form(
        self,
        kind: str,
        object_name: str,
        form_name: str,
        *,
        purpose: str = "Custom",
        synonym: str | None = None,
        set_default: bool = False,
        module_content: str | None = None,
    ) -> SemanticChange:
        normalized_kind, folder, prefix, rule = self._form_rule(kind, purpose)
        object_name = self._identifier(object_name, "object name")
        form_name = self._identifier(form_name, "form name")
        object_path = Path(folder) / f"{object_name}.xml"
        object_file = self.workspace.resolve(object_path)
        if not object_file.exists():
            raise SemanticMetadataError(f"Metadata object was not found: {normalized_kind}.{object_name}")
        descriptor, form_path, module_path = self._form_paths(folder, object_name, form_name)
        if self.workspace.resolve(descriptor).exists():
            raise SemanticMetadataError(f"Form already exists: {prefix}.{object_name}.Form.{form_name}")
        default_property = rule[4]
        if set_default and default_property is None:
            raise SemanticMetadataError("This form purpose cannot be assigned as the default form")

        snapshot = self.snapshots.create([object_path, descriptor, form_path, module_path])
        display = synonym or form_name
        version = self._format_version()
        self.workspace.write_text(descriptor, self._descriptor_xml(form_name, display, version))
        self.workspace.write_text(form_path, self._form_xml(object_name, display, version, rule))
        self.workspace.write_text(module_path, module_content if module_content is not None else FORM_MODULE_SKELETON)
        parent = self._insert_child_object(object_file.read_text(encoding="utf-8-sig"), f"<Form>{form_name}</Form>")
        if set_default:
            parent = self._set_property(parent, str(default_property), f"{prefix}.{object_name}.Form.{form_name}")
        object_file.write_text(parent, encoding="utf-8")
        return self._change(snapshot, f"Created managed form {prefix}.{object_name}.Form.{form_name}")

    @staticmethod
    def _next_ids(text: str, count: int) -> list[int]:
        ids = [int(value) for value in re.findall(r'\bid="(-?\d+)"', text)]
        first = max((value for value in ids if value >= 0), default=0) + 1
        return list(range(first, first + count))

    @staticmethod
    def _root_child_items(text: str) -> tuple[int, int, str]:
        bar = re.search(r"<AutoCommandBar\b", text)
        if bar is None:
            raise SemanticMetadataError("Form.xml has no AutoCommandBar")
        bar_end = text.find("</AutoCommandBar>", bar.start())
        if bar_end >= 0:
            cursor = bar_end + len("</AutoCommandBar>")
        else:
            cursor = text.find("/>", bar.start()) + 2
        match = re.search(r"<ChildItems\b[^>]*?/?>", text[cursor:])
        if match is None:
            raise SemanticMetadataError("Form.xml has no root ChildItems")
        start, open_end = cursor + match.start(), cursor + match.end()
        opening = text[start:open_end]
        if opening.rstrip().endswith("/>"):
            return start, open_end, opening
        tokens = re.compile(r"<ChildItems\b[^>]*?/?>|</ChildItems>")
        depth = 0
        for item in tokens.finditer(text, start):
            raw = item.group(0)
            if raw.startswith("</"):
                depth -= 1
                if depth == 0:
                    return start, item.end(), text[start:item.end()]
            elif not raw.rstrip().endswith("/>"):
                depth += 1
        raise SemanticMetadataError("Malformed ChildItems")

    @classmethod
    def _append_child_item(cls, text: str, snippet: str) -> str:
        start, end, block = cls._root_child_items(text)
        line_start = text.rfind("\n", 0, start) + 1
        indent = text[line_start:start]
        child_indent = indent + "\t"
        rendered = "\n".join(child_indent + line for line in snippet.splitlines())
        if block.rstrip().endswith("/>"):
            return text[:start] + f"<ChildItems>\n{rendered}\n{indent}</ChildItems>" + text[end:]
        closing = start + block.rfind("</ChildItems>")
        closing_line = text.rfind("\n", start, closing) + 1
        return text[:closing_line] + rendered + "\n" + text[closing_line:]

    def add_form_input(
        self,
        kind: str,
        object_name: str,
        form_name: str,
        name: str,
        *,
        data_path: str,
        title: str | None = None,
    ) -> SemanticChange:
        _, folder, _, _ = self._form_rule(kind, "Custom")
        object_name = self._identifier(object_name, "object name")
        form_name = self._identifier(form_name, "form name")
        name = self._identifier(name, "form element name")
        if not data_path.strip():
            raise SemanticMetadataError("data_path is required")
        _, form_path, _ = self._form_paths(folder, object_name, form_name)
        target = self.workspace.resolve(form_path)
        if not target.exists():
            raise SemanticMetadataError(f"Form definition was not found: {form_path}")
        text = target.read_text(encoding="utf-8-sig")
        if re.search(rf'\bname="{re.escape(name)}"', text):
            raise SemanticMetadataError(f"Form element already exists: {name}")
        field_id, menu_id, tooltip_id = self._next_ids(text, 3)
        snippet = f'''<InputField name="{name}" id="{field_id}">
\t<DataPath>{escape(data_path.strip())}</DataPath>
\t<Title><v8:item><v8:lang>ru</v8:lang><v8:content>{escape(title or name)}</v8:content></v8:item></Title>
\t<ContextMenu name="{name}КонтекстноеМеню" id="{menu_id}"/>
\t<ExtendedTooltip name="{name}РасширеннаяПодсказка" id="{tooltip_id}"/>
</InputField>'''
        snapshot = self.snapshots.create([form_path])
        target.write_text(self._append_child_item(text, snippet), encoding="utf-8")
        return self._change(snapshot, f"Added input field {name} to form {form_name}")

    @staticmethod
    def _append_command(text: str, snippet: str) -> str:
        closings = list(re.finditer(r"(?m)^(?P<i>[ \t]*)</Commands>\s*$", text))
        if closings:
            closing = closings[-1]
            indent = closing.group("i")
            return text[:closing.start()] + f"{indent}\t{snippet}\n" + text[closing.start():]
        form_close = text.rfind("</Form>")
        if form_close < 0:
            raise SemanticMetadataError("Malformed Form.xml")
        block = f"\t<Commands>\n\t\t{snippet}\n\t</Commands>\n"
        return text[:form_close] + block + text[form_close:]

    @staticmethod
    def _append_handler(module: str, action: str, body: str | None) -> str:
        if re.search(rf"(?im)^\s*(?:Процедура|Функция)\s+{re.escape(action)}\s*\(", module):
            raise SemanticMetadataError(f"Form handler already exists: {action}")
        body_text = body.strip() if body and body.strip() else "// TODO: implement"
        lines = "\n".join(f"\t{line}" for line in body_text.splitlines())
        suffix = "" if not module else ("" if module.endswith("\n") else "\n")
        return module + suffix + f"\n&НаКлиенте\nПроцедура {action}(Команда)\n{lines}\nКонецПроцедуры\n"

    def add_form_command(
        self,
        kind: str,
        object_name: str,
        form_name: str,
        name: str,
        *,
        action: str | None = None,
        title: str | None = None,
        button: bool = True,
        default_button: bool = False,
        handler_body: str | None = None,
    ) -> SemanticChange:
        _, folder, _, _ = self._form_rule(kind, "Custom")
        object_name = self._identifier(object_name, "object name")
        form_name = self._identifier(form_name, "form name")
        name = self._identifier(name, "form command name")
        action_name = self._identifier(action or f"{name}Обработка", "form action name")
        _, form_path, module_path = self._form_paths(folder, object_name, form_name)
        target, module_target = self.workspace.resolve(form_path), self.workspace.resolve(module_path)
        if not target.exists():
            raise SemanticMetadataError(f"Form definition was not found: {form_path}")
        text = target.read_text(encoding="utf-8-sig")
        if re.search(rf'<Command\b[^>]*\bname="{re.escape(name)}"', text):
            raise SemanticMetadataError(f"Form command already exists: {name}")
        snapshot = self.snapshots.create([form_path, module_path])
        command_id = self._next_ids(text, 1)[0]
        updated = self._append_command(text, f'<Command name="{name}" id="{command_id}"><Action>{action_name}</Action></Command>')
        if button:
            button_id, tooltip_id = self._next_ids(updated, 2)
            title_xml = (
                f'\n\t<Title><v8:item><v8:lang>ru</v8:lang><v8:content>{escape(title)}</v8:content></v8:item></Title>'
                if title is not None else ""
            )
            default_xml = "\n\t<DefaultButton>true</DefaultButton>" if default_button else ""
            snippet = f'''<Button name="{name}" id="{button_id}">
\t<CommandName>Form.Command.{name}</CommandName>{title_xml}{default_xml}
\t<ExtendedTooltip name="{name}РасширеннаяПодсказка" id="{tooltip_id}"/>
</Button>'''
            updated = self._append_child_item(updated, snippet)
        target.write_text(updated, encoding="utf-8")
        module = module_target.read_text(encoding="utf-8-sig") if module_target.exists() else ""
        module_target.parent.mkdir(parents=True, exist_ok=True)
        module_target.write_text(self._append_handler(module, action_name, handler_body), encoding="utf-8")
        return self._change(snapshot, f"Added command {name} to form {form_name}")
