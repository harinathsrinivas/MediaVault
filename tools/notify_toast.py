"""Dependency-free Windows toast notifications via PowerShell + WinRT.

send_toast(title, message) -> bool
  Returns True on a clean PowerShell exit (returncode 0), False in all
  failure/unavailable cases.  Never raises.  Is a no-op on non-Windows.

No BurntToast module, no pip dependency — uses
Windows.UI.Notifications.ToastNotificationManager directly from PowerShell.
"""

import subprocess
import sys


def send_toast(title: str, message: str) -> bool:
    """Fire a Windows desktop toast and return True on success, False otherwise."""
    if sys.platform != "win32":
        return False

    # Sanitize: collapse newlines to spaces and escape PowerShell single-quotes
    # (PS doubles them: ' -> '') so neither breaks the -Command string nor injects.
    def _clean(text: str) -> str:
        text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        return text.replace("'", "''")

    safe_title = _clean(title)
    safe_message = _clean(message)

    # AppId: the built-in PowerShell shortcut — guaranteed to surface a toast.
    APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

    PS_SCRIPT = (
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument,"
        " ContentType = WindowsRuntime] | Out-Null;"
        " [Windows.UI.Notifications.ToastNotificationManager,"
        " Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
        " $xml = [Windows.Data.Xml.Dom.XmlDocument]::new();"
        " $xml.loadXml('<toast><visual><binding template=\"ToastGeneric\">"
        f"<text>{safe_title}</text>"
        f"<text>{safe_message}</text>"
        "</binding></visual></toast>');"
        f" $AppId = '{APP_ID}';"
        " [Windows.UI.Notifications.ToastNotificationManager]"
        "::CreateToastNotifier($AppId)"
        ".Show([Windows.UI.Notifications.ToastNotification]::new($xml))"
    )

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_SCRIPT],
            capture_output=True,
            timeout=20,
        )
        return result.returncode == 0
    except FileNotFoundError:
        # PowerShell not found on PATH
        return False
    except Exception:
        # subprocess.TimeoutExpired, OSError, or any other failure
        return False


if __name__ == "__main__":
    ok = send_toast("MediaVault", "test")
    print(ok)
