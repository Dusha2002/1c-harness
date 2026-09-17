from __future__ import annotations

from typing import Any

from onec_harness.semantic import SemanticChange
from onec_harness.semantic_forms import FormMetadataEditor
from onec_harness.workspace import Workspace


SEMANTIC_TOOLS = frozenset(
    {
        "create_catalog",
        "create_document_meta",
        "create_enum",
        "add_enum_value",
        "add_attribute",
        "add_tabular_section",
        "create_information_register",
        "create_accumulation_register",
        "create_managed_form",
        "add_form_input",
        "add_form_command",
        "ensure_module",
    }
)


class SemanticToolExecutor:
    """Translate stable high-level agent actions to deterministic metadata edits."""

    def __init__(self, workspace: Workspace) -> None:
        self.editor = FormMetadataEditor(workspace)

    @staticmethod
    def _objects(value: Any, label: str) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise ValueError(f"{label} must be an array of objects")
        return value

    def execute(self, tool: str, args: dict[str, Any]) -> SemanticChange:
        if tool == "create_catalog":
            return self.editor.create_catalog(
                str(args.get("name", "")),
                synonym=str(args["synonym"]) if args.get("synonym") is not None else None,
                hierarchical=bool(args.get("hierarchical", False)),
            )
        if tool == "create_document_meta":
            return self.editor.create_document(
                str(args.get("name", "")),
                synonym=str(args["synonym"]) if args.get("synonym") is not None else None,
                posting=bool(args.get("posting", False)),
            )
        if tool == "create_enum":
            values = args.get("values", [])
            if not isinstance(values, list):
                raise ValueError("values must be an array")
            return self.editor.create_enum(
                str(args.get("name", "")),
                synonym=str(args["synonym"]) if args.get("synonym") is not None else None,
                values=values,
            )
        if tool == "add_enum_value":
            return self.editor.add_enum_value(
                str(args.get("enum_name", "")),
                str(args.get("name", "")),
                synonym=str(args["synonym"]) if args.get("synonym") is not None else None,
            )
        if tool == "add_attribute":
            return self.editor.add_attribute(
                str(args.get("kind", "")),
                str(args.get("object_name", "")),
                str(args.get("name", "")),
                value_type=str(args.get("value_type", "string")),
                synonym=str(args["synonym"]) if args.get("synonym") is not None else None,
                string_length=int(args.get("string_length", 100)),
                digits=int(args.get("digits", 15)),
                fraction_digits=int(args.get("fraction_digits", 2)),
            )
        if tool == "add_tabular_section":
            return self.editor.add_tabular_section(
                str(args.get("kind", "")),
                str(args.get("object_name", "")),
                str(args.get("name", "")),
                synonym=str(args["synonym"]) if args.get("synonym") is not None else None,
                columns=self._objects(args.get("columns"), "columns"),
            )
        if tool == "create_information_register":
            return self.editor.create_information_register(
                str(args.get("name", "")),
                synonym=str(args["synonym"]) if args.get("synonym") is not None else None,
                periodicity=str(args.get("periodicity", "Nonperiodical")),
                write_mode=str(args.get("write_mode", "Independent")),
                dimensions=self._objects(args.get("dimensions"), "dimensions"),
                resources=self._objects(args.get("resources"), "resources"),
                attributes=self._objects(args.get("attributes"), "attributes"),
                main_filter_on_period=bool(args.get("main_filter_on_period", False)),
            )
        if tool == "create_accumulation_register":
            return self.editor.create_accumulation_register(
                str(args.get("name", "")),
                synonym=str(args["synonym"]) if args.get("synonym") is not None else None,
                register_type=str(args.get("register_type", "Balance")),
                dimensions=self._objects(args.get("dimensions"), "dimensions"),
                resources=self._objects(args.get("resources"), "resources"),
                attributes=self._objects(args.get("attributes"), "attributes"),
                enable_totals_splitting=bool(args.get("enable_totals_splitting", True)),
            )
        if tool == "create_managed_form":
            return self.editor.create_managed_form(
                str(args.get("kind", "")),
                str(args.get("object_name", "")),
                str(args.get("name", "")),
                purpose=str(args.get("purpose", "Custom")),
                synonym=str(args["synonym"]) if args.get("synonym") is not None else None,
                set_default=bool(args.get("set_default", False)),
                module_content=str(args["module_content"]) if args.get("module_content") is not None else None,
            )
        if tool == "add_form_input":
            return self.editor.add_form_input(
                str(args.get("kind", "")),
                str(args.get("object_name", "")),
                str(args.get("form_name", "")),
                str(args.get("name", "")),
                data_path=str(args.get("data_path", "")),
                title=str(args["title"]) if args.get("title") is not None else None,
            )
        if tool == "add_form_command":
            return self.editor.add_form_command(
                str(args.get("kind", "")),
                str(args.get("object_name", "")),
                str(args.get("form_name", "")),
                str(args.get("name", "")),
                action=str(args["action"]) if args.get("action") is not None else None,
                title=str(args["title"]) if args.get("title") is not None else None,
                button=bool(args.get("button", True)),
                default_button=bool(args.get("default_button", False)),
                handler_body=str(args["handler_body"]) if args.get("handler_body") is not None else None,
            )
        if tool == "ensure_module":
            return self.editor.ensure_module(
                str(args.get("kind", "")),
                str(args.get("object_name", "")),
                module=str(args.get("module", "object")),
                content=str(args.get("content", "")),
            )
        raise ValueError(f"Unknown semantic tool: {tool}")
