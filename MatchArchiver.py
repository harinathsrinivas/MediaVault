import os
import re
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import timedelta

# The Golden Standard Broadcast Labels
STANDARD_CHAPTERS = [
    "Pre-Match Presentation", "First Half", "First Half (Hydration Break)",
    "First Half (Play Resumed)", "Half-Time Highlights", "Half-Time Analysis",
    "Second Half Warm-Up", "Second Half", "Second Half (Hydration Break)",
    "Second Half (Play Resumed)", "Post-Match Reaction",
    "Extra Time (First Half)", "Extra Time (Half-Time)", "Extra Time (Second Half)",
    "Penalty Shootout", "Match Highlights"
]

TARGET_FILES = ["1 - First Half", "2 - Second Half", "3 - Extras"]
MKVMERGE_PATH = r"C:\Program Files\MKVToolNix\mkvmerge.exe"


def parse_time(time_str):
    time_str = time_str.replace('.', ':')
    parts = time_str.split(':')
    return timedelta(
        hours=int(parts[0]), minutes=int(parts[1]),
        seconds=int(parts[2]), milliseconds=int(parts[3][:3]) if len(parts) > 3 else 0
    )


def format_mkv_time(td):
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    milliseconds = int(td.microseconds / 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


class MatchArchiverApp:
    def __init__(self, root):
        self.root = root
        self.root.withdraw()

        self.target_directory = filedialog.askdirectory(title="Select Folder containing LosslessCut .mkv segments")

        if not self.target_directory:
            messagebox.showinfo("Cancelled", "No folder selected. Exiting Archiver.")
            self.root.destroy()
            return

        os.chdir(self.target_directory)

        self.root.deiconify()
        self.root.title(f"Golden Standard Match Archiver - Working in: {os.path.basename(self.target_directory)}")
        self.root.geometry("1400x700")

        self.segments = self.scan_directory()
        self.rows = []
        self.build_ui()

    def scan_directory(self):
        pattern = re.compile(
            r'(.*)-\s*(\d{1,2}[.:]\d{2}[.:]\d{2}[.:]\d{1,3})\s*-\s*(\d{1,2}[.:]\d{2}[.:]\d{2}[.:]\d{1,3})\s*-\s*seg\d+\.mkv',
            re.IGNORECASE)
        files = sorted([f for f in os.listdir('.') if f.endswith('.mkv') and 'seg' in f.lower()])
        parsed = []
        for f in files:
            match = pattern.search(f)
            if match:
                parsed.append({
                    "filename": f,
                    "base_name": match.group(1).strip(),
                    "start": parse_time(match.group(2)),
                    "end": parse_time(match.group(3))
                })
        return parsed

    def build_ui(self):
        if not self.segments:
            tk.Label(self.root, text="No LosslessCut segments found in this directory.", font=("Arial", 14)).pack(
                pady=50)
            return

        cols = ("Segment File", "Target Output File", "Chapter Label")
        self.tree_frame = tk.Frame(self.root)
        self.tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        for i, col in enumerate(cols):
            tk.Label(self.tree_frame, text=col, font=("Arial", 10, "bold")).grid(row=0, column=i, sticky="w", padx=5,
                                                                                 pady=5)

        for i, seg in enumerate(self.segments):
            tk.Label(self.tree_frame, text=seg["filename"]).grid(row=i + 1, column=0, sticky="w", padx=5)

            target_var = tk.StringVar(value=TARGET_FILES[0])
            if i >= 11:
                target_var.set(TARGET_FILES[2])
            elif i >= 6:
                target_var.set(TARGET_FILES[1])

            target_drop = ttk.Combobox(self.tree_frame, textvariable=target_var, values=TARGET_FILES, state="readonly",
                                       width=15)
            target_drop.grid(row=i + 1, column=1, padx=5)

            chapter_var = tk.StringVar(
                value=STANDARD_CHAPTERS[i] if i < len(STANDARD_CHAPTERS) else STANDARD_CHAPTERS[-1])
            chapter_drop = ttk.Combobox(self.tree_frame, textvariable=chapter_var, values=STANDARD_CHAPTERS, width=30)
            chapter_drop.grid(row=i + 1, column=2, padx=5)

            self.rows.append({"seg": seg, "target": target_var, "chapter": chapter_var})

        btn = tk.Button(self.root, text="Execute Merge Natively", bg="red", fg="white", font=("Arial", 12, "bold"),
                        command=self.generate_files)
        btn.pack(pady=20)

    def generate_files(self):
        targets = {tf: [] for tf in TARGET_FILES}
        for row in self.rows:
            targets[row["target"].get()].append({
                "seg": row["seg"],
                "chapter": row["chapter"].get()
            })

        base_match_name = self.segments[0]["base_name"]
        for target_name, items in targets.items():
            if not items: continue

            part_suffix = target_name.split('- ')[1]

            # Split the base name into chunks based on the ' - ' separator
            name_parts = base_match_name.split(' - ')

            # If the filename matches standard formatting (Date - Teams - Stage...),
            # inject the suffix right after the Teams (index 2).
            if len(name_parts) >= 2:
                name_parts.insert(2, part_suffix)
                out_mkv = " - ".join(name_parts) + ".mkv"
            else:
                # Fallback just in case the filename format is different
                out_mkv = f"{base_match_name} - {part_suffix}.mkv"

            txt_name = f"chapters_{part_suffix.replace(' ', '')}.txt"

            abs_out_mkv = os.path.join(self.target_directory, out_mkv)
            abs_txt_name = os.path.join(self.target_directory, txt_name)

            txt_output = []
            current_time = timedelta(0)
            for idx, item in enumerate(items):
                chap_num = f"{idx + 1:02d}"
                duration = item["seg"]["end"] - item["seg"]["start"]
                txt_output.append(f"CHAPTER{chap_num}={format_mkv_time(current_time)}")
                txt_output.append(f"CHAPTER{chap_num}NAME={item['chapter']}")
                current_time += duration

            with open(abs_txt_name, 'w', encoding='utf-8') as f:
                f.write('\n'.join(txt_output))

            # NATIVE OS EXECUTION COMMAND (No Batch Files, No CMD bugs)
            cmd = [MKVMERGE_PATH, "--output", abs_out_mkv, "--chapters", abs_txt_name]
            for idx, item in enumerate(items):
                if idx > 0:
                    cmd.append("+")  # Standard MKVToolNix append flag
                cmd.append(os.path.join(self.target_directory, item["seg"]["filename"]))

            try:
                # Run the command directly.
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            except subprocess.CalledProcessError as e:
                # Catch the raw engine error if it fails
                error_msg = e.stdout + "\n" + e.stderr
                messagebox.showerror("MKVMerge Error", f"Failed on {target_name}:\n\n{error_msg}")
                return

        messagebox.showinfo("Success", "All files successfully multiplexed and chaptered!")
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = MatchArchiverApp(root)
    root.mainloop()