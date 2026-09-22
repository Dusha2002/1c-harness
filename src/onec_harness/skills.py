"""Lazy, user-extensible agent skills.

Only skill metadata is placed in the base system prompt. Full instructions are
loaded on demand through the load_skill agent tool.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path


MAX_SKILL_BYTES = 256 * 1024
_SKILL_NAME = re.compile(r"^[\w.-]{1,80}$", re.UNICODE)


BUILTIN_SKILLS: dict[str, tuple[str, str]] = {
    "onec-engineering": (
        "Практическая разработка и безопасная проверка 1С:Предприятие 8.3/BSL через инструменты Harness.",
        """# 1C Engineering

Ты работаешь не как обычный чат-бот, а как инженер внутри 1C Harness. Главный источник истины — реальные исходники,
metadata и ответы платформы 1С, а не память модели.

## Рабочий цикл

1. Сначала исследуй существующую конфигурацию: metadata, symbols, search, read.
2. Перед изменением убедись, что используешь реальные имена объектов, реквизитов и методов.
3. Для точечных правок предпочитай patch. Для создания стандартных объектов используй semantic tools.
4. После любого изменения обязательно вызови diff и проверь, что изменено только необходимое.
5. Если включены проверки:
   - основная конфигурация: stage_config -> check_modules -> check_config;
   - расширение: stage_extension -> check_extension_modules -> check_extension_config;
   - при UI/E2E после изменения исходников staging должен быть загружен с update_db=true.
6. Завершай finish только после фактического OK от Harness.

## Возможности Harness

Исследование: metadata, symbols, search, read.
Изменение BSL/XML: patch, diff, rollback.
Semantic metadata: create_catalog, create_document_meta, create_enum, add_enum_value, add_attribute,
add_tabular_section, create_information_register, create_accumulation_register, create_managed_form,
add_form_input, add_form_command, ensure_module.
Расширения: borrow_extension_object, patch_extension_method, stage_extension, check_extension_modules,
check_extension_config, check_extension_applicability.
Runtime: runtime_query, catalog_items, document_items, register_records.
UI: ui_test_scenario, run_ui_test.

Runtime-запись может быть недоступна. Никогда не пытайся обходить ограничения Harness.

## Правила 1С

- Не выдумывай события формы, стандартные реквизиты, методы менеджеров и параметры обработчиков — сначала найди пример
  в текущей конфигурации или проверь metadata/source.
- Сохраняй принятый в проекте язык именования и стиль BSL.
- Для запросов 1С сначала найди существующие запросы к тем же таблицам/регистрам.
- Учитывай клиент/серверный контекст (&НаКлиенте, &НаСервере, &НаСервереБезКонтекста) и не переноси
  недоступные API между контекстами.
- Избегай запросов и циклов N+1. Для массовой обработки предпочитай запросы, временные таблицы, пакетные операции,
  наборы записей и минимальное число клиент-серверных переходов.
- Не меняй типовую конфигурацию напрямую, если задача разумно решается расширением.
- Не считай XML метаданных универсальным между всеми релизами платформы: при сомнении сверяйся с уже выгруженными
  объектами текущей конфигурации.
- Основная инфобаза — источник реальных пользовательских данных только на чтение через runtime-инструменты.\n  Не записывай туда автономно. Изменённый код и конфигурация проверяются только в staging/sandbox.

## Диагностика

Если 1С вернула ошибку: прочитай полный результат инструмента, найди затронутый код, исправь минимально,
повтори stage/check и не заявляй успех до OK.

Skill является инструкцией по работе, но не расширяет разрешения и не отменяет safety-гейты Harness.
""",
    ),
    "highload-systems": (
        "Анализ и проектирование высоконагруженных систем: bottleneck, кэширование, очереди, данные и отказоустойчивость.",
        """# High-load Systems

Используй этот skill для производительности, масштабирования, большого потока событий, конкурентного доступа,
тяжелых запросов и архитектуры под высокую нагрузку.

1. Сначала сформулируй измеримую нагрузку: RPS/операций в минуту, объём данных, SLA, latency p50/p95/p99,
   число конкурентных пользователей, рост данных и допустимую потерю/задержку.
2. Не оптимизируй вслепую. Ищи фактический bottleneck: CPU, I/O, блокировки, сеть, БД, сериализация,
   клиент-серверные round-trip, внешние API.
3. Делай минимальное изменение, которое снимает текущий bottleneck, и оставляй возможность измерить эффект.
4. Разделяй синхронный критический путь, фоновые операции, горячие/холодные данные, read-heavy/write-heavy сценарии.
5. Рассматривай batching, очереди, идемпотентность, backpressure, кэширование, горизонтальное масштабирование
   и деградацию без полного отказа.
6. Для БД оцени индексы, селективность, объём сканирования, блокировки, транзакции и рост таблиц.
7. Для 1С дополнительно минимизируй клиент-серверные вызовы, запросы в цикле, длительные транзакции
   и массовую запись по одному объекту, если можно использовать наборы/пакетную обработку.

