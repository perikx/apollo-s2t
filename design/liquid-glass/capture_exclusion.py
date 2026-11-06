"""Can an overlay window read the desktop *behind itself* on Windows?
Show a solid magenta topmost window, exclude it from capture
(WDA_EXCLUDEFROMCAPTURE), grab the screen under it with BitBlt (PIL ImageGrab),
and count magenta pixels in the grab."""
import ctypes, os, tempfile, time, tkinter as tk
from PIL import ImageGrab

ctypes.windll.shcore.SetProcessDpiAwareness(2)
X, Y, S = 300, 300, 160
root = tk.Tk(); root.overrideredirect(True); root.attributes("-topmost", True)
root.geometry(f"{S}x{S}+{X}+{Y}"); root.configure(bg="#ff00ff"); root.update(); time.sleep(0.5); root.update()
hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()

def share(img):
    px = img.load(); n = hits = 0
    for x in range(0, img.width, 4):
        for y in range(0, img.height, 4):
            n += 1; r, g, b = px[x, y][:3]; hits += (r > 240 and g < 20 and b > 240)
    return hits / n

before = ImageGrab.grab((X, Y, X + S, Y + S), all_screens=True)
ok = ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11)
root.update(); time.sleep(0.4); root.update()
times = []
for _ in range(20):
    t0 = time.perf_counter(); after = ImageGrab.grab((X, Y, X + S, Y + S), all_screens=True); times.append((time.perf_counter() - t0) * 1000)
print(f"SetWindowDisplayAffinity ok={bool(ok)}")
print(f"magenta in grab before exclusion: {share(before):.0%}")
print(f"magenta in grab after exclusion:  {share(after):.0%}")
print(f"160x160 grab: median {sorted(times)[10]:.1f} ms over 20 grabs")
shot = os.path.join(tempfile.gettempdir(), "apollo_behind.png")
after.save(shot)
print(f"grab saved to {shot}")
root.destroy()
