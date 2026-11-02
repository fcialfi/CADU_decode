#!/usr/bin/env python3
"""Window for cadu_decode.py: open a file of CADUs, see the summary, browse the frames, show one CADU.

    python cadu_gui.py [capture.bin]

Tkinter only (part of Python). The decoding is the one of cadu_decode.py.

Author: Fabio Cialfi (Exostaff). Proprietary software, see LICENSE.
"""

import csv
import os
import sys
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

import cadu_decode as cd
import licensing
from detail_view import DetailView
from summary_view import SummaryView
from version import APP_AUTHOR, APP_AUTHOR_SHORT, APP_NAME, APP_VERSION

COLUMNS = (  # (key, heading, width)
    ("n", "#", 70), ("file", "File", 160), ("offset", "Offset", 90), ("scid", "SCID", 55), ("vcid", "VC", 40), ("mc_count", "MCFC", 55),
    ("vc_count", "VCFC", 55), ("fhp", "FHP", 150), ("segment_length_id", "Seg", 40), ("ocf_flag", "OCF", 40),
    ("clcw_vcid", "CLCW VC", 80), ("cop_in_effect", "COP", 45), ("flags", "CLCW flags", 140),
    ("report_value", "Report", 60), ("rs", "RS", 75),
)
ALL = "All"
CONCATENATED = "All files, concatenated"

# Navy header with the Exostaff logo, as in the other Exostaff tools.
HEADER_BG, HEADER_FG, HEADER_FG_MUTED, HEADER_HOVER = "#1f2a36", "#ffffff", "#b8c4d0", "#2c3b4b"
WARNING_ON_DARK = "#ffb74d"  # licence expiring soon

# Logo and icon images (assets/), found next to this file or, in the packaged
# executable, in PyInstaller's extraction folder.
_ASSETS_DIR = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "assets")
LOGO_HEADER_FILE = "exostaff_logo_header.png"  # 44 px high, for the window header
ICON_FILE = "exostaff_icon.ico"
ICON_PNG_FILE = "exostaff_icon_64.png"


def _asset_path(name: str):
    """Path of an assets/ file, or None when it is missing."""
    path = os.path.join(_ASSETS_DIR, name)
    return path if os.path.isfile(path) else None


def _load_image(name: str, master=None):
    """An assets/ PNG as a Tk image, or None when missing or unreadable (keep a reference to it)."""
    path = _asset_path(name)
    if path is None:
        return None
    try:
        return tk.PhotoImage(file=path, master=master)
    except tk.TclError:
        return None


def _set_icon(root: tk.Tk) -> None:
    """Exostaff icon in the title bar and the task bar."""
    icon = _asset_path(ICON_FILE)
    if icon and sys.platform == "win32":
        try:
            root.iconbitmap(default=icon)
        except tk.TclError:
            pass
    image = _load_image(ICON_PNG_FILE, master=root)
    if image is not None:
        root._icon_image = image
        root.iconphoto(True, image)


