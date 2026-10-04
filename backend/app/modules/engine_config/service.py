"""The advanced settings: engine-config overrides an admin applies with the advanced password.

Apply = check each value against the registry -> validate the whole engine config -> rebuild the
pipeline on the worker thread (between two photos; the recognizer puts the old settings back if
that fails) -> store the overrides and their change-log entries. A failed reload changes nothing.
"""

import asyncio
import json
import logging
from typing import Any

from app.core.config import CoreSettings
from app.core.errors import AppError, Conflict, Invalid
from app.modules.audit.ports import ChangeEntry
from app.modules.audit.schemas import RevertIn
from app.modules.auth.ports import CurrentUser
from app.modules.engine_config.registry import BY_KEY, REGISTRY, get_path, normalize_value
from app.modules.engine_config.repository import EngineConfigRepository
from app.modules.engine_config.schemas import ApplyIn, ConfigOut
from app.modules.pos_settings.service import SettingsService
from app.modules.recognition.worker import RecognitionWorker

logger = logging.getLogger("app.engine_config")


class EngineConfigService:
    def __init__(
        self,
        repo: EngineConfigRepository,
        pos_settings: SettingsService,
        worker: RecognitionWorker,
        settings: CoreSettings,
    ):
        self.repo, self.pos_settings, self.worker, self.settings = repo, pos_settings, worker, settings

    def _raw(self) -> dict[str, Any]:
        from engine.core.config import read_raw_config

        raw: dict[str, Any] = read_raw_config(self.settings.pipeline_config)
        return raw

    def _built(self, overrides: dict[str, Any]) -> Any:
        """The engine's validated config with these overrides; ValueError when it does not hold."""
        from engine.core.config import build_config

        return build_config(self.settings.pipeline_config, overrides)

    async def view(self, applied: bool | None = None) -> ConfigOut:
        overrides = await self.repo.overrides()
        raw = await asyncio.to_thread(self._raw)
        try:
            effective, error = await asyncio.to_thread(self._built, overrides), None
        except ValueError as exc:  # an old override the current YAML rejects: shown, not deleted
            effective, error = None, str(exc)
        items = [
            {
                **entry.to_dict(),
                "default": get_path(raw, entry.key),
                "overridden": entry.key in overrides,
                "value": (
                    get_path(effective, entry.key)
                    if effective is not None
                    else overrides.get(entry.key, get_path(raw, entry.key))
                ),
            }
            for entry in REGISTRY
        ]
        return ConfigOut(
            items=items,
            reloading=self.worker.reloading,
            config_error=error,
            advanced_password_set=await self.pos_settings.advanced_password_set(),
            pipeline_config=self.settings.pipeline_config.name,
            applied=applied,
        )

    async def apply(self, current: CurrentUser, body: ApplyIn) -> ConfigOut:
        if body.confirm is not True:
            raise AppError(
                "Cần xác nhận: thay đổi ảnh hưởng nhận diện và tạm dừng hệ thống 30–60 giây",
                code="CONFIRM_REQUIRED",
                status_code=422,
            )
        await self.pos_settings.verify_advanced_password(current, body.advanced_password)
        changes = body.changes
        if not isinstance(changes, dict) or not changes:
            raise Invalid("Không có thay đổi nào")
        old, raw = await self.repo.overrides(), await asyncio.to_thread(self._raw)
        new = dict(old)
        for key, value in changes.items():
            entry = BY_KEY.get(key)
            if entry is None:
                raise Invalid(f"Thiết lập không được phép sửa trên web: {key}")
            if value is None:  # back to the YAML's value
                new.pop(key, None)
                continue
            try:
                value = normalize_value(entry, value)
            except ValueError as exc:
                raise Invalid(str(exc)) from exc
            if value == get_path(raw, key):
                new.pop(key, None)
            else:
                new[key] = value
        if new == old:
            return await self.view(applied=False)
        try:
            await asyncio.to_thread(self._built, new)
        except ValueError as exc:
            raise AppError(str(exc), code="CONFIG_INVALID", status_code=422) from exc
        try:
            await self.worker.reload_pipeline(new)
        except AppError:
            raise
        except TimeoutError as exc:
            raise AppError(
                "Nạp lại pipeline quá lâu — xem log server, có thể cần khởi động lại",
                code="RELOAD_TIMEOUT",
                status_code=504,
            ) from exc
        except Exception as exc:
            logger.exception("applying engine settings failed")
            raise AppError(
                f"Không nạp được pipeline với thiết lập mới, đã quay về thiết lập cũ: {exc}",
                code="RELOAD_FAILED",
                status_code=500,
            ) from exc
        await self.repo.save(old, new, current.username, current.user_id)
        logger.info("engine settings applied", extra={"changes": {k: new.get(k) for k in changes}})
        return await self.view(applied=True)

    async def revert(self, current: CurrentUser, entry: ChangeEntry, body: RevertIn) -> None:
        """Change-log reverter for the `config` table: the advanced password again, and a reload."""
        overrides = await self.repo.overrides()
        stored = json.dumps(overrides[entry.field]) if entry.field in overrides else None
        if stored != entry.new:
            raise Conflict("Thiết lập đã được thay đổi sau lần sửa này, không thể hoàn tác", code="CHANGE_STALE")
        target = None if entry.old is None else json.loads(entry.old)
        await self.apply(
            current, ApplyIn(changes={entry.field: target}, confirm=True, advanced_password=body.advanced_password)
        )
