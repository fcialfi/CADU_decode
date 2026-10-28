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
    ("n", "#", 70), ("offset", "Offset", 90), ("scid", "SCID", 55), ("vcid", "VC", 40), ("mc_count", "MCFC", 55),
    ("vc_count", "VCFC", 55), ("fhp", "FHP", 150), ("segment_length_id", "Seg", 40), ("ocf_flag", "OCF", 40),
    ("clcw_vcid", "CLCW VC", 80), ("cop_in_effect", "COP", 45), ("flags", "CLCW flags", 140),
    ("report_value", "Report", 60), ("rs", "RS", 75),
)
ALL = "All"

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
        self.root = root
        self.data = b""
        self.rows: list[dict] = []
        self.path = None
        self.cadu_len = 0
        self.randomized = True
        self.usual = None
        self.packets = None
        self.license_status = license_status or licensing.check_license()
        root.title(f"{APP_NAME} {APP_VERSION}")
        root.geometry("1250x880")
        _set_icon(root)
        self.mono = tkfont.nametofont("TkFixedFont").copy()
        self.mono.configure(size=10)
        self._header(root)

        bar = ttk.Frame(root, padding=(10, 8))
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="Open…", command=self.open_file).pack(side=tk.LEFT)
        self.interleave_var = tk.IntVar(value=5)
        self.randomized_var = tk.StringVar(value="Auto")
        self.fecf_var = tk.BooleanVar(value=False)
        ttk.Label(bar, text="  RS interleaving I").pack(side=tk.LEFT)
        ttk.Spinbox(bar, from_=1, to=8, width=3, textvariable=self.interleave_var,
                    command=self.reload).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(bar, text="Randomized").pack(side=tk.LEFT)
        box = ttk.Combobox(bar, textvariable=self.randomized_var, values=("Auto", "Yes", "No"), width=6,
                           state="readonly")
        box.pack(side=tk.LEFT, padx=(4, 0))
        box.bind("<<ComboboxSelected>>", lambda _e: self.reload())
        ttk.Checkbutton(bar, text="FECF", variable=self.fecf_var, command=self.reload).pack(side=tk.LEFT, padx=(8, 0))
        self.rs_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Reed-Solomon check", variable=self.rs_var,
                        command=self.reload).pack(side=tk.LEFT, padx=(12, 0))
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
        self.file_var = tk.StringVar(value="No file: press Open… (CADUs starting with the sync marker 1ACFFC1D)")
        status = ttk.Frame(root, padding=(10, 0))
        status.pack(fill=tk.X)
        ttk.Label(status, textvariable=self.file_var).pack(side=tk.LEFT)
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
        self.tree = ttk.Treeview(table_box, columns=[c[0] for c in COLUMNS], show="headings", height=9)
        for key, heading, width in COLUMNS:
            self.tree.heading(key, text=heading)
            self.tree.column(key, width=width, anchor="e" if key not in ("fhp", "flags") else "w", stretch=False)
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
        report = ttk.Frame(self.tabs)
        self.tabs.add(report, text="Text report")
        ttk.Button(report, text="Copy to clipboard", command=self.copy_report).pack(anchor="w", pady=(4, 4))
        self.summary_text = self._text(report)
        self.summary_text.master.pack(fill=tk.BOTH, expand=True)

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
        tk.Label(titles, text=f"Version {APP_VERSION}  ·  CCSDS TM CADUs: frames, Reed-Solomon, packets",
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
        lines = [f"{APP_NAME} {APP_VERSION}", "Decoder of CCSDS TM CADUs: transfer frames, Reed-Solomon, packets",
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
        path = filedialog.askopenfilename(title="Open a file of CADUs",
                                          filetypes=[("All files", "*.*"), ("Binary", "*.bin *.dat *.raw")])
        if path:
            self.load(path)

    # Overall share of the progress bar taken by each stage of the loading.
    STAGES = {"read": (0, 5), "frames": (5, 30), "reed-solomon": (30, 70), "packets": (70, 85),
              "display": (85, 100)}
    STAGE_TEXT = {"read": "reading the file", "frames": "reading the frames",
                  "reed-solomon": "Reed-Solomon check", "packets": "rebuilding the packets",
                  "display": "filling the views"}

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

    def load(self, path: str) -> None:
        previous, self.path = self.path, path
        self._busy(True)
        try:
            self._step("read")
            data = cd.read_input(path)
        except OSError as exc:
            self.path = previous
            self._busy(False)
            messagebox.showerror("Open", f"Cannot read {path}: {exc}")
            return
        except BaseException:
            self._busy(False)
            raise
        self.data = data
        self.reload(busy=True)

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
        rs = self.rs_var.get()
        if not rs:
            self.exclude_var.set(False)
            self.rserr_var.set(False)
        self.exclude_box.state(["!disabled"] if rs else ["disabled"])
        self.rserr_box.state(["!disabled"] if rs else ["disabled"])
        self.rserr_box.configure(text="Only RS-corrected frames" if self.exclude_var.get() else "Only RS errors")

    def _exclude_changed(self) -> None:
        self._sync_checks()
        self.refresh()

    def _load(self) -> None:
        interleave = int(self.interleave_var.get())
        choice = self.randomized_var.get()
        if choice == "Auto":
            self.randomized = cd.detect_randomization(self.data, interleave, self.fecf_var.get())
            how = ("randomized" if self.randomized else "not randomized, already de-randomized") + " (detected)"
        else:
            self.randomized = choice == "Yes"
            how = "randomized" if self.randomized else "not randomized"
        name = os.path.basename(self.path)
        self.rows = cd.decode_all(self.data, interleave, self.randomized, self.fecf_var.get(),
                                  reed_solomon=self.rs_var.get(), progress=self._step)
        self.cadu_len = len(cd.ASM) + cd.RS_N * interleave
        self._step("packets")
        self.packets = cd.packet_statistics(self.data, self.rows, interleave, self.randomized, self.fecf_var.get(),
                                            progress=lambda done, total: self._step("packets", done, total))
        self._step("display")
        self.refresh(progress=True)
        self._file_line = (f"{name}: {len(self.data)} bytes; CADU {self.cadu_len} bytes, "
                           f"transfer frame {cd.RS_K * interleave} bytes (I = {interleave}); codeblocks {how}")
        self.file_var.set(self._file_line)

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
        if self.rows:
            self._set_text(self.summary_text, cd.summary(self._analysed(), len(self.data), self.cadu_len,
                                                         all_rows=self.rows, packets=self.packets,
                                                         checked_apids=checked))
        else:
            self._set_text(self.summary_text, ["No sync marker 1ACFFC1D found."])
        top = self.summary_view.canvas.yview()[0]
        self.summary_view.update_view(self._analysed(), self.rows, len(self.data), self.cadu_len, self.packets)
        self.root.update_idletasks()
        self.summary_view.canvas.yview_moveto(top)  # keep the page where it was

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
                r["n"], r["offset"], r["scid"], r["vcid"], r["mc_count"], r["vc_count"],
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
            messagebox.showerror("Go to", f"Enter a CADU number from 1 to {len(self.rows)}.")
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
                              dump=self.dump_var.get(), usual=self.usual)

    def export_packets(self) -> None:
        """Packets of each APID to <folder>/apid_NNNN.bin, and their list to packets.csv."""
        if not self.rows:
            messagebox.showinfo("Export packets", "Open a file of CADUs first.")
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
                                       progress=lambda done, total: self._step("packets", done, total))
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
            messagebox.showinfo("Export CSV", "Open a file of CADUs first.")
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
    CaduWindow(root, sys.argv[1] if len(sys.argv) > 1 else None, license_status=status)
    root.deiconify()
    root.update_idletasks()
    _close_splash()
    root.mainloop()


if __name__ == "__main__":
    main()
