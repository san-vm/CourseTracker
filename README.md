# CourseTracker — Offline Course Progress Manager

**CourseTracker** is a lightweight Python application built with `tkinter` that helps you organize, track, and manage progress across your downloaded courses — even when offline.

No more hunting for “where you left off” or opening random files. CourseTracker creates an interactive course library where you can view modules, mark them complete, and open files directly using your system's default handlers.

---

## 🚀 Features

- **Organized Course Management** — Add multiple courses and manage them from one dashboard.  
- **Progress Tracking** — Mark lessons or modules as *complete* and instantly view your overall progress.  
- **Smart File Detection** — Automatically hides subtitle and non-course files like `.srt`, `.vtt`, etc.  
- **Offline Ready** — 100% functional offline. Perfect for locally stored courses.  
- **Native File Opening** — Opens video, PDF, or any course material using your system’s default app — nothing runs internally.  
- **Modern Tkinter UI** — Clean, responsive, and intuitive interface built with `tkinter` and advanced widget styling.  

---

## 🖼️ Interface Overview

- **Library View** — Displays all added courses with completion stats.  
- **Modules Page** — Shows all files (sections/lessons) in the selected course.  
- **Progress Bar** — Updates automatically as you mark lessons complete.  

*(Insert screenshots or demo GIFs here to showcase the interface.)*

---

## 🛠️ Installation

Clone this repository and install the dependencies:

TKinter and customTkinter.


## 📂 How It Works

1. **Add a course** by selecting its local folder through the GUI.  
2. **Automatic structure generation** — CourseTracker scans the folder for valid course media (videos, PDFs, etc.) and builds an interactive lesson layout.  
3. **Subtitle and junk files** (like `.srt`, `.vtt`, `.txt`) are hidden automatically to keep your library clean.  
4. **Open lessons instantly** — Clicking any module launches the file using your system’s default handler (e.g., video player, PDF reader).  
5. **Track progress visually** — Mark completed lessons, and your progress bar updates in real time.  
6. **Multiple course support** — Manage any number of local courses from one unified dashboard.

---

## ⚙️ Tech Stack

- **Language:** Python 3  
- **GUI Framework:** Tkinter (custom-themed UI)  
- **Core Libraries:** `os`, `pathlib`, `json`, `subprocess`, `tkinter`, `shutil`  
- **Supported Platforms:** Windows, macOS, Linux  

---

## 📦 Build (Optional)

To create a standalone executable using [PyInstaller](https://pyinstaller.org/):

```bash
pyinstaller --onefile --noconsole main.py
```