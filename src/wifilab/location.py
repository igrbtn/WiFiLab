"""Location Services permission. macOS 14+ hides SSID and BSSID in Wi-Fi scans from apps without it.

The permission belongs to the app bundle (WiFiLab.app, Info.plist NSLocationWhenInUseUsageDescription); a
python started from a terminal is judged as that terminal, which usually has no location access, so its scans
come back anonymous. request() must run on the main thread of a running Cocoa app (the menubar app calls it).
"""
from __future__ import annotations

import sys

STATUS = {0: "not_determined", 1: "restricted", 2: "denied", 3: "authorized", 4: "authorized_when_in_use"}
_manager = None
_delegate = None


def status() -> dict:
    if sys.platform != "darwin":
        return {"needed": False, "status": "not_needed", "authorized": True, "services_enabled": True}
    try:
        import CoreLocation
    except ImportError:
        return {"needed": True, "status": "unavailable", "authorized": False, "services_enabled": False}
    enabled = bool(CoreLocation.CLLocationManager.locationServicesEnabled())
    code = int(_manager.authorizationStatus()) if _manager is not None else \
        int(CoreLocation.CLLocationManager.authorizationStatus())
    st = STATUS.get(code, "unknown")
    return {"needed": True, "status": st, "authorized": st.startswith("authorized") and enabled,
            "services_enabled": enabled, "bundled": _in_bundle()}


def _in_bundle() -> bool:
    try:
        from Foundation import NSBundle
        return str(NSBundle.mainBundle().bundlePath()).endswith(".app")
    except ImportError:
        return False


def request() -> dict:
    """Ask for When-In-Use authorization (shows the system prompt once); keep the manager alive."""
    global _manager, _delegate
    if sys.platform != "darwin":
        return status()
    import CoreLocation
    from Foundation import NSObject

    if _manager is None:
        class Delegate(NSObject):
            def locationManagerDidChangeAuthorization_(self, manager):
                if int(manager.authorizationStatus()) in (3, 4):
                    manager.stopUpdatingLocation()

            def locationManager_didUpdateLocations_(self, manager, locations):
                manager.stopUpdatingLocation()

            def locationManager_didFailWithError_(self, manager, error):
                manager.stopUpdatingLocation()

        _delegate = Delegate.alloc().init()
        _manager = CoreLocation.CLLocationManager.alloc().init()
        _manager.setDelegate_(_delegate)
    _manager.requestWhenInUseAuthorization()
    # Some macOS versions only show the prompt once a location update is asked for.
    _manager.startUpdatingLocation()
    return status()
