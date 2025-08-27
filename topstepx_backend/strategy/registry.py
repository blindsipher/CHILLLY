"""Strategy registry for discovering and instantiating trading strategies."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

import yaml

from topstepx_backend.strategy.base import Strategy


class StrategyRegistry:
    """
    Registry for discovering and instantiating trading strategies.

    Loads strategy configurations from YAML files and dynamically
    imports strategy classes from the strategy.examples package. It can also
    discover user supplied strategy plugins from a ``strategies/`` directory at
    the project root. This enables strategies to be dropped in without
    modifying the core package.
    """

    def __init__(self, config_path: Optional[str] = None, plugin_dir: str = "strategies"):
        """
        Initialize strategy registry.

        Args:
            config_path: Path to strategy configuration file
        """
        self.logger = logging.getLogger(__name__)
        self.config_path = (
            config_path or "topstepx_backend/strategy/configs/strategies.yaml"
        )
        self._strategy_classes: Dict[str, Type[Strategy]] = {}
        self._strategy_configs: List[Dict[str, Any]] = []
        # Directory containing drop-in strategy plugins
        self.plugin_path = Path(plugin_dir)

    def load_configurations(self) -> List[Dict[str, Any]]:
        """
        Load strategy configurations from YAML file.

        Returns:
            List of strategy configuration dictionaries
        """
        try:
            config_file = Path(self.config_path)
            if not config_file.exists():
                self.logger.warning(
                    f"Strategy config file not found: {self.config_path}"
                )
                return []

            with open(config_file, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f)

            if not config_data or "strategies" not in config_data:
                self.logger.warning("No 'strategies' section found in config file")
                return []

            strategies = config_data["strategies"]
            if not isinstance(strategies, list):
                self.logger.error("'strategies' section must be a list")
                return []

            self._strategy_configs = strategies
            self.logger.info(f"Loaded {len(strategies)} strategy configurations")

            return strategies

        except yaml.YAMLError as e:
            self.logger.error(f"YAML parsing error in {self.config_path}: {e}")
            return []
        except Exception as e:
            self.logger.error(f"Failed to load strategy configurations: {e}")
            return []

    def discover_strategy_classes(self) -> Dict[str, Type[Strategy]]:
        """Discover available strategy classes.

        This scans both the built in examples package as well as the optional
        ``strategies`` plugin directory. Discovered classes are cached on the
        registry instance.

        Returns:
            Dictionary mapping class paths to strategy classes
        """

        strategy_classes: Dict[str, Type[Strategy]] = {}

        try:
            strategy_classes.update(self._discover_example_classes())
            strategy_classes.update(self._discover_plugin_classes())
        except Exception as e:  # pragma: no cover - defensive
            self.logger.error(f"Failed to discover strategy classes: {e}")

        self._strategy_classes = strategy_classes
        self.logger.info(f"Discovered {len(strategy_classes)} strategy classes")
        return strategy_classes

    # ------------------------------------------------------------------
    # Discovery helpers
    # ------------------------------------------------------------------

    def _discover_example_classes(self) -> Dict[str, Type[Strategy]]:
        """Load strategy classes from the bundled examples package."""
        classes: Dict[str, Type[Strategy]] = {}

        examples_package = "topstepx_backend.strategy.examples"
        examples_path = Path(__file__).parent / "examples"
        if not examples_path.exists():
            self.logger.warning(f"Examples directory not found: {examples_path}")
            return classes

        for py_file in examples_path.glob("*.py"):
            if py_file.name.startswith("__"):
                continue
            module_name = py_file.stem
            full_module = f"{examples_package}.{module_name}"
            try:
                module = importlib.import_module(full_module)
                self._extract_strategy_classes(module, full_module, classes)
            except Exception as e:  # pragma: no cover - importlib errors
                self.logger.error(f"Failed to import {full_module}: {e}")

        return classes

    def _discover_plugin_classes(self) -> Dict[str, Type[Strategy]]:
        """Load strategy classes from the ``strategies`` plugin directory."""
        classes: Dict[str, Type[Strategy]] = {}
        if not self.plugin_path.exists():
            return classes

        for py_file in self.plugin_path.glob("*.py"):
            if py_file.name.startswith("__"):
                continue

            module_name = py_file.stem
            full_module = f"strategies.{module_name}"

            try:
                spec = importlib.util.spec_from_file_location(full_module, py_file)
                if not spec or not spec.loader:  # pragma: no cover - unlikely
                    continue
                module = importlib.util.module_from_spec(spec)
                sys.modules[full_module] = module
                spec.loader.exec_module(module)  # type: ignore[attr-defined]

                self._extract_strategy_classes(module, full_module, classes)
            except Exception as e:  # pragma: no cover - importlib errors
                self.logger.error(f"Failed to load plugin {full_module}: {e}")

        return classes

    def _extract_strategy_classes(
        self,
        module: Any,
        module_path: str,
        target: Dict[str, Type[Strategy]],
    ) -> None:
        """Extract Strategy subclasses from a module and add to mapping."""
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, Strategy)
                and attr is not Strategy
            ):
                class_path = f"{module_path}.{attr_name}"
                target[class_path] = attr
                self.logger.debug(f"Discovered strategy class: {class_path}")

    def create_strategy_instance(self, config: Dict[str, Any]) -> Optional[Strategy]:
        """
        Create a strategy instance from configuration.

        Args:
            config: Strategy configuration dictionary

        Returns:
            Strategy instance or None if creation failed
        """
        try:
            # Validate required fields
            required_fields = [
                "strategy_id",
                "class",
                "account_id",
                "contract_id",
                "timeframe",
            ]
            for field in required_fields:
                if field not in config:
                    self.logger.error(
                        f"Missing required field '{field}' in strategy config"
                    )
                    return None

            strategy_id = config["strategy_id"]
            class_path = config["class"]
            params = config.get("params", {})

            # Get strategy class
            if class_path not in self._strategy_classes:
                self.logger.error(f"Strategy class not found: {class_path}")
                return None

            strategy_class = self._strategy_classes[class_path]

            # Create instance
            strategy = strategy_class(strategy_id, params)

            # Validate parameters
            if not strategy.validate_params():
                self.logger.error(
                    f"Parameter validation failed for strategy: {strategy_id}"
                )
                return None

            self.logger.info(f"Created strategy instance: {strategy_id}")
            return strategy

        except Exception as e:
            self.logger.error(f"Failed to create strategy instance: {e}")
            return None

    def create_all_strategies(self) -> List[Strategy]:
        """
        Create all strategy instances from loaded configurations.

        Returns:
            List of successfully created strategy instances
        """
        strategies = []

        # Load configurations if not already loaded
        if not self._strategy_configs:
            self.load_configurations()

        # Discover classes if not already discovered
        if not self._strategy_classes:
            self.discover_strategy_classes()

        # Create instances
        for config in self._strategy_configs:
            strategy = self.create_strategy_instance(config)
            if strategy:
                strategies.append(strategy)

        self.logger.info(f"Created {len(strategies)} strategy instances")
        return strategies

    def get_strategy_config(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        """
        Get configuration for a specific strategy.

        Args:
            strategy_id: Strategy identifier

        Returns:
            Strategy configuration or None if not found
        """
        for config in self._strategy_configs:
            if config.get("strategy_id") == strategy_id:
                return config
        return None

    def list_available_classes(self) -> List[str]:
        """
        List all available strategy class paths.

        Returns:
            List of strategy class paths
        """
        if not self._strategy_classes:
            self.discover_strategy_classes()
        return list(self._strategy_classes.keys())

    def list_configured_strategies(self) -> List[str]:
        """
        List all configured strategy IDs.

        Returns:
            List of strategy IDs from configuration
        """
        if not self._strategy_configs:
            self.load_configurations()
        return [
            config.get("strategy_id")
            for config in self._strategy_configs
            if config.get("strategy_id")
        ]

    # ------------------------------------------------------------------
    # Dynamic configuration management
    # ------------------------------------------------------------------

    def add_or_update_config(self, config: Dict[str, Any]) -> None:
        """Add a new strategy config or update an existing one in memory."""
        strategy_id = config.get("strategy_id")
        if not strategy_id:
            return

        for idx, existing in enumerate(self._strategy_configs):
            if existing.get("strategy_id") == strategy_id:
                self._strategy_configs[idx] = config
                break
        else:
            self._strategy_configs.append(config)

    def remove_strategy_config(self, strategy_id: str) -> None:
        """Remove a strategy configuration from memory."""
        self._strategy_configs = [
            cfg for cfg in self._strategy_configs if cfg.get("strategy_id") != strategy_id
        ]

    def reload_strategy_config(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        """Reload a single strategy configuration from the YAML file."""
        try:
            config_file = Path(self.config_path)
            if not config_file.exists():
                self.logger.warning(
                    f"Strategy config file not found: {self.config_path}"
                )
                return None

            with open(config_file, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f) or {}

            strategies = config_data.get("strategies", [])
            for cfg in strategies:
                if cfg.get("strategy_id") == strategy_id:
                    self.add_or_update_config(cfg)
                    self.logger.info(
                        f"Reloaded strategy configuration for {strategy_id}"
                    )
                    return cfg

            self.logger.warning(
                f"Strategy configuration not found in file: {strategy_id}"
            )
            return None

        except Exception as e:
            self.logger.error(
                f"Failed to reload strategy configuration {strategy_id}: {e}"
            )
            return None
