"""Windowed kitchen application. Runtime data lives outside the installation."""
from __future__ import annotations

import csv
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import sqlite3
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import urllib.request
from urllib.parse import urlencode, urlparse
from contextlib import closing

from edge_sync import EdgeSynchronizer, SyncConfig

DATA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PattyOps"
TABLES = {"Cooking events": "patty_events", "Cooking sessions": "cooking_sessions", "Patties": "patties"}
EVENTS = ("All events", "DETECTED", "STATE_CHANGED", "FLIPPED", "REMOVED")
PAGE_SIZE = 100
ASSETS = Path(__file__).resolve().parent / "assets"
COLORS = {
    "ink": "#282828", "brand": "#CB500B", "paper": "#FFFFFF",
    "surface": "#F3F4F4", "muted": "#626262", "line": "#D6D8D8",
}


def configure_brand(root):
    """One palette for native controls, including focus and disabled states."""
    root.configure(background=COLORS["paper"])
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=("Segoe UI", 11), background=COLORS["paper"], foreground=COLORS["ink"])
    style.configure("TButton", padding=(16, 12), background=COLORS["paper"], bordercolor=COLORS["line"], lightcolor=COLORS["paper"], darkcolor=COLORS["paper"], focuscolor=COLORS["brand"])
    style.map("TButton", background=[("disabled", COLORS["surface"]), ("active", "#FFF1E8")], foreground=[("disabled", "#747474")], bordercolor=[("focus", COLORS["brand"])])
    style.configure("Compact.TButton", padding=(14, 6))
    # A deeper orange keeps white button text above 4.5:1 contrast.
    style.configure("Primary.TButton", font=("Segoe UI", 12, "bold"), background="#B84608", foreground="white", bordercolor="#B84608", focuscolor="white", padding=(24, 14))
    style.map("Primary.TButton", background=[("disabled", COLORS["surface"]), ("pressed", "#963900"), ("active", "#AF4105")], foreground=[("disabled", "#747474"), ("!disabled", "white")], bordercolor=[("disabled", COLORS["line"]), ("!disabled", COLORS["brand"])])
    style.configure("TNotebook", background=COLORS["paper"], borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", padding=(22, 14), background=COLORS["surface"], borderwidth=0, font=("Segoe UI", 11, "bold"), focuscolor=COLORS["brand"])
    style.map("TNotebook.Tab", background=[("selected", COLORS["paper"]), ("active", "#FFF1E8")], foreground=[("selected", "#A53B00"), ("!selected", COLORS["muted"])])
    style.configure("Title.TLabel", font=("Segoe UI", 25, "bold"))
    style.configure("Heading.TLabel", font=("Segoe UI", 18, "bold"))
    style.configure("Muted.TLabel", foreground=COLORS["muted"])
    style.configure("Surface.TFrame", background=COLORS["surface"])
    style.configure("Surface.TLabel", background=COLORS["surface"])
    style.configure("TEntry", padding=9, fieldbackground=COLORS["paper"], bordercolor=COLORS["line"])
    style.configure("TCombobox", padding=8, bordercolor=COLORS["line"], arrowsize=16)
    style.map("TCombobox", fieldbackground=[("readonly", COLORS["paper"])], selectbackground=[("readonly", COLORS["paper"])], selectforeground=[("readonly", COLORS["ink"])])
    style.configure("Treeview", rowheight=38, background=COLORS["paper"], fieldbackground=COLORS["paper"], borderwidth=0)
    style.configure("Treeview.Heading", font=("Segoe UI", 11, "bold"), background=COLORS["surface"], padding=(10, 12), relief="flat")
    style.map("Treeview", background=[("selected", "#FFE5D3")], foreground=[("selected", COLORS["ink"])])
    style.configure("TScrollbar", background="#D6D8D8", troughcolor=COLORS["surface"], borderwidth=0, arrowsize=16)


def local_records(database, table, before=None, event_type=None):
    if table not in TABLES.values():
        raise ValueError("Choose a supported record type.")
    path = Path(database).resolve()
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        conditions, values = [], []
        if before is not None:
            conditions.append("id < ?")
            values.append(before)
        if event_type and table == "patty_events":
            conditions.append("event_type = ?")
            values.append(event_type)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        return [dict(row) for row in conn.execute(
            f"SELECT * FROM {table}{where} ORDER BY id DESC LIMIT ?", (*values, PAGE_SIZE))]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Cloud address redirected. Ask support for the final HTTPS address.")


def cloud_records(settings, before=None, event_type=None):
    config = sync_config(settings)
    config.validate()
    query = {"limit": PAGE_SIZE}
    if before is not None:
        query["before_id"] = before
    if event_type:
        query["event_type"] = event_type
    request = urllib.request.Request(config.cloud_url + "/v1/events?" + urlencode(query), headers={
        "Authorization": "Bearer " + config.device_token, "X-Device-ID": config.device_id})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
        return json.load(response)


def sync_config(settings):
    url = settings["cloud_url"].strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Enter a valid HTTPS cloud address without credentials or query parameters.")
    return SyncConfig(Path(settings["database"]), url, settings["device_id"], settings["token"])


def export_records(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        # Prevent spreadsheet formula execution when opening exported text.
        writer.writerows({key: "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
                         for key, value in row.items()} for row in rows)


class RecordBrowser(ttk.Frame):
    def __init__(self, parent, app, cloud=False):
        super().__init__(parent, padding=18)
        self.app, self.cloud = app, cloud
        self.rows, self.history, self.before = [], [], None
        self.busy = False
        self.kind = tk.StringVar(value="Cooking events")
        self.event = tk.StringVar(value=EVENTS[0])
        ttk.Label(self, text="Cloud event logs" if cloud else "Saved records", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(self, text="Browse cooking events uploaded by this kitchen." if cloud else "Review cooking events, sessions, and patties saved on this computer.", style="Muted.TLabel").pack(anchor="w", pady=(6, 18))
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x")
        if not cloud:
            selector = ttk.Combobox(toolbar, textvariable=self.kind, values=list(TABLES), state="readonly", width=20)
            selector.pack(side="left", padx=(0, 10))
            selector.bind("<<ComboboxSelected>>", lambda _: self.refresh())
        selector = ttk.Combobox(toolbar, textvariable=self.event, values=EVENTS, state="readonly", width=18)
        selector.pack(side="left")
        selector.bind("<<ComboboxSelected>>", lambda _: self.refresh())
        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(12, 0))
        for title, action in (("Refresh", self.refresh), ("Newer", self.newer), ("Older", self.older), ("View details", self.details), ("Export this page", self.export)):
            ttk.Button(actions, text=title, command=action).pack(side="left", padx=(0, 8))
        self.notice = tk.StringVar(value="Choose Refresh to load records.")
        ttk.Label(self, textvariable=self.notice, wraplength=880, style="Muted.TLabel").pack(anchor="w", pady=12)
        ttk.Label(self, text="Select a row and choose View details. Records are read-only.", style="Muted.TLabel").pack(side="bottom", anchor="w", pady=(12, 0))
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(body, show="headings")
        self.tree.tag_configure("alternate", background="#F6F7F7")
        y = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        x = ttk.Scrollbar(body, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        y.grid(row=0, column=1, sticky="ns")
        x.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", self.details)
        self.tree.bind("<Return>", self.details)

    def refresh(self):
        if self.busy:
            return
        self.before, self.history = None, []
        self.load()

    def older(self):
        if not self.busy and len(self.rows) == PAGE_SIZE:
            self.history.append(self.before)
            self.before = self.rows[-1]["id"]
            self.load()

    def newer(self):
        if not self.busy and self.history:
            self.before = self.history.pop()
            self.load()

    def load(self):
        self.busy = True
        self.notice.set("Loading records...")
        settings = self.app.settings.copy()
        table, before = TABLES[self.kind.get()], self.before
        event = None if self.event.get() == EVENTS[0] else self.event.get()
        def work():
            try:
                rows = cloud_records(settings, before, event) if self.cloud else local_records(settings["database"], table, before, event)
                self.app.messages.put((self.loaded, rows))
            except Exception as error:
                self.app.messages.put((self.failed, self.app.friendly_error(error)))
        threading.Thread(target=work, daemon=True).start()

    def failed(self, error):
        self.busy = False
        self.rows = []
        self.tree.delete(*self.tree.get_children())
        self.notice.set(error + " Choose Refresh to retry.")

    def loaded(self, rows):
        self.busy, self.rows = False, rows
        self.tree.delete(*self.tree.get_children())
        columns = list(rows[0]) if rows else []
        self.tree.configure(columns=columns)
        for column in columns:
            self.tree.heading(column, text=column.replace("_", " ").capitalize())
            self.tree.column(column, width=180 if "at" in column else 130, stretch=False)
        for index, row in enumerate(rows):
            self.tree.insert("", "end", iid=str(index), values=["" if value is None else value for value in row.values()], tags=("alternate",) if index % 2 else ())
        self.notice.set(f"Page {len(self.history) + 1} - {len(rows)} records. Use Older to browse history." if rows else "No records on this page. Try Newer or Refresh; new events appear after tracking and cloud sync.")

    def details(self, _=None):
        selected = self.tree.selection()
        if not selected:
            self.notice.set("Select a record in the table to view its details.")
            return
        window = tk.Toplevel(self)
        window.title("Record details")
        text = tk.Text(window, wrap="word", width=90, height=25, padx=18, pady=18, background=COLORS["paper"], foreground=COLORS["ink"], relief="flat")
        text.pack(fill="both", expand=True)
        text.insert("1.0", "\n\n".join(f"{key.replace('_', ' ').capitalize()}: {value}" for key, value in self.rows[int(selected[0])].items()))
        text.configure(state="disabled")

    def export(self):
        if not self.rows:
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("Spreadsheet CSV", "*.csv")])
        if path:
            try:
                export_records(path, self.rows)
            except OSError:
                messagebox.showerror("Could not export", "Choose a folder you can save files in.")


class KitchenApp(tk.Tk):
    def __init__(self, data_dir=DATA):
        super().__init__()
        self.data = Path(data_dir)
        self.data.mkdir(parents=True, exist_ok=True)
        self.title("PattyOps Kitchen")
        self.geometry("1180x780")
        self.minsize(1000, 680)
        self.messages, self.frames = queue.Queue(), queue.Queue(maxsize=1)
        self.stop_tracking, self.stop_sync = threading.Event(), threading.Event()
        self.worker, self.sync_worker = None, None
        self.closing = False
        self.settings = {"model": "", "source": "0", "database": str(self.data / "pattyops.db"), "cloud_url": "", "device_id": "", "token": ""}
        self.config_error = False
        try:
            saved = json.loads((self.data / "settings.json").read_text(encoding="utf-8"))
            if not isinstance(saved, dict) or not all(isinstance(v, str) for v in saved.values()):
                raise ValueError("Invalid settings")
            self.settings.update({k: v for k, v in saved.items() if k in self.settings})
        except FileNotFoundError:
            pass
        except (ValueError, OSError):
            self.config_error = True
        configure_brand(self)
        from PIL import Image, ImageTk
        with Image.open(ASSETS / "pattyops.png") as mark:
            self.brand_icon = ImageTk.PhotoImage(mark.resize((56, 56), Image.Resampling.LANCZOS))
            self.window_icon = ImageTk.PhotoImage(mark.resize((32, 32), Image.Resampling.LANCZOS))
        self.iconphoto(True, self.window_icon)
        if os.name == "nt":
            self.iconbitmap(str(ASSETS / "pattyops.ico"))
        header = ttk.Frame(self, padding=(24, 12, 24, 12))
        header.pack(fill="x")
        ttk.Label(header, image=self.brand_icon).pack(side="left", padx=(0, 14))
        identity = ttk.Frame(header)
        identity.pack(side="left")
        ttk.Label(identity, text="PattyOps", style="Title.TLabel").pack(anchor="w")
        ttk.Label(identity, text="Kitchen workspace", style="Muted.TLabel").pack(anchor="w")
        ttk.Label(header, text="Camera tracking and cooking records", style="Muted.TLabel").pack(side="right")
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.tabs.enable_traversal()
        home = ttk.Frame(self.tabs, padding=20)
        self.tabs.add(home, text="Kitchen")
        self.status = tk.StringVar(value="Ready. Choose Start kitchen to begin." if self.settings["model"] else "First visit? Open Setup to choose the model and camera.")
        ttk.Label(home, text="Your kitchen", style="Heading.TLabel").pack(anchor="w", pady=(0, 6))
        ttk.Label(home, textvariable=self.status, wraplength=900).pack(anchor="w")
        self.cloud_status = tk.StringVar(value="Cloud connection not configured. Local tracking is available.")
        buttons = ttk.Frame(home)
        buttons.pack(fill="x", pady=16)
        self.start_button = ttk.Button(buttons, text="Start kitchen", command=self.start, style="Primary.TButton")
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Stop kitchen", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=12)
        ttk.Button(buttons, text="Open setup", command=lambda: self.tabs.select(self.setup)).pack(side="right")
        self.preview = ttk.Label(home, text="Camera preview\n\nChoose Start kitchen to see the camera here.", anchor="center", justify="center", background=COLORS["surface"], foreground=COLORS["muted"], font=("Segoe UI", 14))
        self.preview.pack(fill="both", expand=True)
        cloud_strip = ttk.Frame(home, style="Surface.TFrame", padding=(14, 12))
        cloud_strip.pack(fill="x", pady=(12, 0))
        ttk.Label(cloud_strip, text="Cloud connection", style="Surface.TLabel", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(cloud_strip, textvariable=self.cloud_status, style="Surface.TLabel", wraplength=860).pack(anchor="w", pady=(4, 0))
        ttk.Label(home, text="Keep PattyOps open during the shift. Cooking events are saved even when the internet is down.", style="Muted.TLabel", wraplength=900).pack(anchor="w", pady=(12, 0))
        self.local = RecordBrowser(self.tabs, self)
        self.cloud = RecordBrowser(self.tabs, self, True)
        self.tabs.add(self.local, text="Saved records")
        self.tabs.add(self.cloud, text="Cloud event logs")
        self.setup_tab()
        support = ttk.Frame(self.tabs, padding=18)
        self.tabs.add(support, text="Support logs")
        ttk.Label(support, text="Support logs", style="Heading.TLabel").pack(anchor="w", pady=(0, 6))
        ttk.Label(support, text="Recent activity from this computer. Share relevant details with your support contact.", style="Muted.TLabel").pack(anchor="w")
        ttk.Button(support, text="Refresh logs", command=self.refresh_logs).pack(anchor="w", pady=10)
        self.logs = tk.Text(support, wrap="word", state="disabled", font=("Consolas", 10), padx=14, pady=14, background=COLORS["surface"], foreground=COLORS["ink"], relief="flat")
        self.logs.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(100, self.poll)
        self.begin_sync()
        if self.config_error:
            self.after(200, lambda: messagebox.showwarning("Setup needs attention", "Saved settings could not be read. Open Setup and save the kitchen settings again."))

    def setup_tab(self):
        self.setup = ttk.Frame(self.tabs, padding=18)
        self.tabs.add(self.setup, text="Setup")
        ttk.Button(self.setup, text="Save setup", command=self.save, style="Primary.TButton").pack(side="bottom", anchor="w", pady=(8, 0))
        ttk.Label(self.setup, text="Set up this kitchen once", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(self.setup, text="Choose the camera and model, then add your kitchen's cloud connection.", style="Muted.TLabel").pack(anchor="w", pady=(6, 12))
        sections = ttk.Frame(self.setup)
        sections.pack(fill="x")
        sections.columnconfigure((0, 1), weight=1, uniform="setup")
        self.fields = {}
        labels = {"model": "Trained model file", "source": "Camera number or video file", "database": "Saved records file", "cloud_url": "Cloud address (HTTPS)", "device_id": "Kitchen ID", "token": "Connection key"}
        for column, (title, keys) in enumerate((
            ("On this computer", ("model", "source", "database")),
            ("Cloud connection", ("cloud_url", "device_id", "token")),
        )):
            group = ttk.Frame(sections, padding=(0 if column == 0 else 20, 0, 20 if column == 0 else 0, 0))
            group.grid(row=0, column=column, sticky="nsew")
            group.columnconfigure(0, weight=1)
            ttk.Label(group, text=title, font=("Segoe UI", 12, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
            for index, key in enumerate(keys):
                row = 1 + index * 2
                ttk.Label(group, text=labels[key]).grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 5))
                value = tk.StringVar(value=self.settings[key])
                self.fields[key] = value
                ttk.Entry(group, textvariable=value, show="*" if key == "token" else "", width=20).grid(row=row + 1, column=0, sticky="ew")
                if column == 0:
                    ttk.Button(group, text="Browse", command=lambda k=key: self.browse(k), style="Compact.TButton").grid(row=row + 1, column=1, padx=(8, 0))
        ttk.Label(self.setup, text="Camera 0 is your first USB camera. Leave cloud fields blank for local use.", style="Muted.TLabel", wraplength=840).pack(anchor="w", pady=8)

    def browse(self, key):
        if key == "database":
            path = filedialog.asksaveasfilename(defaultextension=".db", confirmoverwrite=False, filetypes=[("PattyOps database", "*.db")])
        else:
            path = filedialog.askopenfilename(filetypes=[("Model", "*.pt")] if key == "model" else [("Video", "*.mp4 *.avi *.mov *.mkv"), ("All files", "*.*")])
        if path:
            self.fields[key].set(path)

    def save(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Stop the kitchen first", "Stop the kitchen before changing its setup.")
            return
        settings = {key: value.get().strip() for key, value in self.fields.items()}
        try:
            if not Path(settings["model"]).is_file():
                raise ValueError("Choose the trained model file supplied by your installer.")
            if not settings["source"] or not settings["database"]:
                raise ValueError("Choose a camera or video and a saved records file.")
            if any(settings[key] for key in ("cloud_url", "device_id", "token")):
                sync_config(settings).validate()
            settings["model"] = str(Path(settings["model"]).resolve())
            settings["database"] = str(Path(settings["database"]).resolve())
            Path(settings["database"]).parent.mkdir(parents=True, exist_ok=True)
            temporary = self.data / "settings.tmp"
            temporary.write_text(json.dumps(settings, indent=2), encoding="utf-8")
            temporary.replace(self.data / "settings.json")
        except (ValueError, OSError) as error:
            messagebox.showerror("Setup needs attention", str(error))
            return
        self.settings = settings
        self.stop_sync.set()
        self.status.set("Setup saved. Choose Start kitchen to begin.")
        self.after(100, self.restart_sync)
        self.tabs.select(0)

    def restart_sync(self):
        if self.closing:
            return
        if self.sync_worker and self.sync_worker.is_alive():
            self.after(200, self.restart_sync)
        else:
            self.begin_sync()

    def begin_sync(self):
        if self.sync_worker and self.sync_worker.is_alive():
            return
        if not self.settings["cloud_url"]:
            self.cloud_status.set("Cloud connection not configured. Records stay on this computer.")
            return
        settings = self.settings.copy()
        self.stop_sync = threading.Event()
        stop = self.stop_sync
        def work():
            while not stop.is_set():
                try:
                    config = sync_config(settings)
                    synchronizer = EdgeSynchronizer(config)
                    if config.database_path.is_file():
                        count = synchronizer.sync_once()
                        status = f"Cloud connected. Last check: {datetime.now():%H:%M:%S}. Sent {count} events."
                    else:
                        synchronizer.heartbeat()
                        status = "Cloud connected. Start the kitchen to create records."
                except Exception as error:
                    status = self.friendly_error(error) + " Saved events will retry automatically."
                self.messages.put((self.cloud_status.set, status))
                stop.wait(5)
        self.sync_worker = threading.Thread(target=work, daemon=True)
        self.sync_worker.start()

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        if not Path(self.settings["model"]).is_file():
            self.tabs.select(self.setup)
            messagebox.showinfo("Choose a model", "Open Setup and choose the trained model file first.")
            return
        settings = self.settings.copy()
        self.stop_tracking.clear()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("Starting camera and loading the model...")
        def frame(image):
            try:
                self.frames.put_nowait(image)
            except queue.Full:
                pass
        def work():
            try:
                from pattyops import build_parser, run_tracking
                args = build_parser().parse_args(["run", "--model", settings["model"], "--source", settings["source"], "--db", settings["database"]])
                result = run_tracking(args, self.stop_tracking, frame)
                status = "Kitchen stopped. Records have been saved." if result == 0 else "Could not start. Check camera and model in Setup; see Support logs."
            except Exception:
                logging.exception("Kitchen tracking failed")
                status = "Tracking stopped unexpectedly. Check the camera and see Support logs."
            self.messages.put((self.finished, status))
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def finished(self, status):
        self.status.set(status)
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        while not self.frames.empty():
            self.frames.get_nowait()
        self.preview.configure(image="", text="Kitchen stopped. Choose Start kitchen to resume.")
        self.preview.image = None

    def stop(self):
        self.stop_tracking.set()
        self.status.set("Stopping and saving records. Please wait...")
        self.stop_button.configure(state="disabled")

    @staticmethod
    def friendly_error(error):
        if getattr(error, "code", None) in (401, 403, 422):
            return "Cloud sign-in failed. Check the kitchen ID and connection key in Setup."
        if isinstance(error, ValueError):
            return "Cloud setup needs attention. Check the address, kitchen ID and connection key."
        if isinstance(error, sqlite3.Error):
            return "Could not read saved records. Check the database file in Setup."
        return "Cloud is unavailable. Check the internet connection and cloud address."

    def refresh_logs(self):
        parts = []
        for name in ("activity.log", "runtime.log"):
            path = self.data / name
            if path.exists():
                with path.open("rb") as stream:
                    stream.seek(max(0, path.stat().st_size - 60000))
                    parts.append(name + "\n" + stream.read().decode("utf-8", errors="replace"))
        content = "\n\n".join(parts) or "No support logs yet."
        if self.settings["token"]:
            content = content.replace(self.settings["token"], "[connection key hidden]")
        self.logs.configure(state="normal")
        self.logs.delete("1.0", "end")
        self.logs.insert("1.0", content)
        self.logs.configure(state="disabled")

    def poll(self):
        while not self.messages.empty():
            callback, value = self.messages.get_nowait()
            callback(value)
        if not self.frames.empty() and not self.stop_tracking.is_set():
            from PIL import Image, ImageTk
            image = Image.fromarray(self.frames.get_nowait())
            image.thumbnail((max(320, self.preview.winfo_width()), max(240, self.preview.winfo_height())))
            photo = ImageTk.PhotoImage(image)
            self.preview.configure(image=photo, text="")
            self.preview.image = photo
            self.status.set("Kitchen running. Recording cooking events.")
        if self.closing and not (self.worker and self.worker.is_alive()) and not (self.sync_worker and self.sync_worker.is_alive()):
            self.destroy()
            return
        self.after(100, self.poll)

    def close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("End this shift?", "Closing PattyOps stops camera tracking. Stop and save before closing?"):
                return
            self.stop()
        self.closing = True
        self.stop_sync.set()
        self.start_button.configure(state="disabled")
        self.status.set("Closing safely. Waiting for camera and cloud requests to finish...")


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    # Keep third-party print output usable in a windowed executable.
    runtime = DATA / "runtime.log"
    if runtime.exists() and runtime.stat().st_size > 5_000_000:
        runtime.replace(DATA / "runtime.previous.log")
    sys.stdout = sys.stderr = runtime.open("a", encoding="utf-8", buffering=1)
    logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(DATA / "activity.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")])
    # Prevent two desktop instances writing to the same kitchen database.
    lock = (DATA / "desktop.lock").open("a+b")
    if os.name == "nt":
        import msvcrt
        lock.seek(0)
        if not lock.read(1):
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo("PattyOps is already open", "Use the existing PattyOps Kitchen window.")
            root.destroy()
            return
    try:
        KitchenApp().mainloop()
    finally:
        lock.close()


if __name__ == "__main__":
    main()
