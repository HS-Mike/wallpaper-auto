import win32com.client

def decode_wmi_string(char_array):
    """Convert WMI uint16 array to a readable ASCII string."""
    if not char_array:
        return "Unknown"
    try:
        return "".join(chr(char) for char in char_array if char != 0).strip()
    except Exception:
        return "Unknown"

def get_monitor_info_safe():
    print("Querying native Windows Monitor ID registry...\n")
    try:
        wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\wmi")
        
        # Only query the core MonitorID class to prevent 0x80041010 errors
        monitors = wmi.ExecQuery("SELECT * FROM WmiMonitorID")
        
        if not monitors or len(monitors) == 0:
            print("No active monitors found or WMI access is restricted.")
            return

        for i, monitor in enumerate(monitors, 1):
            manufacturer = decode_wmi_string(monitor.ManufacturerName)
            model_name = decode_wmi_string(monitor.UserFriendlyName)
            serial_num = decode_wmi_string(monitor.SerialNumberID)
            
            if model_name == "Unknown" or not model_name:
                model_name = decode_wmi_string(monitor.ProductCodeID)
            
            # The unique Windows Device ID is natively tied to this object
            raw_instance_name = monitor.InstanceName
            
            # Clean up the trailing '_0' suffix that WMI adds to instance names
            true_pnp_id = raw_instance_name.rsplit('_', 1)[0] if '_' in raw_instance_name else raw_instance_name

            print(f"--- Monitor #{i}: {manufacturer} {model_name} ---")
            print(f"  [+] Physical Serial Number : {serial_num}")
            print(f"  [+] True Windows PnP ID   : {true_pnp_id}")
            print()

    except Exception as e:
        print(f"Error accessing Windows WMI: {e}")

if __name__ == "__main__":
    get_monitor_info_safe()