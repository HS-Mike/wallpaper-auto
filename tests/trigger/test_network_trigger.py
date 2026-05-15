"""Tests for network_trigger.py — WiFi network change detection via WMI."""
# ruff: noqa: N806 — mock names match uppercase Win32 constant names

from unittest.mock import MagicMock, patch

import pytest

from wallpaper_auto.trigger.network_trigger import NetworkTrigger


@pytest.fixture
def mock_run_deps():
    """Patch the four external dependencies common to ``run()`` tests
    and set up the standard KERNEL32/IPHLPAPI return values."""
    with (
        patch("wallpaper_auto.trigger.network_trigger.pythoncom") as mock_pythoncom,
        patch("wallpaper_auto.trigger.network_trigger.wmi"),
        patch("wallpaper_auto.trigger.network_trigger.KERNEL32") as mock_KERNEL32,
        patch("wallpaper_auto.trigger.network_trigger.IPHLPAPI") as mock_IPHLPAPI,
    ):
        mock_KERNEL32.CreateEventW.return_value = 0xCAFE
        mock_IPHLPAPI.NotifyAddrChange.return_value = 0
        yield mock_KERNEL32, mock_IPHLPAPI, mock_pythoncom


@pytest.fixture
def mock_kernel32():
    with patch("wallpaper_auto.trigger.network_trigger.KERNEL32") as mock_k:
        yield mock_k


