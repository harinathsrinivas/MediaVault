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
        self.root.title("Golden Standard: Master Agentic Archiver v6 (God Mode & Encoding Safe)")
        self.root.geometry("1450x1000")

        self.files = []
        self.file_mapping = {}
        self.results = {}
        self.raw_logs = {}
        self.log_widgets = {}

        # Process and Pipeline Management
        self.active_processes = {}
        self.stop_requested = False

        # Agent UI tracking states
        self.agent_statuses = {}
        self.agent_retry_buttons = {}

        self.build_ui()

    def build_ui(self):
        # --- PHASE 1: INGEST & CONTROL ---
        frame_top = tk.Frame(self.root, pady=5)
        frame_top.pack(fill=tk.X, padx=20)

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
        self.btn_stop = tk.Button(btn_frame, text="🛑 ABORT", bg="black", fg="white", font=("Arial", 10, "bold"),
                                  command=self.abort_all, state=tk.DISABLED, width=10)
        self.btn_stop.pack(side=tk.LEFT, padx=5)

        self.var_ignore_dur = tk.BooleanVar(value=False)
        tk.Checkbutton(frame_ingest,
                       text="Override Duration Warnings (Verified match is complete, ignore timeline variances)",
                       variable=self.var_ignore_dur, font=("Arial", 9, "bold"), fg="darkred").pack(pady=5)

        self.lbl_global_status = tk.Label(frame_ingest, text="Ready.", font=("Arial", 10, "bold"), fg="blue")
        self.lbl_global_status.pack(pady=5)

        # --- PHASE 2: AGENTIC OLLAMA CONFIG & DIAGNOSTICS ---
        frame_ollama = tk.LabelFrame(frame_top, text=" 2. Local AI Multi-Agent Engine & Diagnostics ",
                                     font=("Arial", 10, "bold"), padx=10, pady=5)
        frame_ollama.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        cfg_frame = tk.Frame(frame_ollama)
        cfg_frame.pack(fill=tk.X, pady=2)

        self.var_use_ollama = tk.BooleanVar(value=True)
        tk.Checkbutton(cfg_frame, text="Enable Agentic Multi-Model Analysis", variable=self.var_use_ollama,
                       font=("Arial", 9, "bold"), fg="purple").pack(side=tk.LEFT, padx=5)

        tk.Label(cfg_frame, text="API URL:").pack(side=tk.LEFT, padx=5)
        self.ent_ollama_url = tk.Entry(cfg_frame, width=20)
        self.ent_ollama_url.insert(0, "http://localhost:11434")
        self.ent_ollama_url.pack(side=tk.LEFT, padx=5)

        matrix_frame = tk.Frame(frame_ollama, pady=5)
        matrix_frame.pack(fill=tk.BOTH, expand=True)

        models_setup = [
            ("w1", "Worker 1 (Gen):", "qwen3.6:27b-q4_K_M"),
            ("w2", "Worker 2 (Strict):", "gemma4:26b-a4b-it-q4_K_M"),
            ("w3", "Worker 3 (Cons):", "gpt-oss:20b"),
            ("judge", "Lead Judge:", "huihui_ai/qwen3-coder-abliterated:30b-a3b-instruct-q4_K_M")
        ]

        self.model_entries = {}
        for idx, (key, label, default_val) in enumerate(models_setup):
            tk.Label(matrix_frame, text=label, font=("Arial", 9)).grid(row=idx, column=0, sticky="e", pady=2, padx=5)
            ent = tk.Entry(matrix_frame, width=30)
            ent.insert(0, default_val)
            ent.grid(row=idx, column=1, pady=2, padx=5)
            self.model_entries[key] = ent

            lbl_stat = tk.Label(matrix_frame, text="Idle", font=("Arial", 9, "bold"), fg="gray", width=25, anchor="w")
            lbl_stat.grid(row=idx, column=2, pady=2, padx=5)
            self.agent_statuses[key] = lbl_stat

            btn_retry = tk.Button(matrix_frame, text="🔄 Retry", state=tk.DISABLED, font=("Arial", 8),
                                  command=lambda k=key: self.retry_single_agent(k))
            btn_retry.grid(row=idx, column=3, pady=2, padx=5)
            self.agent_retry_buttons[key] = btn_retry

        # --- DYNAMIC PHYSICAL LOGGING SECTION ---
        self.frame_logs = tk.Frame(self.root)
        self.frame_logs.pack(fill=tk.BOTH, expand=True, padx=20, pady=5)

        # --- TABBED ANALYSIS REPORT ---
        frame_report = tk.LabelFrame(self.root, text=" 3. Verdicts & Technical Breakdown ", font=("Arial", 10, "bold"))
        frame_report.pack(fill=tk.X, padx=20, pady=5)

        self.report_notebook = ttk.Notebook(frame_report)
        self.report_notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # --- PHASE 4: MKVTOOLNIX REMUX ---
        frame_mux = tk.LabelFrame(self.root, text=" 4. MKVToolNix Remux Engine ", font=("Arial", 11, "bold"), pady=10,
                                  padx=10)
        frame_mux.pack(fill=tk.X, padx=20, pady=5)

        tk.Label(frame_mux, text="Winning Stream:").grid(row=0, column=0, sticky="w", pady=5)
        self.var_winner = tk.StringVar()
        self.drop_winner = ttk.Combobox(frame_mux, textvariable=self.var_winner, state="readonly", width=80)
        self.drop_winner.grid(row=0, column=1, padx=10, pady=5)

        tk.Label(frame_mux, text="Output Name:").grid(row=1, column=0, sticky="w", pady=5)
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
        self.file_mapping = {os.path.basename(f): f for f in self.files}

        for widget in self.frame_logs.winfo_children(): widget.destroy()
        self.log_widgets = {}

        for i, file in enumerate(self.files):
            col_frame = tk.Frame(self.frame_logs, bd=2, relief=tk.GROOVE)
            col_frame.grid(row=0, column=i, sticky="nsew", padx=5)
            self.frame_logs.grid_columnconfigure(i, weight=1)

            lbl_head = tk.Label(col_frame, text=os.path.basename(file), font=("Arial", 9, "bold"), bg="lightgray")
            lbl_head.pack(fill=tk.X)
            txt_log = scrolledtext.ScrolledText(col_frame, font=("Consolas", 8), bg="black", fg="lightgreen", height=12)
            txt_log.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
            lbl_foot = tk.Label(col_frame, text="Awaiting Execution...", font=("Arial", 9, "bold"), fg="orange")
            lbl_foot.pack(pady=3)
            self.log_widgets[file] = (txt_log, lbl_foot)

        self.drop_winner['values'] = list(self.file_mapping.keys())
        if self.files: self.drop_winner.current(0)

        for tab in self.report_notebook.tabs(): self.report_notebook.forget(tab)
        self.reset_agent_status_visuals()

    def reset_agent_status_visuals(self):
        for key in self.agent_statuses:
            self.agent_statuses[key].config(text="Idle", fg="gray")
            self.agent_retry_buttons[key].config(state=tk.DISABLED)

    def add_report_tab(self, title, content):
        for tab in self.report_notebook.tabs():
            if self.report_notebook.tab(tab, "text") == title:
                self.report_notebook.forget(tab)
                break
        f = tk.Frame(self.report_notebook)
        st = scrolledtext.ScrolledText(f, font=("Consolas", 10), wrap=tk.WORD, height=15)
        st.pack(fill=tk.BOTH, expand=True)
        st.insert(tk.END, content)
        self.report_notebook.add(f, text=title)
        self.report_notebook.select(f)

    def abort_all(self):
        self.stop_requested = True
        self.lbl_global_status.config(text="ABORTING... Killing physical tasks...", fg="red")

        for pid in list(self.active_processes.keys()):
            try:
                self.active_processes[pid].kill()
            except Exception:
                pass
        self.active_processes.clear()

        time.sleep(1)
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

        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.lbl_global_status.config(text="ABORTED. State cleaned up safely.", fg="red")
        for file, widgets in self.log_widgets.items():
            widgets[1].config(text="ABORTED", fg="red")

    def start_analysis_thread(self):
        if not self.files: return
        self.stop_requested = False
        self.active_processes.clear()
        self.reset_agent_status_visuals()

        for tab in self.report_notebook.tabs(): self.report_notebook.forget(tab)

        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.lbl_global_status.config(text="Running Physical Bitstream Ingestion...", fg="blue")
        threading.Thread(target=self.orchestrate_analysis, daemon=True).start()

    def orchestrate_analysis(self):
        self.results = {}
        self.raw_logs = {}

        # Explicit Scope Fix: Ensure report variable exists even if processing fails
        algorithmic_report = "Analysis failed or was aborted."
        has_duration_warning = False

        threads = []
        for file in self.files:
            t = threading.Thread(target=self.analyze_file_realtime, args=(file,))
            t.start()
            threads.append(t)

        for t in threads: t.join()
        if self.stop_requested: return

        algorithmic_report, has_duration_warning = self.generate_verdict()
        self.root.after(0, self.add_report_tab, "Algorithmic Verdict", algorithmic_report)

        if has_duration_warning and not self.var_ignore_dur.get():
            self.root.after(0, lambda: messagebox.showwarning("⚠️ DURATION DISCREPANCY DETECTED",
                                                              "One or more streams are significantly shorter than the primary record.\n\nThe algorithm penalized them. AI agents will now review the corrected Forensic Data metrics."))

        if self.var_use_ollama.get() and not self.stop_requested:
            self.run_full_agent_pipeline(algorithmic_report)
        else:
            self.root.after(0, self.cleanup_ui_post_run, algorithmic_report)

    # --- FORENSIC DATA EXTRACTION ENGINE ---
    def probe_file_stats(self, filepath):
        """Uses ffprobe to extract deep JSON metadata from a video file. Includes encoding immunity."""
        if not os.path.exists(filepath):
            return {"duration_sec": 0, "duration_str": "00h 00m 00s", "error": "File not found"}

        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", filepath]

        try:
            creationflags = 0x08000000 if os.name == 'nt' else 0
            # FIX: Explicitly decoding as utf-8 and ignoring corrupt byte errors from bad TS streams
            result = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='ignore', timeout=20,
                                    creationflags=creationflags)
            probe_data = json.loads(result.stdout)

            video_stream = next((s for s in probe_data.get('streams', []) if s['codec_type'] == 'video'), None)
            audio_streams = [s for s in probe_data.get('streams', []) if s['codec_type'] == 'audio']

            primary_audio = audio_streams[0] if audio_streams else None
            for a in audio_streams:
                if "ac3" in a.get('codec_name', '').lower() or a.get('channels', 0) > 2:
                    primary_audio = a
                    break

            format_data = probe_data.get('format', {})
            duration_sec = float(format_data.get('duration', 0))
            h, m, s = int(duration_sec // 3600), int((duration_sec % 3600) // 60), int(duration_sec % 60)

            return {
                "duration_str": f"{h:02d}h {m:02d}m {s:02d}s",
                "duration_sec": duration_sec,
                "size_mb": round(int(format_data.get('size', 0)) / (1024 * 1024), 2) if format_data.get('size') else 0,
                "bitrate_kbps": round(int(format_data.get('bit_rate', 0)) / 1000, 2) if format_data.get(
                    'bit_rate') else 0,
                "video_codec": video_stream.get('codec_name', 'Unknown') if video_stream else 'None',
                "resolution": f"{video_stream.get('width', 0)}x{video_stream.get('height', 0)}" if video_stream else 'Unknown',
                "audio_codec": primary_audio.get('codec_name', 'Unknown') if primary_audio else 'None',
                "audio_channels": primary_audio.get('channels', 0) if primary_audio else 0
            }
        except Exception as e:
            return {"duration_sec": 0, "duration_str": "00h 00m 00s", "error": str(e)}

    # --- ENCODING-SAFE FFMPEG ENGINE ---
    def execute_ffmpeg_pass(self, cmd, log_file, txt_log):
        """Anti-Choke Buffer System. Reads binary data to prevent crash on corrupted TS packets."""
        output_capture = []
        try:
            creationflags = 0x08000000 if os.name == 'nt' else 0
            # FIX: Remove text=True to handle raw bytes manually and bypass decoding crashes
            process = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, creationflags=creationflags)
            pid = process.pid
            self.active_processes[pid] = process

            self.root.after(0, self.lbl_global_status.config,
                            {"text": f"Running Process ID [PID: {pid}] via FFmpeg Engine...", "fg": "blue"})

            line_batch = []
            last_ui_update = time.time()

            with open(log_file, 'wb') as f_log:
                while True:
                    line_bytes = process.stderr.readline()
                    if not line_bytes and process.poll() is not None: break
                    if self.stop_requested: break

                    # Force UTF-8 decode, aggressively dropping corrupted hex bytes
                    line = line_bytes.decode('utf-8', errors='ignore')
                    output_capture.append(line)
                    f_log.write(line_bytes)

                    line_batch.append(line)
                    # Batch UI updates to max 5 times per second
                    if time.time() - last_ui_update > 0.2:
                        batch_str = "".join(line_batch)
                        self.root.after(0, self.append_log, txt_log, batch_str)
                        line_batch = []
                        last_ui_update = time.time()

            if line_batch:
                self.root.after(0, self.append_log, txt_log, "".join(line_batch))

            process.wait()
            if pid in self.active_processes: del self.active_processes[pid]
            return "".join(output_capture), process.returncode
        except Exception as e:
            return str(e), -1

    def append_log(self, text_widget, batch_str):
        """Limits the UI text box to 1500 lines so RAM never overflows"""
        text_widget.insert(tk.END, batch_str)
        try:
            lines = int(text_widget.index('end-1c').split('.')[0])
            if lines > 1500:
                text_widget.delete('1.0', f"end-{1500}l")
        except:
            pass
        text_widget.see(tk.END)

    def analyze_file_realtime(self, filepath):
        txt_log, lbl_foot = self.log_widgets[filepath]
        self.root.after(0, lambda: lbl_foot.config(text="PASS 1 (Strict)...", fg="blue"))

        base_dir = os.path.dirname(filepath)
        base_name = os.path.splitext(os.path.basename(filepath))[0]
        temp_mkv = os.path.join(base_dir, f"{base_name}_temp_analysis.mkv")
        log_file = os.path.join(base_dir, f"{base_name}_analysis_log.txt")
        fallback_log = os.path.join(base_dir, f"{base_name}_fallback_log.txt")

        # 1. Gather Deep Stats on the original lying TS file
        ts_stats = self.probe_file_stats(filepath)

        # 2. Execute FFmpeg conversion
        cmd1 = ["ffmpeg", "-y", "-i", filepath, "-c", "copy", "-map", "0", temp_mkv]
        output, code = self.execute_ffmpeg_pass(cmd1, log_file, txt_log)

        if self.stop_requested: return
        used_fallback = False

        if "conversion failed!" in output.lower() or "error muxing a packet" in output.lower():
            self.root.after(0, self.append_log, txt_log,
                            "\n\n[!] FATAL BITSTREAM DECODE CRASH in Pass 1.\n[!] INITIATING PASS 2: +discardcorrupt...\n\n")
            self.root.after(0, lambda: lbl_foot.config(text="PASS 2 (Fallback)...", fg="orange"))
            used_fallback = True
            cmd2 = ["ffmpeg", "-y", "-fflags", "+discardcorrupt", "-i", filepath, "-c", "copy", "-map", "0", temp_mkv]
            output, code = self.execute_ffmpeg_pass(cmd2, fallback_log, txt_log)
            if self.stop_requested: return

        # 3. PROBE THE MKV BEFORE DELETION (Gather Absolute Truth)
        mkv_stats = self.probe_file_stats(temp_mkv)

        self.raw_logs[os.path.basename(filepath)] = output

        # 4. Consolidate Data
        data = self.parse_metrics(output, mkv_stats.get('duration_sec', 0))
        data["ts_probe"] = ts_stats
        data["mkv_probe"] = mkv_stats
        data["used_fallback"] = used_fallback

        self.results[os.path.basename(filepath)] = data

        try:
            if os.path.exists(temp_mkv): os.remove(temp_mkv)
        except:
            pass

        if not self.stop_requested:
            self.root.after(0, lambda: lbl_foot.config(text=f"COMPLETED", fg="green"))

    def parse_metrics(self, output, mkv_dur_sec):
        data = {"is_hdr": False, "fps": 0.0, "audio_codec": "Unknown", "audio_channels": "Unknown", "audio_bitrate": 0,
                "corrupt_packets": 0, "discontinuities": 0, "crashed": False}

        # Grabbing Header Duration
        dur_match = re.search(r"Duration: (\d{2}):(\d{2}):(\d{2})", output)
        if dur_match:
            h, m, s = int(dur_match.group(1)), int(dur_match.group(2)), int(dur_match.group(3))
            header_sec = h * 3600 + m * 60 + s
            header_str = f"{h:02d}h {m:02d}m {s:02d}s"
        else:
            header_sec, header_str = 0, "Unknown"

        # Grabbing Physical FFmpeg Processed Time
        time_matches = re.findall(r"time=(\d{2}):(\d{2}):(\d{2})", output)
        if time_matches:
            h, m, s = int(time_matches[-1][0]), int(time_matches[-1][1]), int(time_matches[-1][2])
            phys_sec = h * 3600 + m * 60 + s
            phys_str = f"{h:02d}h {m:02d}m {s:02d}s"
        else:
            phys_sec, phys_str = 0, "Unknown"

        # MKV Truth Formatting
        if mkv_dur_sec > 0:
            h, m, s = int(mkv_dur_sec // 3600), int((mkv_dur_sec % 3600) // 60), int(mkv_dur_sec % 60)
            mkv_str = f"{h:02d}h {m:02d}m {s:02d}s"
        else:
            mkv_str = "Failed"

        # TRIPLE-CLOCK CONSOLIDATION: MKV TRUTH IS PARAMOUNT.
        if mkv_dur_sec > 0:
            data["duration_sec"] = mkv_dur_sec
            data["duration_str"] = mkv_str
            data["dur_note"] = f"(Truth: {mkv_str} [MKV] | Header: {header_str} | FFmpeg log: {phys_str})"
        else:
            data["duration_sec"] = phys_sec if phys_sec > 0 else header_sec
            data["duration_str"] = phys_str if phys_sec > 0 else header_str
            data["dur_note"] = f"(Truth: {data['duration_str']} [FFmpeg Fallback] | Header: {header_str})"

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
        has_duration_warning = False
        override_duration = self.var_ignore_dur.get()

        report = "==================================================\n"
        report += "      ALGORITHMIC TECHNICAL BREAKDOWN             \n"
        report += "==================================================\n\n"

        valid_durs = [d['duration_sec'] for d in self.results.values() if d['duration_sec'] > 0 and not d.get('error')]
        max_dur = max(valid_durs) if valid_durs else 0

        for name, data in self.results.items():
            if "error" in data: continue
            report += f"[{name}]\n"
            report += f"  - Final Calculated Duration: {data['duration_str']}\n"
            report += f"    {data['dur_note']}\n"

            is_short = False
            if max_dur > 0 and data['duration_sec'] < (max_dur - 300):
                is_short = True
                if override_duration:
                    report += f"    [INFO] Stream is shorter, but penalty bypassed by User.\n"
                else:
                    report += f"    [!!!] WARNING: STREAM IS STRUCTURALLY INCOMPLETE [!!!]\n"
                    has_duration_warning = True

            # Algorithmic Reporting of the Deep Probe Data
            mkv_stats = data.get("mkv_probe", {})
            v_codec = mkv_stats.get('video_codec', 'Unknown') if mkv_stats else "Unknown"
            v_res = mkv_stats.get('resolution', 'Unknown') if mkv_stats else "Unknown"

            report += f"  - Visuals:   {v_codec.upper()} {v_res} | {'10-bit HDR (BT.2020/PQ)' if data['is_hdr'] else '8-bit SDR'} | {data['fps']} fps\n"
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
                scores[name] -= 10000
            elif data.get('used_fallback'):
                scores[name] -= 1500

            if is_short and not override_duration:
                scores[name] -= 20000

            scores[name] -= (data['corrupt_packets'] * 2)
            scores[name] -= min(data['discontinuities'] * 0.05, 500)

        if not scores: return "Analysis failed on all files.", False

        winner = max(scores, key=scores.get)
        report += f"ALGORITHMIC WINNER: {winner}\n"
        return report, has_duration_warning

    def run_full_agent_pipeline(self, algorithmic_report):
        url = self.ent_ollama_url.get().strip()
        worker_responses = {}
        workers_list = [("w1", "Worker 1"), ("w2", "Worker 2"), ("w3", "Worker 3")]

        for key, name in workers_list:
            model = self.model_entries[key].get().strip()
            if not model or self.stop_requested: continue

            self.update_agent_status(key, f"⏳ Executing Engine...", "purple")
            prompt = self.build_worker_prompt()
            response = self.ask_ollama(url, model, prompt)

            if "Failed to connect" in response or "timed out" in response:
                self.update_agent_status(key, "❌ Connection Timeout", "red", enable_retry=True)
                worker_responses[f"{name}_{model}"] = f"[ERROR: Model failed to respond. {response}]"
            else:
                self.update_agent_status(key, "✅ Evaluation Complete", "green")
                worker_responses[f"{name}_{model}"] = response

            self.root.after(0, self.add_report_tab, f"Worker: {model}", worker_responses[f"{name}_{model}"])

        judge_key = "judge"
        judge_model = self.model_entries[judge_key].get().strip()
        if judge_model and not self.stop_requested:
            self.update_agent_status(judge_key, "⚖️ Deliberating Verdict...", "darkblue")
            judge_prompt = self.build_judge_prompt(worker_responses)

            judge_response = self.ask_ollama(url, judge_model, judge_prompt)
            if "Failed to connect" in judge_response or "timed out" in judge_response:
                self.update_agent_status(judge_key, "❌ Connection Timeout", "red", enable_retry=True)
            else:
                self.update_agent_status(judge_key, "🏅 Verdict Delivered", "green")

            self.root.after(0, self.add_report_tab, f"🏅 JUDGE: {judge_model}", judge_response)

        self.root.after(0, self.cleanup_ui_post_run, algorithmic_report)

    def ask_ollama(self, url, model, prompt):
        data = {"model": model, "prompt": prompt, "stream": False}
        req = urllib.request.Request(f"{url}/api/generate", data=json.dumps(data).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'})
        try:
            # Enforce 600s TIMEOUT - Crucial for local models processing full context
            with urllib.request.urlopen(req, timeout=600) as response:
                return json.loads(response.read().decode('utf-8')).get("response", "Empty response.")
        except Exception as e:
            return f"Failed to connect to local Ollama instance.\nError Details: timed out ({str(e)})"

    def build_worker_prompt(self):
        override_dur = self.var_ignore_dur.get()

        matrix_header = (
            "### 🚨 CRITICAL FORENSIC DATA (PYTHON DUAL-PROBE EXTRACTION)\n"
            "IPTV .ts files frequently have corrupted headers that lie about their duration. My Python layer has run deep `ffprobe` analysis on BOTH the original source file and the resulting MKV container to expose the truth. "
            "**You MUST use the 'MKV Truth Duration' as your absolute basis for completeness.**\n\n"
            "| Stream Name | Source (.ts) Claimed Duration | MKV Truth Duration (Actual) | Final Video Codec | Final Audio Codec | File Size (MB) |\n"
            "|---|---|---|---|---|---|\n"
        )

        for name, data in self.results.items():
            ts = data.get('ts_probe', {})
            mkv = data.get('mkv_probe', {})
            matrix_header += f"| {name} | {ts.get('duration_str', 'Error')} | **{mkv.get('duration_str', 'Error')}** | {mkv.get('video_codec', '')} {mkv.get('resolution', '')} | {mkv.get('audio_codec', '')} ({mkv.get('audio_channels', '')} channels) | {mkv.get('size_mb', 0)} MB |\n"

        matrix_header += "\n\n"

        system_prompt = (
                "You are an elite Audio/Video Archival Engineer building a master "
                "archive for a true 4K HDR Projector and a premium KEF 5.1 Surround Sound system.\n\n"
                "YOUR TASK:\n"
                "Review the provided Forensic Data Matrix and raw logs to determine the ultimate archive selection.\n\n"
                + matrix_header +
                "YOUR SCORING DIRECTIVES:\n"
                "1. VISUALS: Prioritize true 10-bit HDR (BT.2020 / SMPTE2084) and 59.94 FPS.\n"
                "2. AUDIO (CRITICAL): Prioritize discrete AC-3 Dolby Digital 5.1 over flat AAC Stereo. "
                "The client has dedicated center and surround channels that require this.\n"
                "3. INTEGRITY: Penalize streams that crashed or required '+discardcorrupt'. However, a slightly messy 5.1 HDR stream is better than a "
                "flawless stereo SDR stream.\n"
        )

        if override_dur:
            system_prompt += "4. DURATION: THE CLIENT HAS VERIFIED THE MATCH DATA IS COMPLETE. Any duration variance is just post-match ads. DO NOT penalize any stream for being shorter.\n\n"
        else:
            system_prompt += "4. DURATION (FATAL): If a stream is missing significant playtime compared to the longest capture in the MKV Truth Matrix, it is fundamentally incomplete. You MUST flag this explicitly and DO NOT SELECT IT under any circumstances.\n\n"

        system_prompt += "Here are the truncated FFmpeg logs for deeper context:\n\n"

        for name, log_content in self.raw_logs.items():
            system_prompt += f"--- LOG FOR STREAM: {name} ---\n"
            lines = log_content.split('\n')
            if len(lines) > 200:
                system_prompt += "\n".join(lines[:100]) + "\n\n... [TRUNCATED MIDDLE] ...\n\n" + "\n".join(
                    lines[-100:]) + "\n\n"
            else:
                system_prompt += log_content + "\n\n"

        system_prompt += "Weigh the technical indicators step-by-step, cross-check with the forensic matrix above, and explicitly declare a winner."
        return system_prompt

    def build_judge_prompt(self, worker_responses):
        system_prompt = (
            "You are the Lead Archival Synthesizer and Chief Judge. You oversee a team of AI "
            "video engineers tasked with selecting the ultimate 4K IPTV stream for a high-end "
            "home theater archive.\n\n"
            "YOUR TASK:\n"
            "1. Review the absolute ground truth summary table below.\n"
            "2. Review the individual Worker verdicts. Pinpoint anomalies where models misread raw log timestamps.\n"
            "3. Deliver the final, executive ruling on which file to archive based on the Ground Truth Matrix.\n\n"
            "### 🚨 CRITICAL FORENSIC DATA MATRIX:\n"
            "| Stream Name | MKV Truth Duration (Actual) | Final Video | Final Audio |\n"
            "|---|---|---|---|\n"
        )

        for name, data in self.results.items():
            mkv = data.get('mkv_probe', {})
            system_prompt += f"| {name} | **{mkv.get('duration_str', 'Error')}** | {mkv.get('video_codec', '')} {mkv.get('resolution', '')} | {mkv.get('audio_codec', '')} |\n"

        system_prompt += "\n--- THE AI WORKER VERDICTS ---\n"
        for worker, response in worker_responses.items():
            system_prompt += f"VERDICT FROM {worker}:\n{response}\n\n"

        system_prompt += "Synthesize these findings, correct any worker logical errors, and declare the absolute winner."
        return system_prompt

    def start_mux_thread(self):
        target_ts_basename = self.var_winner.get()
        target_ts = self.file_mapping.get(target_ts_basename)
        out_name = self.ent_output.get().strip()

        if not target_ts or not out_name or "e.g.," in out_name: return
        if not out_name.lower().endswith(".mkv"): out_name += ".mkv"

        out_path = os.path.join(os.path.dirname(target_ts), out_name)
        self.lbl_global_status.config(text=f"MKVToolNix is safely patching and remuxing {out_name}...", fg="blue")
        threading.Thread(target=self.run_mkvmerge, args=(target_ts, out_path), daemon=True).start()

    def run_mkvmerge(self, input_file, output_file):
        cmd = [MKVMERGE_PATH, "-o", output_file, input_file]
        try:
            # FIX: Encoding safety here as well just to be immune to any stray binary output
            subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='ignore', check=True)
            self.root.after(0, lambda: self.lbl_global_status.config(text="Golden Remux Complete!", fg="green"))
            self.root.after(0, lambda: messagebox.showinfo("Success",
                                                           f"Saved as:\n{os.path.basename(output_file)}\n\nMKVToolNix has successfully bypassed corrupted packets."))
        except subprocess.CalledProcessError as e:
            self.root.after(0, lambda: messagebox.showerror("MKVMerge Error", f"Remux failed:\n\n{e.stderr}"))

    def retry_single_agent(self, key):
        url = self.ent_ollama_url.get().strip()
        model = self.model_entries[key].get().strip()

        self.agent_retry_buttons[key].config(state=tk.DISABLED)
        self.update_agent_status(key, "🔄 Retrying Instance...", "orange")

        def retry_thread():
            if key == "judge":
                worker_responses = {}
                for key_w, name_w in [("w1", "Worker 1"), ("w2", "Worker 2"), ("w3", "Worker 3")]:
                    m_name = self.model_entries[key_w].get().strip()
                    worker_responses[f"{name_w}_{m_name}"] = f"Refer to tab for model {m_name}"
                prompt = self.build_judge_prompt(worker_responses)
                tab_title = f"🏅 JUDGE: {model}"
            else:
                prompt = self.build_worker_prompt()
                tab_title = f"Worker: {model}"

            response = self.ask_ollama(url, model, prompt)

            if "Failed to connect" in response or "timed out" in response:
                self.update_agent_status(key, "❌ Retry Failed (Timeout)", "red", enable_retry=True)
            else:
                self.update_agent_status(key, "✅ Evaluation Complete (Restored)", "green")

            self.root.after(0, self.add_report_tab, tab_title, response)

        threading.Thread(target=retry_thread, daemon=True).start()

    def update_agent_status(self, key, text, color, enable_retry=False):
        def update():
            self.agent_statuses[key].config(text=text, fg=color)
            if enable_retry:
                self.agent_retry_buttons[key].config(state=tk.NORMAL)
            else:
                self.agent_retry_buttons[key].config(state=tk.DISABLED)

        self.root.after(0, update)

    def cleanup_ui_post_run(self, algorithmic_report):
        self.lbl_global_status.config(text="Testing & AI Agent Pipeline Complete.", fg="green")
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)

        winner_match = re.search(r"ALGORITHMIC WINNER: (.+)", algorithmic_report)
        if winner_match:
            winner_name = winner_match.group(1).strip()
            for idx, filename in enumerate(self.drop_winner['values']):
                if winner_name == filename:
                    self.drop_winner.current(idx)
                    break


if __name__ == "__main__":
    root = tk.Tk()
    app = MasterArchiverApp(root)
    root.mainloop()