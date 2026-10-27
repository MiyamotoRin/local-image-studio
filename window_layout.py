"""Keep initial Windows window frames inside the current monitor's work area."""
import os


def fit_window_to_work_area(window):
    if os.name != 'nt' or window.state() == 'withdrawn':
        return
    if not window.winfo_ismapped():
        def on_map(event):
            if event.widget is window:
                window.unbind('<Map>', binding)
                window.after_idle(lambda: fit_window_to_work_area(window))
        binding = window.bind('<Map>', on_map, add='+')
        return

    import ctypes
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT),
                    ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]

    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]

    window.update_idletasks()
    hwnd = user32.GetAncestor(window.winfo_id(), 2)  # GA_ROOT: include the title bar.
    monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
    outer, client = wintypes.RECT(), wintypes.RECT()
    if not (user32.GetMonitorInfoW(monitor, ctypes.byref(info))
            and user32.GetWindowRect(hwnd, ctypes.byref(outer))
            and user32.GetClientRect(hwnd, ctypes.byref(client))):
        return

    work = info.rcWork  # Excludes taskbars on any edge of this monitor.
    frame_width = outer.right - outer.left - client.right
    frame_height = outer.bottom - outer.top - client.bottom
    available_width = max(1, work.right - work.left - frame_width)
    available_height = max(1, work.bottom - work.top - frame_height)
    min_width, min_height = window.minsize()
    window.minsize(min(min_width, available_width), min(min_height, available_height))
    width = min(window.winfo_width(), available_width)
    height = min(window.winfo_height(), available_height)
    window.geometry(f'{width}x{height}')
    window.update_idletasks()
    x = work.left + max(0, (work.right - work.left - width - frame_width) // 2)
    y = work.top + max(0, (work.bottom - work.top - height - frame_height) // 2)
    # Native coordinates also handle monitors to the left/above the primary screen.
    user32.SetWindowPos(hwnd, None, x, y, 0, 0, 0x0015)  # NOSIZE | NOZORDER | NOACTIVATE