class TestNetworkTrigger:
    """Test NetworkTrigger core logic, fully isolated from WMI and Win32 API dependencies"""

    def test_run_detects_network_change(self, mock_run_deps):
        """Run method detects network fingerprint change and triggers"""
        mock_KERNEL32, mock_IPHLPAPI, _ = mock_run_deps
        monitor = NetworkTrigger()
        monitor._exit_event = 0xBEEF

        with patch.object(monitor, "trigger") as mock_trigger:
            with patch.object(monitor, "_get_network_fingerprint") as mock_fingerprint:
                mock_fingerprint.side_effect = [
                    {"eth_192.168.1.1"},  # initial fingerprint
                    {"wifi_192.168.2.1"},  # after network change
                ]
                # first wait: network change (idx 0), second wait: exit (idx 1)
                mock_KERNEL32.WaitForMultipleObjects.side_effect = [0, 1]

                monitor.run()

                mock_trigger.assert_called_once()
                assert monitor._last_gateways == {"wifi_192.168.2.1"}

    def test_run_does_not_trigger_on_same_fingerprint(self, mock_run_deps):
        """Run method does not trigger when fingerprint hasn't changed after event"""
        mock_KERNEL32, mock_IPHLPAPI, _ = mock_run_deps
        monitor = NetworkTrigger()
        monitor._exit_event = 0xBEEF

        with patch.object(monitor, "trigger") as mock_trigger:
            with patch.object(monitor, "_get_network_fingerprint", return_value={"same_gateway"}):
                mock_KERNEL32.WaitForMultipleObjects.side_effect = [0, 1]

                monitor.run()

                mock_trigger.assert_not_called()

    def test_run_breaks_on_notify_addr_change_error(self, mock_run_deps):
        """Run breaks loop when NotifyAddrChange returns an unexpected error"""
        mock_KERNEL32, mock_IPHLPAPI, _ = mock_run_deps
        mock_IPHLPAPI.NotifyAddrChange.return_value = 1  # error (not 0 or 997)
        monitor = NetworkTrigger()
        monitor._exit_event = 0xBEEF

        with patch.object(monitor, "_get_network_fingerprint", return_value=set()):
            monitor.run()

            mock_KERNEL32.WaitForMultipleObjects.assert_not_called()

    def test_get_network_fingerprint_logic(self):
        """Network fingerprint parsing extracts strings from WMI config"""
        with patch("wallpaper_auto.trigger.network_trigger.wmi") as mock_wmi:
            mock_config = MagicMock()
            mock_config.Description = "Realtek Ethernet"
            mock_config.DefaultIPGateway = ["192.168.1.1"]
            mock_wmi.WMI.return_value.Win32_NetworkAdapterConfiguration.return_value = [mock_config]

            fingerprint = NetworkTrigger._get_network_fingerprint()

            assert fingerprint == {"Realtek Ethernet_192.168.1.1"}

    def test_get_network_fingerprint_handles_exception(self):
        """Network fingerprint returns empty set on WMI exception"""
        with patch("wallpaper_auto.trigger.network_trigger.wmi") as mock_wmi:
            mock_wmi.WMI.side_effect = Exception("COM error")

            fingerprint = NetworkTrigger._get_network_fingerprint()

            assert fingerprint == set()

    def test_get_network_fingerprint_skips_no_gateway(self):
        """Network fingerprint skips adapters without a default gateway"""
        with patch("wallpaper_auto.trigger.network_trigger.wmi") as mock_wmi:
            mock_config1 = MagicMock()
            mock_config1.Description = "WiFi"
            mock_config1.DefaultIPGateway = ["10.0.0.1"]

            mock_config2 = MagicMock()
            mock_config2.Description = "Bluetooth"
            mock_config2.DefaultIPGateway = None  # no gateway

            mock_wmi.WMI.return_value.Win32_NetworkAdapterConfiguration.return_value = [
                mock_config1,
                mock_config2,
            ]

            fingerprint = NetworkTrigger._get_network_fingerprint()

            assert fingerprint == {"WiFi_10.0.0.1"}

    def test_lifecycle_and_com_cleanup(self, mock_run_deps):
        """COM init/uninit is called during run"""
        mock_KERNEL32, mock_IPHLPAPI, mock_pythoncom = mock_run_deps
        mock_KERNEL32.WaitForMultipleObjects.return_value = 1  # immediate exit
        monitor = NetworkTrigger()
        monitor._exit_event = 0xBEEF

        with patch.object(monitor, "_get_network_fingerprint", return_value=set()):
            monitor.run()

            mock_pythoncom.CoInitialize.assert_called_once()
            mock_pythoncom.CoUninitialize.assert_called_once()
            # CloseHandle is called for net_event in finally block
            mock_KERNEL32.CloseHandle.assert_called_once()

    def test_activate_creates_exit_event(self, mock_kernel32):
        """activate creates exit_event and delegates to super"""
        mock_kernel32.CreateEventW.return_value = 0xCAFE
        monitor = NetworkTrigger()

        with patch("threading.Thread.start") as mock_start:
            monitor.activate()

            mock_kernel32.CreateEventW.assert_called_once_with(None, False, False, None)
            assert monitor._exit_event == 0xCAFE
            mock_start.assert_called_once()

    def test_activate_closes_old_handle(self, mock_kernel32):
        """activate closes existing exit_event handle before creating new one"""
        mock_kernel32.CreateEventW.return_value = 0xBEEF
        monitor = NetworkTrigger()
        monitor._exit_event = 0xDEAD

        with patch("threading.Thread.start"):
            monitor.activate()

            mock_kernel32.CloseHandle.assert_any_call(0xDEAD)
            mock_kernel32.CreateEventW.assert_called_once()
            assert monitor._exit_event == 0xBEEF

    def test_deactivate_signals_and_cleans_up_handle(self, mock_kernel32):
        """deactivate signals the exit event, joins thread, and closes handle"""
        monitor = NetworkTrigger()
        monitor._exit_event = 0xCAFE

        with patch("threading.Thread.join") as mock_super_deactivate:
            with patch.object(monitor, "_request_stop"):
                monitor.deactivate()

                mock_kernel32.SetEvent.assert_called_once_with(0xCAFE)
                mock_super_deactivate.assert_called_once_with(timeout=3)
                mock_kernel32.CloseHandle.assert_called_once_with(0xCAFE)
                assert monitor._exit_event is None

    def test_deactivate_skips_when_no_exit_event(self, mock_kernel32):
        """deactivate is safe when exit_event is None"""
        monitor = NetworkTrigger()
        assert monitor._exit_event is None

        with patch("threading.Thread.join"):
            with patch.object(monitor, "_request_stop"):
                monitor.deactivate()

                mock_kernel32.SetEvent.assert_not_called()
                mock_kernel32.CloseHandle.assert_not_called()