class CaduWindow:
    def __init__(self, root: tk.Tk, path=None, license_status=None):
        """``path``: a file, or a list of files, to open at once."""
        self.root = root
        self.data = b""
        self.files: list[tuple[str, bytes]] = []  # files opened (path, content), in order of name
        self.bounds = None  # files concatenated: (start offset, name) of each, see cadu_decode.concatenate
        self.rows: list[dict] = []
        self.path = None
        self.cadu_len = 0
        self.frame_length = 0  # 0: CADUs; otherwise a file of bare transfer frames of this length
        self.randomized = True
        self.usual = None
        self.packets = None
        self.license_status = license_status or licensing.check_license()
        root.title(f"{APP_NAME} {APP_VERSION}")
        root.geometry("1320x880")
        _set_icon(root)
        self.mono = tkfont.nametofont("TkFixedFont").copy()
        self.mono.configure(size=10)
        self._header(root)

        bar = ttk.Frame(root, padding=(10, 8))
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="Open…", command=self.open_file).pack(side=tk.LEFT)
        # What the file holds: CADUs (sync marker + codeblock) or bare transfer frames
        self.input_var = tk.StringVar(value="Auto")
        self.length_var = tk.StringVar(value="Auto")
        ttk.Label(bar, text="  Input").pack(side=tk.LEFT)
        box = ttk.Combobox(bar, textvariable=self.input_var, values=("Auto", "CADU", "Frames"),
                           width=7, state="readonly")
        box.pack(side=tk.LEFT, padx=(4, 0))
        box.bind("<<ComboboxSelected>>", lambda _e: self.reload())
        ttk.Label(bar, text="  Frame bytes").pack(side=tk.LEFT)
        self.length_box = ttk.Combobox(bar, textvariable=self.length_var, width=5,
                                       values=("Auto", "1115", "1024", "892", "2048"))
        self.length_box.pack(side=tk.LEFT, padx=(4, 0))
        self.length_box.bind("<<ComboboxSelected>>", lambda _e: self.reload())
        self.length_box.bind("<Return>", lambda _e: self.reload())
        self.interleave_var = tk.IntVar(value=5)
        self.randomized_var = tk.StringVar(value="Auto")
        self.fecf_var = tk.BooleanVar(value=False)
        ttk.Label(bar, text="  RS interleaving I").pack(side=tk.LEFT)
        self.interleave_box = ttk.Spinbox(bar, from_=1, to=8, width=3, textvariable=self.interleave_var,
                                          command=self.reload)
        self.interleave_box.pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(bar, text="Randomized").pack(side=tk.LEFT)
        box = ttk.Combobox(bar, textvariable=self.randomized_var, values=("Auto", "Yes", "No"), width=6,
                           state="readonly")
        box.pack(side=tk.LEFT, padx=(4, 0))
        box.bind("<<ComboboxSelected>>", lambda _e: self.reload())
        ttk.Checkbutton(bar, text="FECF", variable=self.fecf_var, command=self.reload).pack(side=tk.LEFT, padx=(8, 0))
        self.rs_var = tk.BooleanVar(value=True)
        self.rs_box = ttk.Checkbutton(bar, text="Reed-Solomon check", variable=self.rs_var, command=self.reload)
        self.rs_box.pack(side=tk.LEFT, padx=(12, 0))
        # Analysis options (no new decoding): they need the Reed-Solomon check.
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=12)
        self.exclude_var = tk.BooleanVar(value=False)
        self.exclude_box = ttk.Checkbutton(bar, text="Exclude uncorrectable frames", variable=self.exclude_var,
                                           command=self._exclude_changed)
        self.exclude_box.pack(side=tk.LEFT)
        export = ttk.Menubutton(bar, text="Export")
        menu = tk.Menu(export, tearoff=False)
        menu.add_command(label="Frames to CSV…", command=self.export_csv)
        menu.add_command(label="Packets, one file per APID…", command=self.export_packets)
        export["menu"] = menu
        export.pack(side=tk.RIGHT)
        root.bind_all("<Control-q>", lambda _e: self.exit_app())
        root.bind_all("<F1>", lambda _e: self.show_about())
        root.protocol("WM_DELETE_WINDOW", self.exit_app)
        self.file_var = tk.StringVar(value="No file: press Open… (CADUs starting with the sync marker 1ACFFC1D, "
                                           "or bare transfer frames)")
        status = ttk.Frame(root, padding=(10, 0))
        status.pack(fill=tk.X)
        # Several files opened: the one shown, or all of them as one stream (chunks of one capture)
        self.choice_var = tk.StringVar()
        self.choice_label = ttk.Label(status, text="File")
        self.choice_box = ttk.Combobox(status, textvariable=self.choice_var, width=60, state="readonly")
        self.choice_box.bind("<<ComboboxSelected>>", lambda _e: self.select_file())
        self.file_label = ttk.Label(status, textvariable=self.file_var)
        self.file_label.pack(side=tk.LEFT)
        # Progress of the loading, shown only while a file is read and analysed.
        self.progress = ttk.Progressbar(status, mode="determinate", maximum=100, length=320)
        self.progress_label = ttk.Label(status, width=8, anchor="e")

        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        self.check_var = tk.StringVar(value=cd.DEFAULT_CHECKED_APIDS)  # APIDs whose continuity is checked
        self.summary_view = SummaryView(self.tabs, on_goto=self.open_cadu, check_var=self.check_var,
                                        on_check=self.refresh_summary)
        self.tabs.add(self.summary_view, text="Summary")

        frames_tab = ttk.Frame(self.tabs)
        self.tabs.add(frames_tab, text="Frames")
        filters = ttk.Frame(frames_tab, padding=(0, 4))
        filters.pack(fill=tk.X)
        ttk.Label(filters, text="Virtual channel").pack(side=tk.LEFT)
        self.vc_var = tk.StringVar(value=ALL)
        self.vc_box = ttk.Combobox(filters, textvariable=self.vc_var, values=[ALL], width=8, state="readonly")
        self.vc_box.pack(side=tk.LEFT, padx=(4, 12))
        self.vc_box.bind("<<ComboboxSelected>>", lambda _e: self.fill_frames())
        # Every filter hides frames: ticked, fewer frames are listed.
        self.hide_idle_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(filters, text="Hide idle frames", variable=self.hide_idle_var,
                        command=self.fill_frames).pack(side=tk.LEFT)
        self.rserr_var = tk.BooleanVar(value=False)
        self.rserr_box = ttk.Checkbutton(filters, text="Only RS errors", variable=self.rserr_var,
                                         command=self.fill_frames)
        self.rserr_box.pack(side=tk.LEFT, padx=(12, 0))
        self.odd_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(filters, text="Only unusual CLCW", variable=self.odd_var,
                        command=self.fill_frames).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(filters, text="   Go to #").pack(side=tk.LEFT)
        self.goto_var = tk.StringVar()
        goto = ttk.Entry(filters, textvariable=self.goto_var, width=8)
        goto.pack(side=tk.LEFT, padx=4)
        goto.bind("<Return>", lambda _e: self.goto())
        ttk.Button(filters, text="Show", command=self.goto).pack(side=tk.LEFT)
        self.dump_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(filters, text="Hex dump", variable=self.dump_var,
                        command=self.show_selected).pack(side=tk.LEFT, padx=(12, 0))
        self.count_var = tk.StringVar()
        ttk.Label(filters, textvariable=self.count_var).pack(side=tk.RIGHT)

        panes = ttk.PanedWindow(frames_tab, orient=tk.VERTICAL)
        panes.pack(fill=tk.BOTH, expand=True)
        table_box = ttk.Frame(panes)
        self.tree = ttk.Treeview(table_box, columns=[c[0] for c in COLUMNS], show="headings", height=9,
                                 displaycolumns=[c[0] for c in COLUMNS if c[0] != "file"])
        for key, heading, width in COLUMNS:
            self.tree.heading(key, text=heading)
            self.tree.column(key, width=width, anchor="e" if key not in ("fhp", "flags", "file") else "w",
                             stretch=False)
        scroll = ttk.Scrollbar(table_box, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.show_selected())
        panes.add(table_box, weight=1)
        self.detail_view = DetailView(panes)
        panes.add(self.detail_view, weight=3)
        # The frame table gets about 10 rows; the rest goes to the detail of the selected frame.
        def _place_sash(_event):
            if panes.winfo_height() > 400 and not getattr(panes, "_placed", False):
                panes.sashpos(0, 240)
                panes._placed = True

        panes.bind("<Configure>", _place_sash)

        # The summary as text, to read or copy.
        report = self.report_tab = ttk.Frame(self.tabs)
        self.tabs.add(report, text="Text report")
        ttk.Button(report, text="Copy to clipboard", command=self.copy_report).pack(anchor="w", pady=(4, 4))
        self.summary_text = self._text(report)
        self.summary_text.master.pack(fill=tk.BOTH, expand=True)

        # Several files: one row each (pass from the file name) and the totals; shown with 2 files or more.
        self.files_tab = ttk.Frame(self.tabs)
        top = ttk.Frame(self.files_tab, padding=(0, 4))
        top.pack(fill=tk.X)
        ttk.Label(top, text="One row per file, in order of pass start (pass times and station from the file name). "
                            "MB: size of the file; packet MB: packets without the idle ones. "
                            "Double click: open the file.").pack(side=tk.LEFT)
        ttk.Button(top, text="Copy as text", command=self.copy_overview).pack(side=tk.RIGHT)
        box = ttk.Frame(self.files_tab)
        box.pack(fill=tk.BOTH, expand=True)
        heads = cd.overview_table([])[0]
        widths = (50, 60, 165, 70, 45, 100, 60, 100, 60, 80, 85, 95, 125, 75, 70, 80, 420)
        self.files_tree = ttk.Treeview(box, columns=[str(k) for k in range(len(heads))], show="headings")
        for k, (head, width) in enumerate(zip(heads, widths)):
            self.files_tree.heading(str(k), text=head[0].upper() + head[1:])
            self.files_tree.column(str(k), width=width, anchor="w" if k in (0, 1, 2, 3, 5, 7, 16) else "e",
                                   stretch=k == 16)
        bold = tkfont.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold")
        self.files_tree.tag_configure("total", font=bold)
        self._bold = bold  # keep a reference: Tk forgets fonts no longer referenced
        yscroll = ttk.Scrollbar(box, orient=tk.VERTICAL, command=self.files_tree.yview)
        xscroll = ttk.Scrollbar(box, orient=tk.HORIZONTAL, command=self.files_tree.xview)
        self.files_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        xscroll.pack(side=tk.BOTTOM, fill=tk.X)
        self.files_tree.pack(fill=tk.BOTH, expand=True)
        self.files_tree.bind("<Double-1>", self._open_from_overview)
        self.overview = None      # file_overview() of every file, for the options in self._overview_key
        self._overview_key = None
        self.tabs.bind("<<NotebookTabChanged>>", lambda _e: self._tab_changed())

        if path:  # after the window is shown, so the progress of the loading is visible
            root.after(50, lambda: self.load(path))

    def _text(self, parent) -> tk.Text:
        box = ttk.Frame(parent)
        text = tk.Text(box, wrap="none", font=self.mono, padx=8, pady=6, relief="flat")
        ys = ttk.Scrollbar(box, orient=tk.VERTICAL, command=text.yview)
        xs = ttk.Scrollbar(box, orient=tk.HORIZONTAL, command=text.xview)
        text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set, state="disabled")
        ys.pack(side=tk.RIGHT, fill=tk.Y)
        xs.pack(side=tk.BOTTOM, fill=tk.X)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        return text

    @staticmethod
    def _set_text(widget: tk.Text, lines) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert("1.0", "\n".join(lines))
        widget.configure(state="disabled")

    def _header(self, root: tk.Tk) -> None:
        """Navy bar at the top: Exostaff logo, name and version, licence, About and Exit."""
        header = tk.Frame(root, bg=HEADER_BG, padx=16, pady=10)
        header.pack(fill=tk.X)
        family = tkfont.nametofont("TkDefaultFont").actual("family")
        brand = tk.Frame(header, bg=HEADER_BG)
        brand.pack(side=tk.LEFT)
        logo = _load_image(LOGO_HEADER_FILE, master=brand)
        if logo is not None:
            label = tk.Label(brand, image=logo, bg=HEADER_BG, bd=0)
            label.image = logo
            label.pack(anchor="w")
        tk.Label(brand, text=f"by {APP_AUTHOR_SHORT}", bg=HEADER_BG, fg=HEADER_FG_MUTED,
                 font=(family, 8, "italic")).pack(anchor="e")
        titles = tk.Frame(header, bg=HEADER_BG)
        titles.pack(side=tk.LEFT, padx=(16, 0))
        tk.Label(titles, text=APP_NAME, bg=HEADER_BG, fg=HEADER_FG,
                 font=(family, 15, "bold")).pack(anchor="w")
        tk.Label(titles, text=f"Version {APP_VERSION}  ·  CADUs and transfer frames: headers, CLCW, Reed-Solomon, packets",
                 bg=HEADER_BG, fg=HEADER_FG_MUTED, font=(family, 9)).pack(anchor="w")
        for text, command in (("Exit", self.exit_app), ("About", self.show_about)):
            tk.Button(header, text=text, command=command, bg=HEADER_HOVER, fg=HEADER_FG,
                      activebackground=HEADER_FG_MUTED, activeforeground=HEADER_BG, relief="flat", bd=0,
                      padx=14, pady=4, cursor="hand2").pack(side=tk.RIGHT, padx=(12, 0))
        license_text = self.license_status.summary()
        if license_text:
            tk.Label(header, text=license_text, bg=HEADER_BG, font=(family, 9),
                     fg=WARNING_ON_DARK if self.license_status.expiring_soon else HEADER_FG_MUTED,
                     ).pack(side=tk.RIGHT, padx=(12, 0))

    def show_about(self) -> None:
        """Version, author and licence of the program (About button and F1)."""
        lines = [f"{APP_NAME} {APP_VERSION}", "Decoder of CCSDS TM CADUs and transfer frames: headers, CLCW, Reed-Solomon, packets",
                 "", f"Author: {APP_AUTHOR}", "Copyright © 2026 Fabio Cialfi (Exostaff). All rights reserved."]
        license_text = self.license_status.summary()
        if license_text:
            lines += ["", license_text]
        messagebox.showinfo("About", "\n".join(lines), parent=self.root)

    # --- file ---

    def exit_app(self) -> None:
        """Close the program: the Exit button, Ctrl+Q and the window close button."""
        self.root.destroy()

    def open_file(self) -> None:
        paths = filedialog.askopenfilenames(title="Open files of CADUs or transfer frames (one or more)",
                                            filetypes=[("All files", "*.*"), ("Binary", "*.bin *.dat *.raw")])
        if paths:
            self.load(list(paths))

    # Overall share of the progress bar taken by each stage of the loading.
    STAGES = {"read": (0, 5), "frames": (5, 30), "reed-solomon": (30, 70), "packets": (70, 85),
              "display": (85, 100), "overview": (0, 100)}
    STAGE_TEXT = {"read": "reading the file", "frames": "reading the frames",
                  "reed-solomon": "Reed-Solomon check", "packets": "rebuilding the packets",
                  "display": "filling the views", "overview": "overview of the files"}

    def _busy(self, on: bool) -> None:
        """Show the progress bar (and block the other controls) while loading."""
        if on:
            self.progress["value"] = 0
            self.progress_label.configure(text="0 %")
            self.progress_label.pack(side=tk.RIGHT)
            self.progress.pack(side=tk.RIGHT, padx=(10, 4))
            self.root.update_idletasks()
            try:
                self.progress.grab_set()  # no clicks elsewhere until the loading is over
            except tk.TclError:  # window not shown yet (file given on the command line)
                pass
            self.root.config(cursor="watch")
        else:
            self.progress.grab_release()
            self.progress.pack_forget()
            self.progress_label.pack_forget()
            self.root.config(cursor="")
        self.root.update()

    def _step(self, stage: str, done: int = 0, total: int = 1) -> None:
        """Move the progress bar to ``done`` of ``total`` within ``stage``."""
        low, high = self.STAGES[stage]
        value = low + (high - low) * min(done, total) / max(total, 1)
        self.progress["value"] = value
        self.progress_label.configure(text=f"{value:.0f} %")
        name = os.path.basename(self.path) if self.path else ""
        self.file_var.set(f"{name}: {self.STAGE_TEXT[stage]}…")
        self.root.update()

    def load(self, paths) -> None:
        """Open one file or several (a list): the first is shown, the others are chosen in File."""
        paths = sorted([paths] if isinstance(paths, str) else paths, key=lambda p: os.path.basename(p).lower())
        previous, self.path = self.path, paths[0]
        self._busy(True)
        files = []
        try:
            for k, path in enumerate(paths):
                self.path = path
                self._step("read", k, len(paths))
                files.append((path, cd.read_input(path)))
        except OSError as exc:
            self.path = previous
            self._busy(False)
            messagebox.showerror("Open", f"Cannot read {path}: {exc}")
            return
        except BaseException:
            self._busy(False)
            raise
        self.files = files
        names = [os.path.basename(p) for p, _ in files]
        self.file_label.pack_forget()
        self.overview = None
        if len(files) > 1:
            self.tabs.add(self.files_tab, text="Files")
            self.choice_box.configure(values=names + [CONCATENATED])
            self.choice_label.pack(side=tk.LEFT)
            self.choice_box.pack(side=tk.LEFT, padx=(4, 10))
        else:
            if str(self.files_tab) in self.tabs.tabs():
                self.tabs.forget(self.files_tab)
            self.choice_label.pack_forget()
            self.choice_box.pack_forget()
        self.file_label.pack(side=tk.LEFT)
        self.choice_var.set(names[0])
        self.select_file(busy=True)

    def _options_key(self) -> tuple:
        return (self.input_var.get(), self.length_var.get(), int(self.interleave_var.get()),
                self.randomized_var.get(), self.fecf_var.get(), self.rs_var.get())

    def _tab_changed(self) -> None:
        """Files and Text report tabs: with several files, the overview of all of them is computed when needed."""
        if len(self.files) > 1 and self.tabs.select() in (str(self.files_tab), str(self.report_tab)):
            self.fill_overview()

    def fill_overview(self) -> None:
        """The Files tab: every file analysed with the options of the toolbar (again when they change)."""
        if self.overview is None or self._overview_key != self._options_key():
            kind = {"Auto": "auto", "CADU": "cadu", "Frames": "frames"}[self.input_var.get()]
            length = self.length_var.get().strip()
            length = int(length) if length.isdigit() and kind != "cadu" else 0
            randomization = self.randomized_var.get().lower()
            shown = self.path
            self._busy(True)
            items = []
            try:
                for k, (path, data) in enumerate(self.files):
                    self.path = path
                    self._step("overview", k, len(self.files))
                    items.append(cd.file_overview(path, data, int(self.interleave_var.get()), kind, length,
                                                  randomization, self.fecf_var.get(), reed_solomon=self.rs_var.get()))
            finally:
                self.path = shown
                self._busy(False)
                self.file_var.set(self._file_line)
            self.overview, self._overview_key = items, self._options_key()
            self._fill_report()
        self.files_tree.delete(*self.files_tree.get_children())
        table = cd.overview_table(self.overview)
        for k, row in enumerate(table[1:], start=1):
            total = len(self.overview) > 1 and k == len(table) - 1
            self.files_tree.insert("", tk.END, iid="total" if total else row[-1], values=row,
                                   tags=("total",) if total else ())

    def _open_from_overview(self, event) -> None:
        item = self.files_tree.identify_row(event.y)
        if item and item != "total":
            self.choice_var.set(item)
            self.select_file()
            self.tabs.select(0)

    def copy_overview(self) -> None:
        if self.overview:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(cd.overview_lines(self.overview)))

    def select_file(self, busy: bool = False) -> None:
        """Show the file chosen in File, or all the files read one after the other as one stream."""
        if not self.files:
            return
        choice = self.choice_var.get()
        if choice == CONCATENATED:
            self.data, self.bounds = cd.concatenate([(os.path.basename(p), d) for p, d in self.files])
            self.path = f"{len(self.files)} files concatenated"
        else:
            self.path, self.data = next((p, d) for p, d in self.files if os.path.basename(p) == choice)
            self.bounds = None
        self.tree.configure(displaycolumns=[c[0] for c in COLUMNS if c[0] != "file" or self.bounds])
        self.reload(busy=busy)

    def reload(self, busy: bool = False) -> None:
        self._sync_checks()
        if not self.path:
            return
        if not busy:
            self._busy(True)
        try:
            self._load()
        finally:
            self._busy(False)

    def _sync_checks(self) -> None:
        """Untick and grey out the options that need the Reed-Solomon check when it is off.

        With the uncorrectable frames excluded, the RS errors left are the corrected frames: the
        box says so.
        """
        frames = self.frame_length > 0
        rs = self.rs_var.get() and not frames  # transfer frames have no Reed-Solomon code
        self.rs_box.state(["disabled"] if frames else ["!disabled"])
        self.interleave_box.state(["disabled"] if self.frame_length and self.length_var.get() != "Auto"
                                  else ["!disabled"])  # Auto falls back to 223 x I
        if not rs:
            self.exclude_var.set(False)
            self.rserr_var.set(False)
        self.exclude_box.state(["!disabled"] if rs else ["disabled"])
        self.rserr_box.state(["!disabled"] if rs else ["disabled"])
        self.rserr_box.configure(text="Only RS-corrected frames" if self.exclude_var.get() else "Only RS errors")

    def _exclude_changed(self) -> None:
        self._sync_checks()
        self.refresh()

    def _input_layout(self, interleave: int) -> tuple[int, str]:
        """(frame length, how it was chosen) of a file of transfer frames; (0, "") for CADUs."""
        kind = self.input_var.get()
        if kind == "Auto":
            kind = "Frames" if cd.detect_input(self.data, interleave) == "frames" else "CADU"
        if kind == "CADU":
            return 0, ""
        text = self.length_var.get().strip()
        if text and text != "Auto":
            try:
                length = int(text)
                if not 8 <= length <= cd.MAX_FRAME_LEN:
                    raise ValueError
                return length, "set"
            except ValueError:
                messagebox.showerror("Frame length", f"The frame length must be 8 to {cd.MAX_FRAME_LEN} bytes, "
                                                     "or Auto.")
                self.length_var.set("Auto")
        length = cd.detect_frame_length(self.data)
        if length:
            return length, "detected"
        return cd.RS_K * interleave, f"not detected: 223 x I"

    def _load(self) -> None:
        interleave = int(self.interleave_var.get())
        self.frame_length, length_how = self._input_layout(interleave)
        self._sync_checks()
        choice = self.randomized_var.get()
        if choice == "Auto":
            self.randomized = cd.detect_randomization(self.data, interleave, self.fecf_var.get(),
                                                      frame_length=self.frame_length)
            how = ("randomized" if self.randomized else "not randomized, already de-randomized") + " (detected)"
        else:
            self.randomized = choice == "Yes"
            how = "randomized" if self.randomized else "not randomized"
        name = os.path.basename(self.path)
        self.rows = cd.decode_all(self.data, interleave, self.randomized, self.fecf_var.get(),
                                  reed_solomon=self.rs_var.get(), progress=self._step,
                                  frame_length=self.frame_length)
        if self.bounds:
            cd.tag_files(self.rows, self.bounds)
        sync_len, block_len, frame_len = cd.layout(interleave, self.frame_length)
        self.cadu_len = sync_len + block_len
        self._step("packets")
        self.packets = cd.packet_statistics(self.data, self.rows, interleave, self.randomized, self.fecf_var.get(),
                                            progress=lambda done, total: self._step("packets", done, total),
                                            frame_length=self.frame_length)
        self._step("display")
        self.refresh(progress=True)
        if self.frame_length:
            self._file_line = (f"{name}: {len(self.data)} bytes; transfer frames of {frame_len} bytes "
                               f"({length_how}), no sync marker, no Reed-Solomon code; frames {how}")
        else:
            self._file_line = (f"{name}: {len(self.data)} bytes; CADU {self.cadu_len} bytes, "
                               f"transfer frame {frame_len} bytes (I = {interleave}); codeblocks {how}")
        self.file_var.set(self._file_line)

    def _unit(self) -> str:
        return "Transfer frames" if self.frame_length else "CADUs"

    def _analysed(self):
        if self.exclude_var.get():
            return [r for r in self.rows if r.get("rs") != "uncorrectable"]
        return self.rows

    def refresh_summary(self) -> None:
        """Summary tab and text report (also after a change of the APIDs checked)."""
        try:
            checked = cd.parse_apids(self.check_var.get())
        except ValueError as exc:
            messagebox.showerror("APIDs", f"{exc}\n\nWrite them as 100 or 15-16, 100.")
            self.check_var.set(cd.DEFAULT_CHECKED_APIDS)
            checked = cd.parse_apids(cd.DEFAULT_CHECKED_APIDS)
        self._checked = checked
        self._fill_report()
        top = self.summary_view.canvas.yview()[0]
        self.summary_view.update_view(self._analysed(), self.rows, len(self.data), self.cadu_len, self.packets,
                                      unit=self._unit())
        self.root.update_idletasks()
        self.summary_view.canvas.yview_moveto(top)  # keep the page where it was

    def _fill_report(self) -> None:
        """Text report: with several files the overview of all of them, then the full summary of the file
        shown (every frame and gap listed, not only the first 50)."""
        lines = []
        if len(self.files) > 1:
            if self.overview is not None and self._overview_key == self._options_key():
                lines += cd.overview_lines(self.overview)
            else:
                lines.append(f"Overview of the {len(self.files)} files: computed when the Files or Text report tab is opened.")
            lines += ["", "=" * 100, f"{self.path if self.bounds else os.path.basename(self.path)}:", ""]
        if self.rows:
            lines += cd.summary(self._analysed(), len(self.data), self.cadu_len, max_listed=10 ** 9,
                                all_rows=self.rows, packets=self.packets,
                                checked_apids=getattr(self, "_checked", None), unit=self._unit())
        else:
            lines.append("The file is shorter than one transfer frame." if self.frame_length
                         else "No sync marker 1ACFFC1D found.")
        self._set_text(self.summary_text, lines)

    def refresh(self, progress: bool = False) -> None:
        """Summary and frame table of the frames analysed (without the uncorrectable ones when excluded)."""
        self.refresh_summary()
        self.usual = cd.usual_clcw(self._analysed())
        vcs = sorted({r["vcid"] for r in self._analysed()})  # without the excluded frames
        self.vc_box.configure(values=[ALL] + [str(v) for v in vcs])
        if self.vc_var.get() not in self.vc_box.cget("values"):
            self.vc_var.set(ALL)
        self.fill_frames(progress)
        self.detail_view._message("Select a frame to see its content.")

    # --- frames ---

    def _visible_rows(self):
        vc = self.vc_var.get()
        for r in self._analysed():
            if vc != ALL and r["vcid"] != int(vc):
                continue
            if self.rserr_var.get() and r.get("rs", "ok") == "ok":
                continue
            if self.hide_idle_var.get() and r["first_header_pointer"] == 0x7FE:
                continue
            if self.odd_var.get() and not cd.clcw_differences(r, self.usual):
                continue
            yield r

    def fill_frames(self, progress: bool = False) -> None:
        self.tree.delete(*self.tree.get_children())
        shown = 0
        total = len(self._analysed())
        for r in self._visible_rows():
            if r["ocf_flag"]:
                flags = ",".join(name for key, name in (
                    ("no_rf_available", "NoRF"), ("no_bit_lock", "NoLock"), ("lockout", "Lockout"),
                    ("wait", "Wait"), ("retransmit", "Retx")) if r[key]) or "–"
                clcw = (r["clcw_vcid"], r["cop_in_effect"], flags, r["report_value"])
            else:
                clcw = ("–", "–", "–", "–")
            self.tree.insert("", tk.END, iid=str(r["n"]), values=(
                r["n"], r.get("file", ""), r.get("file_offset", r["offset"]), r["scid"], r["vcid"], r["mc_count"], r["vc_count"],
                cd.first_header_pointer_text(r["first_header_pointer"]), r["segment_length_id"], r["ocf_flag"],
                *clcw, cd.rs_text(r)))
            shown += 1
            if progress and shown % 2000 == 0:
                self._step("display", shown, total)
        analysed = len(self._analysed())
        excluded = len(self.rows) - analysed
        self.count_var.set(f"{shown} of {analysed} frames" + (f" ({excluded} excluded)" if excluded else ""))

    def goto(self) -> None:
        text = self.goto_var.get().strip()
        try:
            number = cd.parse_selection(text, len(self.rows))[0]
        except (ValueError, IndexError):
            messagebox.showerror("Go to", f"Enter a frame number from 1 to {len(self.rows)}.")
            return
        if not self.tree.exists(str(number)):  # hidden by the filters
            self.vc_var.set(ALL)
            self.hide_idle_var.set(False)
            self.odd_var.set(False)
            self.rserr_var.set(False)
            if self.exclude_var.get() and self.rows[number - 1].get("rs") == "uncorrectable":
                self.exclude_var.set(False)
                self._sync_checks()
                self.refresh()
            self.fill_frames()
        self.tree.selection_set(str(number))
        self.tree.see(str(number))

    def open_cadu(self, number: int) -> None:
        """Show CADU ``number`` in the Frames tab (from the summary tables)."""
        self.tabs.select(1)
        self.goto_var.set(str(number))
        self.goto()

    def copy_report(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.summary_text.get("1.0", tk.END))

    def show_selected(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        number = int(selection[0])
        self.detail_view.show(number, self.rows[number - 1], self.data, int(self.interleave_var.get()),
                              self.randomized, self.fecf_var.get(), reed_solomon=self.rs_var.get(),
                              dump=self.dump_var.get(), usual=self.usual, frame_length=self.frame_length)

    def export_packets(self) -> None:
        """Packets of each APID to <folder>/apid_NNNN.bin, and their list to packets.csv."""
        if not self.rows:
            messagebox.showinfo("Export packets", "Open a file first.")
            return
        folder = filedialog.askdirectory(title="Folder for the packets (one .bin file per APID)",
                                         mustexist=False)
        if not folder:
            return
        self._busy(True)
        error = None
        try:
            counts = cd.export_packets(self.data, self.rows, folder, int(self.interleave_var.get()), self.randomized,
                                       self.fecf_var.get(),
                                       progress=lambda done, total: self._step("packets", done, total),
                                       frame_length=self.frame_length)
        except OSError as exc:
            error = exc
        finally:
            self._busy(False)
            self.file_var.set(self._file_line)
        if error:
            messagebox.showerror("Export packets", f"Cannot write to {folder}: {error}")
            return
        listed = "\n".join(f"APID {apid}: {n} packets → apid_{apid:04d}.bin" for apid, n in sorted(counts.items()))
        messagebox.showinfo("Export packets", f"Written to {folder}:\n\n{listed or 'no packets'}\n\n"
                            "packets.csv lists every packet (idle packets, APID 2047, left out).")

    def export_csv(self) -> None:
        if not self.rows:
            messagebox.showinfo("Export CSV", "Open a file first.")
            return
        path = filedialog.asksaveasfilename(title="Export the frames", defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        rows = self._analysed()  # without the uncorrectable frames when they are excluded
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
            writer.writeheader()
            writer.writerows(rows)
        messagebox.showinfo("Export CSV", f"{len(rows)} frames written to {path}")


def _close_splash() -> None:
    """Close the splash screen of the packaged executable (shown by the bootloader, see cadu_gui.spec)."""
    try:
        import pyi_splash  # only in a PyInstaller build with a splash screen

        pyi_splash.close()
    except Exception:
        pass


def main() -> None:
    root = tk.Tk()
    root.withdraw()  # shown once complete: the splash stays up until then
    # Packaged executable only (see licensing.py): stop here with an explanation if the licence is
    # missing, invalid or expired.
    status = licensing.check_license()
    if not status.valid:
        _close_splash()
        _set_icon(root)
        messagebox.showerror(f"{licensing.PRODUCT}: licence not valid",
                             f"{status.message}\n\nContact the supplier for a new licence file.", parent=root)
        root.destroy()
        raise SystemExit(1)
    CaduWindow(root, sys.argv[1:] or None, license_status=status)  # files dropped on the icon
    root.deiconify()
    root.update_idletasks()
    _close_splash()
    root.mainloop()


if __name__ == "__main__":
    main()
