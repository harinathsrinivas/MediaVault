import os
import re
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
    return f"{total_seconds // 3600:02d}:{(total_seconds % 3600) // 60:02d}:{total_seconds % 60:02d}.{int(td.microseconds * 1000):09d}"


class MatchArchiverApp:
    def __init__(self, root):
        self.root = root

        # Hide the main window temporarily while we ask for the folder
        self.root.withdraw()

        # 1. Ask for the directory
        self.target_directory = filedialog.askdirectory(title="Select Folder containing LosslessCut .mkv segments")

        # If user clicks cancel, exit gracefully
        if not self.target_directory:
            messagebox.showinfo("Cancelled", "No folder selected. Exiting Archiver.")
            self.root.destroy()
            return

        # 2. Change the Python working directory to the chosen folder
        os.chdir(self.target_directory)

        # Bring the main window back and set it up
        self.root.deiconify()
        self.root.title(f"Golden Standard Match Archiver - Working in: {os.path.basename(self.target_directory)}")
        self.root.geometry("1000x600")

        self.segments = self.scan_directory()
        self.rows = []
        self.build_ui()

    def scan_directory(self):
        pattern = re.compile(
            r'(.*)-\s*(\d{1,2}[.:]\d{2}[.:]\d{2}[.:]\d{1,3})\s*-\s*(\d{1,2}[.:]\d{2}[.:]\d{2}[.:]\d{1,3})\s*-\s*seg\d+\.mkv',
            re.IGNORECASE)
        # Scan the current working directory (which we just set via os.chdir)
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

        # Headers
        for i, col in enumerate(cols):
            tk.Label(self.tree_frame, text=col, font=("Arial", 10, "bold")).grid(row=0, column=i, sticky="w", padx=5,
                                                                                 pady=5)
        # Data Rows
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

        btn = tk.Button(self.root, text="Generate MKVToolNix Build Files", bg="green", fg="white",
                        font=("Arial", 12, "bold"), command=self.generate_files)
        btn.pack(pady=20)

    def generate_files(self):
        targets = {tf: [] for tf in TARGET_FILES}
        for row in self.rows:
            targets[row["target"].get()].append({
                "seg": row["seg"],
                "chapter": row["chapter"].get()
            })

        base_match_name = self.segments[0]["base_name"]
        bat_commands = []

        for target_name, items in targets.items():
            if not items: continue

            # File Naming formatting
            part_suffix = target_name.split('- ')[1]
            out_mkv = f"{base_match_name} - {part_suffix}.mkv"
            xml_name = f"chapters_{part_suffix.replace(' ', '')}.xml"

            # 1. Generate XML
            xml_output = ['<?xml version="1.0" encoding="UTF-8"?>', '<Chapters>', '  <EditionEntry>',
                          '    <EditionFlagHidden>0</EditionFlagHidden>',
                          '    <EditionFlagDefault>1</EditionFlagDefault>']
            current_time = timedelta(0)

            for item in items:
                duration = item["seg"]["end"] - item["seg"]["start"]
                xml_output.extend([
                    '    <ChapterAtom>', f'      <ChapterTimeStart>{format_mkv_time(current_time)}</ChapterTimeStart>',
                    '      <ChapterDisplay>', f'        <ChapterString>{item["chapter"]}</ChapterString>',
                    '        <ChapterLanguage>eng</ChapterLanguage>', '      </ChapterDisplay>', '    </ChapterAtom>'
                ])
                current_time += duration

            xml_output.extend(['  </EditionEntry>', '</Chapters>'])
            with open(xml_name, 'w', encoding='utf-8') as f:
                f.write('\n'.join(xml_output))

            # 2. Build MKVMerge Command
            file_list = []
            for idx, item in enumerate(items):
                prefix = "+" if idx > 0 else ""
                file_list.append(f'"{item["seg"]["filename"]}"')

            bat_commands.append(
                f'"{MKVMERGE_PATH}" -o "{out_mkv}" --chapters "{xml_name}" {" ".join(file_list).replace(" \"", " +\"")}')

        # Write Batch File
        with open("Build_Master_Files.bat", "w", encoding='utf-8') as b:
            b.write("@echo off\n")
            b.write("\n".join(bat_commands))
            b.write("\necho.\necho ALL FILES SUCCESSFULLY MERGED!\npause")

        messagebox.showinfo("Success",
                            "XML and Batch files generated!\n\nCheck the selected folder, then double-click 'Build_Master_Files.bat' to start the MKVToolNix merge process.")
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = MatchArchiverApp(root)
    root.mainloop()