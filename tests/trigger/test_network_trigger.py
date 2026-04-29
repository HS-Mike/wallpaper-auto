import pytest
import pythoncom
from unittest.mock import MagicMock, patch, PropertyMock
from wallpaper_automator.trigger.network_trigger import NetworkTrigger

WMI_TIMEOUT = type('x_wmi_timed_out', (Exception,), {})


class TestNetworkTrigger:
    """Test NetworkTrigger core logic, fully isolated from WMI dependency"""

    def test_run_detects_network_change(self):
        """Run method detects network fingerprint change and triggers"""
        with (
            patch('wallpaper_automator.trigger.network_trigger.pythoncom'),
            patch('wallpaper_automator.trigger.network_trigger.wmi') as mock_wmi,
        ):
            monitor = NetworkTrigger()
            with patch.object(monitor, 'trigger') as mock_trigger_method:
                mock_wmi.x_wmi_timed_out = WMI_TIMEOUT

                mock_watcher = MagicMock()
                mock_wmi.WMI.return_value.watch_for.return_value = mock_watcher

                with patch.object(monitor, '_get_network_fingerprint') as mock_fingerprint:
                    def side_effect_logic():
                        if mock_fingerprint.call_count == 1:
                            return {"eth_192.168.1.1"}
                        monitor._request_stop()
                        return {"wifi_192.168.2.1"}

                    mock_fingerprint.side_effect = side_effect_logic

                    monitor.run()

                    mock_trigger_method.assert_called_once()
                    assert monitor._last_gateways == {"wifi_192.168.2.1"}

    def test_run_handles_timeout_loop(self):
        """Run method continues loop on WMI timeout"""
        with (
            patch('wallpaper_automator.trigger.network_trigger.pythoncom'),
            patch('wallpaper_automator.trigger.network_trigger.wmi') as mock_wmi,
        ):
            mock_wmi.x_wmi_timed_out = WMI_TIMEOUT
            monitor = NetworkTrigger()

            mock_watcher = MagicMock()
            call_count = 0
            def watcher_side_effect(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count >= 2:
                    monitor._request_stop()
                raise mock_wmi.x_wmi_timed_out()

            mock_watcher.side_effect = watcher_side_effect
            mock_wmi.WMI.return_value.watch_for.return_value = mock_watcher

            with patch.object(monitor, '_get_network_fingerprint', return_value=set()):
                monitor.run()

                assert mock_watcher.call_count == 2

    def test_get_network_fingerprint_logic(self):
        """Network fingerprint parsing extracts strings from WMI config"""
        monitor = NetworkTrigger()
        mock_client = MagicMock()
        monitor.c = mock_client

        mock_config = MagicMock()
        mock_config.Description = "Realtek Ethernet"
        mock_config.DefaultIPGateway = ["192.168.1.1"]

        mock_client.Win32_NetworkAdapterConfiguration.return_value = [mock_config]

        fingerprint = monitor._get_network_fingerprint()

        assert fingerprint == {"Realtek Ethernet_192.168.1.1"}

    def test_lifecycle_and_com_cleanup(self):
        """COM init/uninit is called during run"""
        with (
            patch('wallpaper_automator.trigger.network_trigger.pythoncom') as mock_pythoncom,
            patch('wallpaper_automator.trigger.network_trigger.wmi'),
        ):
            monitor = NetworkTrigger()
            monitor._request_stop()

            with patch.object(monitor, '_get_network_fingerprint', return_value=set()):
                monitor.run()

                mock_pythoncom.CoInitialize.assert_called_once()
                mock_pythoncom.CoUninitialize.assert_called_once()
                assert monitor.c is None
