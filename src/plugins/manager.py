"""Plugin manager module.

Responsibility: read plugin feature flags from configuration, trigger
enabled plugin instances when requested by the Decision Engine, and
aggregate their evidence. This module must NEVER call the Detector or
Retriever modules directly (see 02_MODULE_SPECIFICATION.md, Section 6.1).

Plugin selection policy:
    - If `decision.trigger_reasons` includes "uncertain" or "ambiguous",
      every enabled plugin runs (broad evidence gathering — the winner
      itself is in doubt).
    - If `decision.trigger_reasons` is exactly `{"force"}` (no
      uncertain/ambiguous), only the plugins listed in
      `decision.forced_plugins` run (a specific product mandates specific
      evidence, e.g. barcode confirmation — no need to also run OCR).
    - A plugin's own `enabled` flag is always the final gate: force rules
      can request a plugin, but can never override it being disabled in
      configuration.
"""

from __future__ import annotations

from src.core.config import AppConfig
from src.core.logger import get_logger
from src.core.utils import timer
from src.models.models import CropImage, DecisionResult, PluginResult
from src.plugins.barcode import BarcodePlugin
from src.plugins.color import ColorPlugin
from src.plugins.ocr import OcrPlugin

logger = get_logger(__name__)


class PluginManager:
    """Triggers on-demand plugins and aggregates their evidence."""

    def __init__(self, config: AppConfig) -> None:
        """Initializes the PluginManager and its underlying plugins once.

        Args:
            config: Fully validated application configuration.
        """
        self._enabled = config.plugins.enabled
        self._plugins = [
            OcrPlugin(config),
            ColorPlugin(config),
            BarcodePlugin(config),
        ]
        logger.info(
            "PluginManager initialized (enabled=%s, registered_plugins=%s)",
            self._enabled,
            [plugin.name for plugin in self._plugins],
        )

    def is_enabled(self) -> bool:
        """Returns whether the plugin subsystem is enabled via configuration."""
        return self._enabled

    def run_plugins(self, crop: CropImage, decision: DecisionResult) -> PluginResult:
        """Executes the plugins required by the Decision Engine's trigger policy.

        Args:
            crop: The cropped product image to gather evidence for.
            decision: The Decision Engine's result signaling whether (and
                why) plugin evidence is needed.

        Returns:
            A PluginResult aggregating evidence from every plugin that
            actually executed. Returns an empty PluginResult if no
            plugins ran.
        """
        with timer() as elapsed:
            executed_plugins: list[str] = []
            evidence: dict[str, dict] = {}

            if self._enabled and decision.needs_plugin:
                only_forced = "force" in decision.trigger_reasons

                for plugin in self._plugins:
                    if not plugin.is_enabled():
                        continue
                    if only_forced and plugin.name not in decision.forced_plugins:
                        continue

                    plugin_output = plugin.run(crop)

                    executed_plugins.append(plugin.name)
                    evidence[plugin.name] = plugin_output
            else:
                logger.debug(
                    "Skipping plugin execution for crop_id='%s' "
                    "(manager_enabled=%s, needs_plugin=%s)",
                    crop.crop_id,
                    self._enabled,
                    decision.needs_plugin,
                )

        if executed_plugins:
            logger.info(
                "PluginManager ran plugins=%s for crop_id='%s' (trigger_reasons=%s) (%.2f ms)",
                executed_plugins,
                crop.crop_id,
                sorted(decision.trigger_reasons),
                elapsed["elapsed_ms"],
            )

        return PluginResult(
            crop_id=crop.crop_id,
            executed_plugins=executed_plugins,
            evidence=evidence,
            processing_time_ms=elapsed["elapsed_ms"],
        )
