"""
Tests for configuration validation and runtime update rules.
"""

import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config, ConfigManager


class TestCandleCloseGraceSeconds(unittest.TestCase):
    """candle_close_grace_seconds: default, validation, and runtime refusal."""

    def _config(self, **overrides):
        config = Config()
        config.eodhd_api_key = 'test_key'
        config.persist_config = False
        for key, value in overrides.items():
            setattr(config, key, value)
        return config

    def test_default_is_two_seconds(self):
        self.assertEqual(self._config().candle_close_grace_seconds, 2.0)

    def test_read_from_environment(self):
        with unittest.mock.patch.dict(
            os.environ, {'CANDLE_CLOSE_GRACE_SECONDS': '0.5'}
        ):
            self.assertEqual(Config().candle_close_grace_seconds, 0.5)

    def test_zero_is_allowed(self):
        errors = self._config(candle_close_grace_seconds=0.0).validate()
        self.assertEqual(
            [e for e in errors if 'candle_close_grace_seconds' in e], []
        )

    def test_negative_is_rejected(self):
        errors = self._config(candle_close_grace_seconds=-0.1).validate()
        self.assertIn('candle_close_grace_seconds must be >= 0', errors)

    def test_grace_at_or_beyond_the_interval_is_rejected(self):
        """A grace as long as the interval would never let a bucket close."""
        for grace in (60.0, 90.0):
            config = self._config(
                candle_interval_minutes=1,
                candle_close_grace_seconds=grace
            )
            errors = [e for e in config.validate()
                      if 'candle_close_grace_seconds' in e]
            self.assertEqual(len(errors), 1, f"grace={grace}")
            self.assertIn('less than the candle interval (60s)', errors[0])

    def test_valid_grace_under_a_longer_interval(self):
        config = self._config(
            candle_interval_minutes=5,
            candle_close_grace_seconds=59.0
        )
        self.assertEqual(
            [e for e in config.validate() if 'candle_close_grace_seconds' in e],
            []
        )

    def test_appears_in_public_config(self):
        public = self._config(candle_close_grace_seconds=3.0).get_public_config()
        self.assertEqual(public['candle_close_grace_seconds'], 3.0)

    def test_cannot_be_changed_at_runtime(self):
        """The close task lives in another process and never sees the change."""
        manager = ConfigManager(self._config())

        result = manager.update({'candle_close_grace_seconds': 5.0})

        self.assertEqual(result['updated'], [])
        self.assertEqual(len(result['errors']), 1)
        self.assertIn('Cannot update candle_close_grace_seconds', result['errors'][0])
        self.assertEqual(manager.config.candle_close_grace_seconds, 2.0)



class TestEmptyIntervalAudit(unittest.TestCase):
    """empty_interval_audit: default, allowed values, runtime refusal."""

    def _config(self, **overrides):
        config = Config()
        config.eodhd_api_key = 'test_key'
        config.persist_config = False
        for key, value in overrides.items():
            setattr(config, key, value)
        return config

    def test_default_is_off(self):
        """Measurement is opt-in; the service does nothing extra by default."""
        self.assertEqual(self._config().empty_interval_audit, 'off')

    def test_allowed_values(self):
        for mode in ('off', 'regular', 'extended'):
            errors = [e for e in self._config(empty_interval_audit=mode).validate()
                      if 'empty_interval_audit' in e]
            self.assertEqual(errors, [], mode)

    def test_unknown_value_is_rejected(self):
        errors = self._config(empty_interval_audit='sometimes').validate()
        self.assertTrue(
            any('empty_interval_audit must be one of' in e for e in errors),
            errors
        )

    def test_environment_value_is_normalised(self):
        with unittest.mock.patch.dict(
            os.environ, {'EMPTY_INTERVAL_AUDIT': '  Regular  '}
        ):
            self.assertEqual(Config().empty_interval_audit, 'regular')

    def test_appears_in_public_config(self):
        public = self._config(empty_interval_audit='regular').get_public_config()
        self.assertEqual(public['empty_interval_audit'], 'regular')

    def test_cannot_be_changed_at_runtime(self):
        manager = ConfigManager(self._config())

        result = manager.update({'empty_interval_audit': 'extended'})

        self.assertEqual(result['updated'], [])
        self.assertIn('Cannot update empty_interval_audit', result['errors'][0])
        self.assertEqual(manager.config.empty_interval_audit, 'off')


if __name__ == '__main__':
    unittest.main()
