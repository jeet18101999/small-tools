import fitz  # PyMuPDF
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import os

class ThesisPDFProcessor:
    def __init__(self, root):
        self.root = root
        self.root.title("Thesis Color Detector & Reporter")
        self.root.geometry("550x450")

        # UI Styling
        tk.Label(root, text="PDF Color Page Extractor", font=("Arial", 14, "bold")).pack(pady=10)
        
        self.btn_select = tk.Button(root, text="Select PDF to Process", command=self.start_process, 
                                   width=25, bg="#4CAF50", fg="white", font=("Arial", 10, "bold"))
        self.btn_select.pack(pady=10)

        self.label_status = tk.Label(root, text="Status: Waiting for input...", fg="blue")
        self.label_status.pack(pady=5)

        self.progress = ttk.Progressbar(root, orient="horizontal", length=450, mode="determinate")
        self.progress.pack(pady=15)

        self.log_text = tk.Text(root, height=12, width=60, state='disabled', font=("Consolas", 9))
        self.log_text.pack(pady=5)

    def log(self, message):
        self.log_text.config(state='normal')
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')
        self.root.update_idletasks()

    def is_page_colored(self, page, doc):
        """Deep analysis of pixel data to avoid RGB-encoded Grayscale false positives."""
        # 1. Check Images
        for img in page.get_images():
            pix = fitz.Pixmap(doc, img[0])
            if pix.colorspace and pix.colorspace.n >= 3:
                samples = pix.samples
                # Check every 15th pixel for performance vs accuracy balance
                for i in range(0, len(samples), 3 * 15):
                    r, g, b = samples[i:i+3]
                    if abs(r - g) > 8 or abs(r - b) > 8: # Variance threshold
                        return True
        
        # 2. Check Vector Drawings (Charts/Graphs common in Thesis)
        for draw in page.get_drawings():
            fill = draw.get("fill")
            if fill and len(fill) == 3: # RGB Color
                if abs(fill[0]-fill[1]) > 0.05 or abs(fill[0]-fill[2]) > 0.05:
                    return True
        return False

    def start_process(self):
        input_pdf = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if not input_pdf: return
        
        # Determine output paths
        base_path = os.path.splitext(input_pdf)[0]
        output_pdf = base_path + "_COLOR_ONLY.pdf"
        report_txt = base_path + "_Color_Report.txt"

        try:
            doc = fitz.open(input_pdf)
            new_doc = fitz.open()
            total_pages = len(doc)
            color_pages = []

            self.log(f"Starting analysis of: {os.path.basename(input_pdf)}")
            
            for i in range(total_pages):
                page = doc[i]
                if self.is_page_colored(page, doc):
                    new_doc.insert_pdf(doc, from_page=i, to_page=i)
                    color_pages.append(i + 1)
                    self.log(f"[COLOR] Found on Page {i+1}")
                else:
                    self.log(f"[GRAY] Page {i+1} is grayscale")

                self.progress["value"] = ((i + 1) / total_pages) * 100
                self.label_status.config(text=f"Processing Page {i+1} of {total_pages}...")
                self.root.update_idletasks()

            # Save Results
            if color_pages:
                new_doc.save(output_pdf)
                # Create Text Report
                with open(report_txt, "w") as f:
                    f.write(f"Color Page Analysis Report\n")
                    f.write(f"Source: {input_pdf}\n")
                    f.write(f"Total Color Pages Found: {len(color_pages)}\n")
                    f.write(f"Page Numbers: {', '.join(map(str, color_pages))}\n")
                
                self.log(f"\n--- SUCCESS ---")
                self.log(f"PDF Saved: {os.path.basename(output_pdf)}")
                self.log(f"Report Saved: {os.path.basename(report_txt)}")
                messagebox.showinfo("Complete", f"Found {len(color_pages)} color pages.\nReport and PDF generated.")
            else:
                self.log("\n--- No Color Pages Detected ---")
                messagebox.showwarning("Result", "No color pages found using high-accuracy scan.")

            doc.close()
            new_doc.close()
            self.label_status.config(text="Status: Finished")

        except Exception as e:
            messagebox.showerror("Error", f"An error occurred: {str(e)}")

if __name__ == "__main__":
    root = tk.Tk()
    app = ThesisPDFProcessor(root)
    root.mainloop()