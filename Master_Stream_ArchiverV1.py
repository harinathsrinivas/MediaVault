import os
import re
import json
import time
import threading
import subprocess
import urllib.request
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# The MKVToolNix Engine Path
MKVMERGE_PATH = r"C:\Program Files\MKVToolNix\mkvmerge.exe"


class MasterArchiverApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Golden Standard: AI Stream Archiver & Analyzer")
        self.root.geometry("1300x1000")

        self.files = []
        self.results = {}
        self.raw_logs = {}
        self.log_widgets = {}

        # Process Management for the Kill Switch
        self.active_processes = []
        self.stop_requested = False

        self.build_ui()

    def build_ui(self):
        # --- PHASE 1: INGEST & OLLAMA CONFIG ---
        frame_top = tk.Frame(self.root, pady=5)
        frame_top.pack(fill=tk.X, padx=20)

        # Left side: File Selection & Execution
        frame_ingest = tk.LabelFrame(frame_top, text=" 1. Ingest & Control ", font=("Arial", 10, "bold"), padx=10,
                                     pady=10)
        frame_ingest.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        btn_frame = tk.Frame(frame_ingest)
        btn_frame.pack(pady=5)

        tk.Button(btn_frame, text="1. Select Streams", font=("Arial", 10, "bold"), command=self.select_files,
                  width=18).pack(side=tk.LEFT, padx=5)
        self.btn_start = tk.Button(btn_frame, text="2. Start Deep Test", bg="orange", fg="black",
                                   font=("Arial", 10, "bold"), command=self.start_analysis_thread, width=18)
        self.btn_start.pack(side=tk.LEFT, padx=5)

        # THE KILL SWITCH
        self.btn_stop = tk.Button(btn_frame, text="🛑 ABORT & CLEAN", bg="black", fg="white", font=("Arial", 10, "bold"),
                                  command=self.abort_all, state=tk.DISABLED, width=18)
        self.btn_stop.pack(side=tk.LEFT, padx=5)

        self.lbl_global_status = tk.Label(frame_ingest, text="Ready.", font=("Arial", 9, "bold"), fg="blue")
        self.lbl_global_status.pack(pady=5)

        # Right side: Ollama Config
        frame_ollama = tk.LabelFrame(frame_top, text=" 2. Local AI Engine (Optional) ", font=("Arial", 10, "bold"),
                                     padx=10, pady=10)
        frame_ollama.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.var_use_ollama = tk.BooleanVar(value=False)
        tk.Checkbutton(frame_ollama, text="Enable Ollama In-Depth Log Analysis", variable=self.var_use_ollama,
                       font=("Arial", 9, "bold"), fg="purple").grid(row=0, column=0, columnspan=2, sticky="w")

        tk.Label(frame_ollama, text="Ollama URL:").grid(row=1, column=0, sticky="w", pady=2)
        self.ent_ollama_url = tk.Entry(frame_ollama, width=25)
        self.ent_ollama_url.insert(0, "http://localhost:11434")
        self.ent_ollama_url.grid(row=1, column=1, pady=2, padx=5)

        tk.Label(frame_ollama, text="Model Name:").grid(row=2, column=0, sticky="w", pady=2)
        self.ent_ollama_model = tk.Entry(frame_ollama, width=25)
        self.ent_ollama_model.insert(0, "llama3")
        self.ent_ollama_model.grid(row=2, column=1, pady=2, padx=5)

        # --- DYNAMIC LOGGING SECTION ---
        self.frame_logs = tk.Frame(self.root)
        self.frame_logs.pack(fill=tk.BOTH, expand=True, padx=20, pady=5)

        # --- ANALYSIS REPORT ---
        frame_report = tk.LabelFrame(self.root, text=" 3. Verdicts & Technical Breakdown ", font=("Arial", 10, "bold"))
        frame_report.pack(fill=tk.X, padx=20, pady=5)
        self.txt_report = scrolledtext.ScrolledText(frame_report, height=12, wrap=tk.WORD, font=("Consolas", 10))
        self.txt_report.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # --- PHASE 3: MKVTOOLNIX REMUX ---
        frame_mux = tk.LabelFrame(self.root, text=" 4. MKVToolNix Remux Engine ", font=("Arial", 11, "bold"), pady=10,
                                  padx=10)
        frame_mux.pack(fill=tk.X, padx=20, pady=5)

        tk.Label(frame_mux, text="Winning Stream:", font=("Arial", 10, "bold")).grid(row=0, column=0, sticky="w",
                                                                                     pady=5)
        self.var_winner = tk.StringVar()
        self.drop_winner = ttk.Combobox(frame_mux, textvariable=self.var_winner, state="readonly", width=80)
        self.drop_winner.grid(row=0, column=1, padx=10, pady=5)

        tk.Label(frame_mux, text="Output Name:", font=("Arial", 10, "bold")).grid(row=1, column=0, sticky="w", pady=5)
        self.ent_output = tk.Entry(frame_mux, width=83)
        self.ent_output.grid(row=1, column=1, padx=10, pady=5)
        self.ent_output.insert(0, "e.g., 2026-07-03 - Australia vs Egypt - Round of 32 - [2160p UHD].mkv")

        tk.Button(frame_mux, text="Execute Golden Standard Remux", bg="red", fg="white", font=("Arial", 12, "bold"),
                  command=self.start_mux_thread).grid(row=2, column=0, columnspan=2, pady=10)

    def select_files(self):
        selected = filedialog.askopenfilenames(title="Select .ts files", filetypes=[("Transport Stream", "*.ts")])
        if len(selected) > 3:
            messagebox.showwarning("Limit Exceeded", "Please select max 3 files.")
            return
        if len(selected) < 2: return

        self.files = selected
        for widget in self.frame_logs.winfo_children(): widget.destroy()
        self.log_widgets = {}

        for i, file in enumerate(self.files):
            col_frame = tk.Frame(self.frame_logs, bd=2, relief=tk.GROOVE)
            col_frame.grid(row=0, column=i, sticky="nsew", padx=5)
            self.frame_logs.grid_columnconfigure(i, weight=1)

            lbl_head = tk.Label(col_frame, text=os.path.basename(file), font=("Arial", 9, "bold"), bg="lightgray")
            lbl_head.pack(fill=tk.X)

            txt_log = scrolledtext.ScrolledText(col_frame, font=("Consolas", 8), bg="black", fg="lightgreen")
            txt_log.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

            lbl_foot = tk.Label(col_frame, text="Awaiting Execution...", font=("Arial", 9, "bold"), fg="orange")
            lbl_foot.pack(pady=3)

            self.log_widgets[file] = (txt_log, lbl_foot)

        self.drop_winner['values'] = self.files
        if self.files: self.drop_winner.current(0)
        self.txt_report.delete(1.0, tk.END)

    def abort_all(self):
        """ The Aggressive Kill Switch & Cleaner """
        self.stop_requested = True
        self.lbl_global_status.config(text="ABORTING... Killing FFmpeg processes...", fg="red")

        # 1. Kill all active OS processes
        for p in self.active_processes:
            try:
                p.kill()  # Force kill at the OS level
            except Exception:
                pass
        self.active_processes.clear()

        # 2. Sweep the directories and delete garbage
        time.sleep(1)  # Give the OS a second to release file locks
        for file in self.files:
            base_dir = os.path.dirname(file)
            base_name = os.path.splitext(os.path.basename(file))[0]

            garbage = [
                os.path.join(base_dir, f"{base_name}_temp_analysis.mkv"),
                os.path.join(base_dir, f"{base_name}_analysis_log.txt"),
                os.path.join(base_dir, f"{base_name}_fallback_log.txt")
            ]
            for g in garbage:
                try:
                    if os.path.exists(g): os.remove(g)
                except Exception:
                    pass

        # 3. Reset UI
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.lbl_global_status.config(text="ABORTED. All processes killed and files swept.", fg="red")
        for file, widgets in self.log_widgets.items():
            widgets[1].config(text="ABORTED", fg="red")

    def start_analysis_thread(self):
        if not self.files: return
        self.stop_requested = False
        self.active_processes.clear()

        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.lbl_global_status.config(text="Running Two-Pass Physical Tests...", fg="blue")
        self.txt_report.delete(1.0, tk.END)
        threading.Thread(target=self.orchestrate_analysis, daemon=True).start()

    def orchestrate_analysis(self):
        self.results = {}
        self.raw_logs = {}
        threads = []
        for file in self.files:
            t = threading.Thread(target=self.analyze_file_realtime, args=(file,))
            t.start()
            threads.append(t)

        for t in threads: t.join()

        if self.stop_requested: return

        algorithmic_report = self.generate_verdict()
        self.root.after(0, self.txt_report.insert, tk.END, algorithmic_report)

        # OLLAMA AI INTEGRATION
        if self.var_use_ollama.get() and not self.stop_requested:
            self.root.after(0, self.lbl_global_status.config, {"text": "Calling Local Ollama AI...", "fg": "purple"})
            ai_report = self.ask_ollama()
            self.root.after(0, self.txt_report.insert, tk.END,
                            f"\n\n==================================================\n             OLLAMA AI VERDICT\n==================================================\n\n{ai_report}\n")

        self.root.after(0, self.cleanup_ui_post_run, algorithmic_report)

    def cleanup_ui_post_run(self, algorithmic_report):
        if not self.stop_requested:
            self.lbl_global_status.config(text="Testing & Analysis Complete.", fg="green")
            self.btn_start.config(state=tk.NORMAL)
            self.btn_stop.config(state=tk.DISABLED)

            winner_match = re.search(r"ALGORITHMIC WINNER: (.+)", algorithmic_report)
            if winner_match:
                winner_name = winner_match.group(1).strip()
                for idx, filepath in enumerate(self.drop_winner['values']):
                    if winner_name in filepath:
                        self.drop_winner.current(idx)
                        break

    def execute_ffmpeg_pass(self, cmd, log_file, txt_log):
        """ Helper function to run an FFmpeg pass, capture output, and allow stopping """
        output_capture = []
        try:
            process = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding='utf-8',
                                       errors='replace', bufsize=1)
            self.active_processes.append(process)

            with open(log_file, 'w', encoding='utf-8') as f_log:
                for line in process.stderr:
                    if self.stop_requested: break
                    output_capture.append(line)
                    f_log.write(line)
                    self.root.after(0, self.append_log, txt_log, line)

            process.wait()
            if process in self.active_processes: self.active_processes.remove(process)
            return "".join(output_capture), process.returncode
        except Exception as e:
            return str(e), -1

    def analyze_file_realtime(self, filepath):
        txt_log, lbl_foot = self.log_widgets[filepath]
        self.root.after(0, lambda: lbl_foot.config(text="PASS 1 (Strict)...", fg="blue"))

        base_dir = os.path.dirname(filepath)
        base_name = os.path.splitext(os.path.basename(filepath))[0]
        temp_mkv = os.path.join(base_dir, f"{base_name}_temp_analysis.mkv")
        log_file = os.path.join(base_dir, f"{base_name}_analysis_log.txt")
        fallback_log = os.path.join(base_dir, f"{base_name}_fallback_log.txt")

        # PASS 1: Strict Copy
        cmd1 = ["ffmpeg", "-y", "-i", filepath, "-c", "copy", "-map", "0", temp_mkv]
        output, code = self.execute_ffmpeg_pass(cmd1, log_file, txt_log)

        if self.stop_requested: return

        used_fallback = False

        # Check for crash in Pass 1
        if "conversion failed!" in output.lower() or "error muxing a packet" in output.lower():
            self.root.after(0, self.append_log, txt_log,
                            "\n\n[!] FATAL CRASH DETECTED in Pass 1.\n[!] INITIATING PASS 2: +discardcorrupt...\n\n")
            self.root.after(0, lambda: lbl_foot.config(text="PASS 2 (Fallback)...", fg="orange"))

            used_fallback = True

            # PASS 2: Fallback with discardcorrupt
            cmd2 = ["ffmpeg", "-y", "-fflags", "+discardcorrupt", "-i", filepath, "-c", "copy", "-map", "0", temp_mkv]
            output, code = self.execute_ffmpeg_pass(cmd2, fallback_log, txt_log)

            if self.stop_requested: return

        # Parse Final Data
        self.raw_logs[os.path.basename(filepath)] = output
        data = self.parse_metrics(output)
        data["used_fallback"] = used_fallback
        self.results[os.path.basename(filepath)] = data

        # Delete temp MKV to save space (keep the TXT logs)
        try:
            if os.path.exists(temp_mkv): os.remove(temp_mkv)
        except:
            pass

        if not self.stop_requested:
            self.root.after(0, lambda: lbl_foot.config(text=f"COMPLETED", fg="green"))

    def append_log(self, text_widget, line):
        text_widget.insert(tk.END, line)
        text_widget.see(tk.END)

    def parse_metrics(self, output):
        data = {"is_hdr": False, "fps": 0.0, "audio_codec": "Unknown", "audio_channels": "Unknown", "audio_bitrate": 0,
                "corrupt_packets": 0, "discontinuities": 0, "crashed": False}
        video_match = re.search(r"Stream #\d:\d.*?Video:\s+(.*?)\n", output)
        if video_match:
            v_str = video_match.group(1).lower()
            if "smpte2084" in v_str or "bt2020" in v_str or "main 10" in v_str: data["is_hdr"] = True
            fps_search = re.search(r"(\d+(?:\.\d+)?)\s+fps", v_str)
            if fps_search: data["fps"] = float(fps_search.group(1))

        audio_match = re.search(r"Stream #\d:\d.*?Audio:\s+(.*?)\n", output)
        if audio_match:
            a_str = audio_match.group(1).lower()
            if "ac3" in a_str or "ac-3" in a_str:
                data["audio_codec"] = "AC-3 (Dolby Digital)"
            elif "aac" in a_str:
                data["audio_codec"] = "AAC"
            if "5.1" in a_str:
                data["audio_channels"] = "5.1 Surround"
            elif "stereo" in a_str:
                data["audio_channels"] = "Stereo"
            bitrate_search = re.search(r"(\d+)\s+kb/s", a_str)
            if bitrate_search: data["audio_bitrate"] = int(bitrate_search.group(1))

        data["corrupt_packets"] = output.lower().count("corrupt input packet") + output.lower().count("dropping it")
        data["discontinuities"] = output.lower().count("timestamp discontinuity") + output.lower().count(
            "starting new cluster due to timestamp")
        if "conversion failed!" in output.lower() or "error muxing a packet" in output.lower(): data["crashed"] = True
        return data

    def generate_verdict(self):
        scores = {}
        report = "==================================================\n"
        report += "      ALGORITHMIC TECHNICAL BREAKDOWN             \n"
        report += "==================================================\n\n"

        for name, data in self.results.items():
            if "error" in data: continue
            report += f"[{name}]\n"
            report += f"  - Profile:   {'10-bit HDR (BT.2020/PQ)' if data['is_hdr'] else '8-bit SDR'}\n"
            report += f"  - Framerate: {data['fps']} fps\n"
            report += f"  - Audio:     {data['audio_codec']} {data['audio_channels']} ({data['audio_bitrate']} kb/s)\n"
            report += f"  - Packets:   {data['corrupt_packets']} dropped/corrupted\n"
            report += f"  - TimeSync:  {data['discontinuities']} discontinuities\n"

            status = "Completed Cleanly"
            if data.get('used_fallback'): status = "Survived via +discardcorrupt"
            if data['crashed']: status = "CRASHED (Unsalvageable)"
            report += f"  - Status:    {status}\n\n"

            scores[name] = 0
            if data['is_hdr']: scores[name] += 1000
            if "5.1" in data['audio_channels']: scores[name] += 500
            if "ac-3" in data['audio_codec'].lower(): scores[name] += 200
            scores[name] += int(data['fps'] * 10)
            scores[name] += data['audio_bitrate']

            if data['crashed']:
                scores[name] -= 10000  # Dead file
            elif data.get('used_fallback'):
                scores[name] -= 1500  # Heavy penalty for needing discarded frames, but still viable

            scores[name] -= (data['corrupt_packets'] * 2)
            # Cap the discontinuity penalty at 500 points so it doesn't outweigh 5.1 Audio
            scores[name] -= min(data['discontinuities'] * 0.05, 500)

        if not scores: return "Analysis failed on all files."

        winner = max(scores, key=scores.get)
        report += f"ALGORITHMIC WINNER: {winner}\n"
        return report

    def ask_ollama(self):
        url = self.ent_ollama_url.get().strip()
        model = self.ent_ollama_model.get().strip()

        system_prompt = "You are an expert Audio/Video archivist specializing in IPTV 4K HEVC streams. I will provide you with the FFmpeg output logs for multiple versions of the same sports broadcast. Your task is to analyze these logs and tell me which stream is the best one to archive for a high-end 4K projector home theater.\n\nPrioritize 10-bit HDR (BT.2020/SMPTE2084), 60 FPS, and AC-3 5.1 surround sound. Heavily penalize streams that crash with bitstream errors. If a stream required the +discardcorrupt fallback, acknowledge that it is structurally weaker but may still win if its base specs (like 5.1 audio) outweigh a clean stereo track.\n\nHere are the logs:\n\n"

        for name, log_content in self.raw_logs.items():
            lines = log_content.split('\n')
            if len(lines) > 200:
                truncated_log = "\n".join(lines[:100]) + "\n\n... [TRUNCATED MIDDLE SPAM] ...\n\n" + "\n".join(
                    lines[-100:])
            else:
                truncated_log = log_content
            system_prompt += f"--- LOG FOR STREAM: {name} ---\n{truncated_log}\n\n"

        system_prompt += "Based on the technical data and errors in these logs, explicitly declare a winner. Explain your reasoning regarding the video quality, audio quality, and stream integrity."

        data = {"model": model, "prompt": system_prompt, "stream": False}
        req = urllib.request.Request(f"{url}/api/generate", data=json.dumps(data).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=300) as response:
                return json.loads(response.read().decode('utf-8')).get("response", "Empty response.")
        except Exception as e:
            return f"Failed to connect to local Ollama instance.\nError Details: {str(e)}"

    # --- MUXING ENGINE ---
    def start_mux_thread(self):
        target_ts = self.var_winner.get()
        out_name = self.ent_output.get().strip()
        if not target_ts or not out_name or "e.g.," in out_name: return
        if not out_name.lower().endswith(".mkv"): out_name += ".mkv"

        out_path = os.path.join(os.path.dirname(target_ts), out_name)
        self.lbl_global_status.config(text=f"MKVToolNix is safely patching and remuxing {out_name}...", fg="blue")
        threading.Thread(target=self.run_mkvmerge, args=(target_ts, out_path), daemon=True).start()

    def run_mkvmerge(self, input_file, output_file):
        cmd = [MKVMERGE_PATH, "-o", output_file, input_file]
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            self.root.after(0, lambda: self.lbl_global_status.config(text="Golden Remux Complete!", fg="green"))
            self.root.after(0, lambda: messagebox.showinfo("Success",
                                                           f"Saved as:\n{os.path.basename(output_file)}\n\nMKVToolNix has successfully bypassed corrupted packets."))
        except subprocess.CalledProcessError as e:
            self.root.after(0, lambda: messagebox.showerror("MKVMerge Error", f"Remux failed:\n\n{e.stderr}"))


if __name__ == "__main__":
    root = tk.Tk()
    app = MasterArchiverApp(root)
    root.mainloop()