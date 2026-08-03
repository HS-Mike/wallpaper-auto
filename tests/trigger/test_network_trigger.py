"""Tests for network_trigger.py -- WiFi network change detection via WMI."""

from unittest.mock import MagicMock, patch

import pytest

from wallpaper_auto.trigger.network_trigger import NetworkTrigger


@pytest.fixture(scope="function")
def mock_kernel32():
    with patch("wallpaper_auto.trigger.network_trigger.KERNEL32") as m:
        yield m


@pytest.fixture(scope="function")
def mock_iphlpapi():
    with patch("wallpaper_auto.trigger.network_trigger.IPHLPAPI") as m:
        yield m


@pytest.fixture(scope="function")
def mock_pythoncom():
    with patch("wallpaper_auto.trigger.network_trigger.pythoncom") as m:
        yield m


class TestNetworkTriggerLifecycle:
    """Tests for start/stop lifecycle -- exit event creation/closing,
    signal, and cleanup."""

    def test_start_stop_real(self) -> None:
        """start spawns a real thread; stop signals exit and joins it (no mocks)"""
        trigger = NetworkTrigger()
        trigger.start()

        assert trigger._thread is not None
        assert trigger._thread.is_alive()

        trigger.stop()

        assert trigger._thread is None
        assert trigger._exit_event is None

    def test_double_start_raises_error(self) -> None:
        """start raises RuntimeError if called twice without stop in between"""
        trigger = NetworkTrigger()
        trigger.start()
        with pytest.raises(RuntimeError):
            trigger.start()
        # clean up
        trigger.stop()


class TestNetworkTriggerRun:
    """Tests for the ``run`` loop -- fingerprint change detection,
    idempotency, error exit, and COM lifecycle."""

    def test_run_detects_network_change(self, mock_kernel32, mock_iphlpapi, mock_pythoncom) -> None:
        """Run method detects network fingerprint change and triggers"""
        mock_kernel32.CreateEventW.return_value = 0xCAFE
        mock_iphlpapi.NotifyAddrChange.return_value = 0
        trigger = NetworkTrigger()
        trigger._exit_event = 0xBEEF

        captured_ssid = []

        def on_trigger(*args, **kwargs):
            captured_ssid.append(trigger.current_ssid)

        with patch.object(trigger, "trigger", wraps=on_trigger) as mock_trigger:
            with patch.object(trigger, "_get_network_fingerprint") as mock_fingerprint:
                with patch(
                    "wallpaper_auto.trigger.network_trigger.get_current_ssid",
                    return_value="HomeWiFi",
                ):
                    mock_fingerprint.side_effect = [
                        {"eth_192.168.1.1"},  # initial fingerprint
                        {"wifi_192.168.2.1"},  # after network change
                    ]
                    # first wait: network change (idx 0), second wait: exit (idx 1)
                    mock_kernel32.WaitForMultipleObjects.side_effect = [0, 1]

                    trigger.run()

                    mock_trigger.assert_called_once()
                    assert trigger._last_gateways == {"wifi_192.168.2.1"}
                    assert captured_ssid == ["HomeWiFi"]
                    assert trigger.current_ssid is None  # cleared after trigger

    def test_run_does_not_trigger_on_same_fingerprint(
        self, mock_kernel32, mock_iphlpapi, mock_pythoncom
    ) -> None:
        """Run method does not trigger when fingerprint hasn't changed after event"""
        mock_kernel32.CreateEventW.return_value = 0xCAFE
        mock_iphlpapi.NotifyAddrChange.return_value = 0
        trigger = NetworkTrigger()
        trigger._exit_event = 0xBEEF

        with patch.object(trigger, "trigger") as mock_trigger:
            with patch.object(trigger, "_get_network_fingerprint", return_value={"same_gateway"}):
                mock_kernel32.WaitForMultipleObjects.side_effect = [0, 1]

                trigger.run()

                mock_trigger.assert_not_called()

    def test_run_breaks_on_notify_addr_change_error(
        self, mock_kernel32, mock_iphlpapi, mock_pythoncom
    ) -> None:
        """Run breaks loop when NotifyAddrChange returns an unexpected error"""
        mock_kernel32.CreateEventW.return_value = 0xCAFE
        mock_iphlpapi.NotifyAddrChange.return_value = 1  # error (not 0 or 997)
        trigger = NetworkTrigger()
        trigger._exit_event = 0xBEEF

        with patch.object(trigger, "_get_network_fingerprint", return_value=set()):
            trigger.run()

            mock_kernel32.WaitForMultipleObjects.assert_not_called()

    def test_lifecycle_and_com_cleanup(self, mock_kernel32, mock_iphlpapi, mock_pythoncom) -> None:
        """COM init/uninit is called during run"""
        mock_kernel32.CreateEventW.return_value = 0xCAFE
        mock_iphlpapi.NotifyAddrChange.return_value = 0
        mock_kernel32.WaitForMultipleObjects.return_value = 1  # immediate exit
        trigger = NetworkTrigger()
        trigger._exit_event = 0xBEEF

        with patch.object(trigger, "_get_network_fingerprint", return_value=set()):
            trigger.run()

            mock_pythoncom.CoInitialize.assert_called_once()
            mock_pythoncom.CoUninitialize.assert_called_once()
            # CloseHandle is called for net_event in finally block
            mock_kernel32.CloseHandle.assert_called_once()


class TestNetworkTriggerFingerprint:
    """Tests for ``_get_network_fingerprint`` -- WMI parsing, exception
    handling, gateway filtering."""

    def test_get_network_fingerprint_logic(self) -> None:
        """Network fingerprint parsing extracts strings from WMI config"""
        with patch("wallpaper_auto.trigger.network_trigger.wmi") as mock_wmi:
            mock_config = MagicMock()
            mock_config.Description = "Realtek Ethernet"
            mock_config.DefaultIPGateway = ["192.168.1.1"]
            mock_wmi.WMI.return_value.Win32_NetworkAdapterConfiguration.return_value = [mock_config]

            fingerprint = NetworkTrigger._get_network_fingerprint()

            assert fingerprint == {"Realtek Ethernet_192.168.1.1"}

    def test_get_network_fingerprint_handles_exception(self) -> None:
        """Network fingerprint returns empty set on WMI exception"""
        with patch("wallpaper_auto.trigger.network_trigger.wmi") as mock_wmi:
            mock_wmi.WMI.side_effect = Exception("COM error")

            fingerprint = NetworkTrigger._get_network_fingerprint()

            assert fingerprint == set()

    def test_get_network_fingerprint_skips_no_gateway(self) -> None:
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
