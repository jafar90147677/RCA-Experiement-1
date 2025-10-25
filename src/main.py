"""Main GUI application for AI Log Helper.

This module provides a GUI interface for log analysis using local AI models.
"""
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

from analyzer import analyze_files

DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL_NAME = os.environ.get("OLLAMA_MODEL", "llama3")

class App(tk.Tk):
    """Main application class for AI Log Helper GUI."""
    
    def __init__(self):
        """Initialize the application."""
        super().__init__()
        self.title("AI Log Helper (Local Llama3)")
        self.geometry("900x600")

        self.project_path = tk.StringVar()
        self.logs_path = tk.StringVar()
        self.log_files = []  # Store selected log files
        self.last_analysis = ""  # Store the last analysis results

        # Top instructions
        tk.Label(self, text="Generate root cause of the log").pack(anchor="w", padx=10, pady=(10,0))
        tk.Label(self, text="Select your project folder and log files, then click Analyze.", fg="#555").pack(anchor="w", padx=10, pady=(0,10))

        # Row with buttons
        row = tk.Frame(self)
        row.pack(fill="x", padx=10, pady=5)

        tk.Button(row, text="Select Project Folder", command=self.pick_project).pack(side="left", padx=5)
        tk.Entry(row, textvariable=self.project_path, width=60).pack(side="left", padx=5, fill="x", expand=True)

        row2 = tk.Frame(self)
        row2.pack(fill="x", padx=10, pady=5)

        tk.Button(row2, text="Select Log Files", command=self.pick_log_files).pack(side="left", padx=5)
        tk.Entry(row2, textvariable=self.logs_path, width=60).pack(side="left", padx=5, fill="x", expand=True)
        
        # Show selected log files
        self.log_files_label = tk.Label(self, text="No log files selected", fg="#666", wraplength=800)
        self.log_files_label.pack(anchor="w", padx=10, pady=(0,5))

        run_row = tk.Frame(self)
        run_row.pack(fill="x", padx=10, pady=10)
        tk.Button(run_row, text="Analyze", command=self.on_analyze, bg="#4CAF50", fg="white").pack(side="left", padx=(0,10))
        tk.Button(run_row, text="Generate Prompt", command=self.generate_prompt, bg="#FF9800", fg="white").pack(side="left")

        # Output box
        self.output = scrolledtext.ScrolledText(self, wrap="word", height=20)
        self.output.pack(fill="both", expand=True, padx=10, pady=10)

        self.status = tk.StringVar(value="Idle")
        tk.Label(self, textvariable=self.status, fg="#777").pack(anchor="w", padx=10, pady=(0,10))

    def pick_project(self):
        """Pick project folder."""
        path = filedialog.askdirectory(title="Pick project folder")
        if path:
            self.project_path.set(path)

    def pick_log_files(self):
        """Pick log files for analysis."""
        files = filedialog.askopenfilenames(
            title="Select Log Files",
            filetypes=[
                ("Log and Text files", "*.log;*.txt"),
                ("Log files", "*.log"),
                ("Text files", "*.txt"),
                ("All files", "*.*")
            ]
        )
        if files:
            self.log_files = list(files)
            # Update the display
            if len(files) == 1:
                self.logs_path.set(files[0])
                self.log_files_label.config(text=f"Selected: {os.path.basename(files[0])}")
            else:
                self.logs_path.set(f"{len(files)} files selected")
                file_names = [os.path.basename(f) for f in files[:3]]
                if len(files) > 3:
                    file_names.append(f"... and {len(files) - 3} more")
                self.log_files_label.config(text=f"Selected: {', '.join(file_names)}")
        else:
            self.log_files = []
            self.logs_path.set("")
            self.log_files_label.config(text="No log files selected")

    def on_analyze(self):
        """Analyze selected log files."""
        proj = self.project_path.get().strip()
        
        if not self.log_files:
            messagebox.showwarning("Missing logs", "Please select log files first.")
            return

        self.status.set("Analyzing...")
        self.output.delete("1.0", "end")
        self.output.insert("end", "Working... this can take a few seconds.\n")

        def task():
            try:
                result_text = analyze_files(
                    project_folder=proj if proj else None,
                    log_files=self.log_files,
                    ollama_url=DEFAULT_OLLAMA_URL,
                    model=MODEL_NAME,
                )
                self.last_analysis = result_text  # Store the analysis results
                self.output.delete("1.0", "end")
                self.output.insert("end", result_text)
                self.status.set("Done")
            except OSError as e:
                self.output.delete("1.0", "end")
                error_msg = f"Analysis failed: {e}\n\n"
                error_msg += "Troubleshooting steps:\n"
                error_msg += "1. Make sure Ollama server is running (run start_server.bat)\n"
                error_msg += "2. Check if the model is available (ollama pull llama3)\n"
                error_msg += "3. Verify the server URL and port\n"
                self.output.insert("end", error_msg)
                self.status.set("Error")

        threading.Thread(target=task, daemon=True).start()

    def generate_prompt(self):
        """Generate prompt for Cursor IDE."""
        if not self.last_analysis:
            messagebox.showwarning("No Analysis", "Please run analysis first before generating prompt.")
            return
        
        # Extract root cause and rectification steps from the analysis
        lines = self.last_analysis.split('\n')
        root_cause = ""
        rectification_steps = []
        
        in_root_cause = False
        in_rectification = False
        
        for line in lines:
            if "🔍 ROOT CAUSE ANALYSIS:" in line:
                in_root_cause = True
                in_rectification = False
                continue
            elif "🛠️ RECTIFICATION STEPS:" in line:
                in_root_cause = False
                in_rectification = True
                continue
            elif line.startswith("🚨") or line.startswith("🔍") or line.startswith("🛠️"):
                in_root_cause = False
                in_rectification = False
                continue
            
            if in_root_cause and line.strip():
                root_cause += line + "\n"
            elif in_rectification and line.strip() and (line.startswith("1)") or line.startswith("2)") or line.startswith("3)") or line.startswith("4)") or line.startswith("5)")):
                rectification_steps.append(line.strip())
        
        # Generate the golden prompt for Cursor
        golden_prompt = f"""You are an expert software developer. Based on the following analysis, please fix the identified issues in the restaurant website project.

ROOT CAUSE ANALYSIS:
{root_cause.strip()}

RECTIFICATION STEPS TO IMPLEMENT:
{chr(10).join([f"{i+1}. {step}" for i, step in enumerate(rectification_steps)])}

TASK:
1. Review the root cause analysis above
2. Implement the rectification steps in the appropriate files
3. Focus on the restaurant website functionality (cart limits, search issues, performance)
4. Provide specific code changes with line numbers where possible
5. Ensure the fixes address the exact error identified in the logs

REQUIREMENTS:
- Make precise code changes based on the analysis
- Update the relevant JavaScript files (script.js, logger.js)
- Fix cart limit validation and search functionality
- Improve performance and user experience
- Test the changes to ensure they work correctly

Please implement these fixes step by step, explaining each change and why it's necessary."""

        # Create a new window to display the prompt
        prompt_window = tk.Toplevel(self)
        prompt_window.title("Golden Prompt for Cursor")
        prompt_window.geometry("800x600")
        
        # Create text widget with scrollbar
        text_frame = tk.Frame(prompt_window)
        text_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        prompt_text = scrolledtext.ScrolledText(text_frame, wrap="word", height=25)
        prompt_text.pack(fill="both", expand=True)
        prompt_text.insert("1.0", golden_prompt)
        prompt_text.config(state="disabled")  # Make it read-only
        
        # Add copy button
        button_frame = tk.Frame(prompt_window)
        button_frame.pack(fill="x", padx=10, pady=(0,10))
        
        def copy_to_clipboard():
            self.clipboard_clear()
            self.clipboard_append(golden_prompt)
            messagebox.showinfo("Copied", "Golden prompt copied to clipboard!")
        
        tk.Button(button_frame, text="Copy to Clipboard", command=copy_to_clipboard, bg="#2196F3", fg="white").pack(side="left")
        tk.Button(button_frame, text="Close", command=prompt_window.destroy, bg="#757575", fg="white").pack(side="right")

if __name__ == "__main__":
    try:
        app = App()
        app.mainloop()
    except KeyboardInterrupt:
        print("\nApplication interrupted by user.")
    except (OSError, ImportError) as e:
        print(f"Application error: {e}")
        print("Please check if all dependencies are installed correctly.")
