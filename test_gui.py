import tkinter as tk
from tkinter import ttk

root = tk.Tk()
content = ttk.Panedwindow(root, orient="vertical")
content.pack(fill=tk.BOTH, expand=True)

f1 = ttk.Labelframe(content, text="F1")
f2 = ttk.Labelframe(content, text="F2")

content.add(f1, weight=1)
content.add(f2, weight=1)

tk.Label(f1, text="Label 1").pack()
tk.Label(f2, text="Label 2").pack()

# root.mainloop()
# I won't run mainloop because it'll block, I'll just check if it instantiates.
