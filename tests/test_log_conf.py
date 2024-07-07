"""
Log configuration tests
"""

from unittest.mock import call, patch
import logging
import unittest

from pyshellytemp.log_conf import configure_logging


@patch('pyshellytemp.log_conf.logging', autospec=True)
class LogConfTests(unittest.TestCase):
    def test_no_var(self, mock_log):
        with patch('pyshellytemp.log_conf.os.environ', {}):
            configure_logging()
        mock_log.basicConfig.assert_called_once_with(level=mock_log.INFO)

    def test_explicit_disable(self, mock_log):
        with patch('pyshellytemp.log_conf.os.environ', {'LOG_DEBUG': 'n'}):
            configure_logging()
        mock_log.basicConfig.assert_called_once_with(level=mock_log.INFO)

    def test_basic_enable(self, mock_log):
        with patch('pyshellytemp.log_conf.os.environ', {'LOG_DEBUG': 'y'}):
            configure_logging()
        mock_log.basicConfig.assert_called_once_with(level=mock_log.DEBUG)

    def test_module_enable(self, mock_log):
        fake_modules = {
            'os': NotImplemented,
            'pyshellytemp.blah': NotImplemented,
        }

        env = {
            'LOG_DEBUG': 'os,blah,unknown'
        }

        with self.assertLogs() as captured:
            with patch('pyshellytemp.log_conf.os.environ', env):
                with patch('pyshellytemp.log_conf.sys.modules', fake_modules):
                    configure_logging()

        self.assertEqual(captured.output, [
            "WARNING:pyshellytemp.log_conf:Module 'unknown' is not " \
            "registered, setting its log level to DEBUG anyway",
        ])

        mock_log.assert_has_calls([
            call.basicConfig(level=mock_log.INFO),
            call.getLogger('os'),
            call.getLogger().setLevel(mock_log.DEBUG),
            call.getLogger('pyshellytemp.blah'),
            call.getLogger().setLevel(mock_log.DEBUG),
        ])

