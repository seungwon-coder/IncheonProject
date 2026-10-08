"""Double-click launcher; report missing dependencies without a console."""
import os
import sys
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

folder=Path(__file__).resolve().parent
os.chdir(folder)
sys.path.insert(0,str(folder))
root=tk.Tk()
try:
    from control_panel import Panel
    Panel(root)
    root.mainloop()
except Exception:
    root.withdraw()
    messagebox.showerror('PV5 실행 실패', traceback.format_exc()+'\n최초 실행이면 INSTALL.bat으로 필수 패키지를 설치하세요.')
    root.destroy()