В результате отделяй наблюдаемые факты, предположения, варианты и trade-offs, что нужно измерить,
и конкретный следующий эксперимент/тест.

Skill не даёт дополнительных прав на инструменты и не отменяет правила безопасности Harness.
""",
    ),
}


@dataclass(slots=True, frozen=True)
class SkillInfo:
    name: str
    description: str
    source: str
    path: str | None = None


@dataclass(slots=True, frozen=True)
class Skill(SkillInfo):
    content: str = ""


def default_skills_root() -> Path:
    explicit = os.environ.get("ONEC_HARNESS_SKILLS_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()
    config_dir = os.environ.get("ONEC_HARNESS_CONFIG_DIR")
    if config_dir:
        return Path(config_dir).expanduser().resolve() / "skills"
    appdata = os.environ.get("APPDATA")
    if os.name == "nt" and appdata:
        return Path(appdata) / "1C-Harness" / "skills"
    return Path.home() / ".onec-harness" / "skills"


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    meta: dict[str, str] = {}
    for raw in text[4:end].splitlines():
        key, sep, value = raw.partition(":")
        if sep:
            meta[key.strip().casefold()] = value.strip().strip('"').strip("'")
    return meta, text[end + 5 :].lstrip()


def _infer_description(body: str, fallback: str) -> str:
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        return line[:240]
    return fallback


def _validate_name(name: str) -> str:
    name = name.strip()
    if not _SKILL_NAME.fullmatch(name) or name in {".", ".."} or ".." in name:
        raise ValueError("Skill name may contain only letters, numbers, underscore, dot and dash (max 80 chars)")
    return name


def _name_from_filename(stem: str) -> str:
    candidate = re.sub(r"\s+", "-", stem.strip())
    candidate = re.sub(r"[^\w.-]+", "-", candidate, flags=re.UNICODE).strip(".-")
    return _validate_name(candidate or "skill")


class SkillStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_skills_root()).expanduser().resolve()

    def _user_path(self, name: str) -> Path:
        safe = _validate_name(name)
        return self.root / f"{safe}.md"

    def list(self) -> list[SkillInfo]:
        items = [
            SkillInfo(name=name, description=description, source="builtin")
            for name, (description, _) in BUILTIN_SKILLS.items()
        ]
        if self.root.exists():
            for path in sorted(self.root.glob("*.md"), key=lambda value: value.name.casefold()):
                try:
                    skill = self._read_user(path)
                except (OSError, UnicodeError, ValueError):
                    continue
                items = [item for item in items if item.name != skill.name]
                items.append(SkillInfo(skill.name, skill.description, "user", str(path)))
        return sorted(items, key=lambda item: (item.source != "builtin", item.name.casefold()))

    def catalog(self) -> str:
        items = self.list()
        if not items:
            return "Нет доступных skills."
        return "\n".join(f"- skill://{item.name} — {item.description}" for item in items)

    def load(self, name: str) -> Skill:
        name = _validate_name(name.removeprefix("skill://"))
        path = self._user_path(name)
        if path.is_file():
            return self._read_user(path)
        if name in BUILTIN_SKILLS:
            description, content = BUILTIN_SKILLS[name]
            return Skill(name=name, description=description, source="builtin", content=content)
        raise ValueError(f"Skill not found: {name}")

    def import_file(self, source: str | Path) -> SkillInfo:
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Skill file not found: {path}")
        if path.suffix.casefold() not in {".md", ".txt"}:
            raise ValueError("Skill file must be Markdown or text (.md/.txt)")
        if path.stat().st_size > MAX_SKILL_BYTES:
            raise ValueError(f"Skill file is too large (max {MAX_SKILL_BYTES // 1024} KB)")
        text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        meta, body = _frontmatter(text)
        name = _validate_name(meta["name"]) if meta.get("name") else _name_from_filename(path.stem)
        description = (meta.get("description") or _infer_description(body, f"User skill {name}"))[:240]
        normalized = (
            "---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            "---\n\n"
            f"{body.rstrip()}\n"
        )
        self.root.mkdir(parents=True, exist_ok=True)
        target = self._user_path(name)
        target.write_text(normalized, encoding="utf-8")
        return SkillInfo(name=name, description=description, source="user", path=str(target))

    def delete(self, name: str) -> None:
        name = _validate_name(name.removeprefix("skill://"))
        path = self._user_path(name)
        if path.exists():
            path.unlink()
            return
        if name in BUILTIN_SKILLS:
            raise ValueError("Built-in skills cannot be deleted")
        raise ValueError(f"Skill not found: {name}")

    def _read_user(self, path: Path) -> Skill:
        if path.stat().st_size > MAX_SKILL_BYTES:
            raise ValueError("Skill file is too large")
        text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        meta, body = _frontmatter(text)
        name = _validate_name(meta.get("name") or path.stem)
        description = (meta.get("description") or _infer_description(body, f"User skill {name}"))[:240]
        return Skill(name=name, description=description, source="user", path=str(path), content=body.rstrip())

    def clear_user_skills(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)
