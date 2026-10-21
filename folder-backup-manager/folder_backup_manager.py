import os
import shutil
import hashlib
import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from datetime import datetime
import threading
import time
import schedule

# Configuration file name
CONFIG_FILE = "backup_config.json"

class ProBackupApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Pro Incremental Backup (Long Path Fix)")
        self.root.geometry("600x670")

        self.sources = []
        self.dest_path = tk.StringVar()
        self.schedule_time = tk.StringVar(value="02:00") 
        self.skipped_forever = []

        self.setup_ui()
        self.load_settings()
        self.start_scheduler_thread()
        
        # Save settings when the window is closed
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def setup_ui(self):
        tk.Label(self.root, text="Incremental Backup System", font=("Arial", 16, "bold")).pack(pady=10)

        # 1. Source Folders Section
        frame_src = tk.LabelFrame(self.root, text="Source Folders", padx=10, pady=10)
        frame_src.pack(fill="both", padx=20, pady=5, expand=True)

        self.src_listbox = tk.Listbox(frame_src, height=6)
        self.src_listbox.pack(side="left", fill="both", expand=True)

        btn_frame = tk.Frame(frame_src)
        btn_frame.pack(side="right", padx=5)
        tk.Button(btn_frame, text="Add Folder", command=self.add_source, width=15).pack(pady=2)
        tk.Button(btn_frame, text="Remove Selected", command=self.remove_source, width=15).pack(pady=2)
        tk.Button(btn_frame, text="Clear All", command=self.clear_sources, width=15).pack(pady=2)

        # 2. Destination Section
        frame_dst = tk.LabelFrame(self.root, text="Destination", padx=10, pady=10)
        frame_dst.pack(fill="x", padx=20, pady=5)
        tk.Entry(frame_dst, textvariable=self.dest_path, width=50).pack(side="left", padx=5)
        tk.Button(frame_dst, text="Browse", command=self.browse_dest).pack(side="left")

        # 3. Scheduler Section
        frame_sched = tk.LabelFrame(self.root, text="Automation Settings", padx=10, pady=10)
        frame_sched.pack(fill="x", padx=20, pady=5)
        tk.Label(frame_sched, text="Run Daily At (HH:MM):").pack(side="left")
        tk.Entry(frame_sched, textvariable=self.schedule_time, width=10).pack(side="left", padx=5)
        tk.Button(frame_sched, text="Set Schedule", command=self.update_schedule).pack(side="left", padx=5)

        # 4. Status and Progress
        self.status_label = tk.Label(self.root, text="Status: Ready", fg="blue", font=("Arial", 10, "italic"))
        self.status_label.pack(pady=10)

        self.progress = ttk.Progressbar(self.root, length=500, mode="determinate")
        self.progress.pack(pady=5)

        self.run_btn = tk.Button(self.root, text="RUN BACKUP NOW", bg="#2e7d32", fg="white", 
                                font=("Arial", 12, "bold"), height=2, command=self.manual_run)
        self.run_btn.pack(pady=20)

    # --- Persistence ---

    def load_settings(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
                    self.sources = config.get("sources", [])
                    for s in self.sources:
                        self.src_listbox.insert(tk.END, s)
                    self.dest_path.set(config.get("destination", ""))
                    self.schedule_time.set(config.get("schedule", "02:00"))
                    self.skipped_forever = config.get("skipped_forever", [])
                    if self.dest_path.get():
                        self.refresh_last_backup_time(self.dest_path.get())
            except: pass

    def save_settings(self):
        config = {
            "sources": self.sources, "destination": self.dest_path.get(),
            "schedule": self.schedule_time.get(), "skipped_forever": self.skipped_forever
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)

    def on_closing(self):
        self.save_settings()
        self.root.destroy()

    # --- Folder Management ---

    def add_source(self):
        folder = filedialog.askdirectory()
        if folder and folder not in self.sources:
            self.sources.append(folder)
            self.src_listbox.insert(tk.END, folder)
            self.save_settings()

    def remove_source(self):
        try:
            idx = self.src_listbox.curselection()[0]
            self.sources.pop(idx)
            self.src_listbox.delete(idx)
            self.save_settings()
        except: pass

    def clear_sources(self):
        self.sources = []
        self.src_listbox.delete(0, tk.END)
        self.save_settings()

    def browse_dest(self):
        folder = filedialog.askdirectory()
        if folder:
            self.dest_path.set(folder)
            self.refresh_last_backup_time(folder)
            self.save_settings()

    # --- Logic Methods with Long Path Prefix ---

    def to_long_path(self, path):
        """Adds UNC prefix to allow paths longer than 260 characters on Windows."""
        abs_path = os.path.abspath(path)
        if not abs_path.startswith("\\\\?\\"):
            return "\\\\?\\" + abs_path
        return abs_path

    def get_file_hash(self, filepath):
        long_filepath = self.to_long_path(filepath)
        hasher = hashlib.sha256()
        try:
            with open(long_filepath, 'rb') as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except: return None

    def refresh_last_backup_time(self, dest):
        manifest_p = Path(dest) / "backup_manifest.json"
        if manifest_p.exists():
            with open(self.to_long_path(str(manifest_p)), 'r') as f:
                data = json.load(f)
                t = data.get("last_backup_time", "Never")
                self.status_label.config(text=f"Last Backup: {t}")

    # --- Scheduling ---

    def start_scheduler_thread(self):
        def run_loop():
            while True:
                schedule.run_pending()
                time.sleep(10)
        self.update_schedule(silent=True)
        threading.Thread(target=run_loop, daemon=True).start()

    def update_schedule(self, silent=False):
        schedule.clear()
        target_time = self.schedule_time.get()
        try:
            datetime.strptime(target_time, "%H:%M")
            schedule.every().day.at(target_time).do(self.run_backup)
            if not silent: messagebox.showinfo("Scheduled", f"Daily backup set for {target_time}")
            self.save_settings()
        except: messagebox.showerror("Error", "Invalid time format (HH:MM)")

    def manual_run(self):
        threading.Thread(target=self.run_backup, daemon=True).start()

    # --- THE CORE BACKUP ENGINE ---

    def run_backup(self):
        dst = self.dest_path.get()
        if not dst or not os.path.exists(dst):
            messagebox.showerror("Error", "Backup folder not present at its original location!")
            return
        if not self.sources: return

        for s in self.sources:
            if not os.path.exists(s):
                messagebox.showerror("Error", f"Source folder not present:\n{s}")
                return

        self.run_btn.config(state="disabled")
        dest_dir = Path(dst)
        manifest_path = dest_dir / "backup_manifest.json"
        
        manifest = {"files": {}, "last_backup_time": "Never"}
        long_manifest_path = self.to_long_path(str(manifest_path))

        if os.path.exists(long_manifest_path):
            try:
                with open(long_manifest_path, 'r') as f:
                    manifest = json.load(f)
            except: pass

        all_files = []
        for src in self.sources:
            s_path = Path(src)
            for root, _, files in os.walk(s_path):
                for f in files:
                    full_p = Path(root) / f
                    if str(full_p) not in self.skipped_forever:
                        all_files.append((s_path, full_p))

        self.progress["maximum"] = len(all_files) if all_files else 1
        self.progress["value"] = 0
        copied = 0

        for src_root, file_path in all_files:
            rel_path = file_path.relative_to(src_root.parent)
            
            # Prepare Long Paths
            source_p_str = self.to_long_path(str(file_path))
            target_p_str = self.to_long_path(str(dest_dir / rel_path))
            
            f_hash = self.get_file_hash(str(file_path))
            if not f_hash: continue

            if str(rel_path) not in manifest["files"] or manifest["files"][str(rel_path)] != f_hash:
                try:
                    os.makedirs(os.path.dirname(target_p_str), exist_ok=True)
                    shutil.copy2(source_p_str, target_p_str)
                    manifest["files"][str(rel_path)] = f_hash
                    copied += 1
                except (PermissionError, OSError) as e:
                    res = messagebox.askyesno("File Error", 
                        f"Error copying: {file_path.name}\n\nDo you want to skip this file in all future backups?")
                    if res:
                        self.skipped_forever.append(str(file_path))
                        self.save_settings()
                    continue

            self.progress["value"] += 1
            self.status_label.config(text=f"Processing: {file_path.name}")
            self.root.update_idletasks()

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        manifest["last_backup_time"] = now
        with open(long_manifest_path, 'w') as f:
            json.dump(manifest, f, indent=4)

        self.status_label.config(text=f"Backup Finished: {now}", fg="green")
        self.run_btn.config(state="normal")
        messagebox.showinfo("Complete", f"Backup finished!\nFiles updated: {copied}")

if __name__ == "__main__":
    root = tk.Tk()
    app = ProBackupApp(root)
    root.mainloop()