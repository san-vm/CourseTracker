import os
import re
import sys
import sqlite3
import time
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict

import customtkinter as ctk
from tkinter import filedialog, messagebox
from tkinter import ttk

# -----------------------------
# App configuration
# -----------------------------

APP_TITLE = "Course Tracker Pro"
DB_FILE = "course_tracker.db"

# Hide subtitles + noise
IGNORED_EXTENSIONS = {
	".vtt", ".srt", ".ass", ".ssa", ".sub", ".idx",
	".nfo", ".sfv", ".url", ".ds_store", ".tmp",
}

# Hide known junk folders; also "contains" matching is applied
IGNORED_FOLDER_EXACT = {
	"websites you may like",
	"sample files",
	"samples",
	"__macosx",
}

IGNORED_FOLDER_CONTAINS = {
	"website",
	"websites",
	"subtitle",
	"subtitles",
}

# Dark mode only
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# Accent colors
COLOR_GREEN = "#2e7d32"
COLOR_GREEN_HOVER = "#1b5e20"
COLOR_DANGER = "#b71c1c"
COLOR_DANGER_HOVER = "#7f0000"
COLOR_NEUTRAL = "#455A64"
COLOR_NEUTRAL_HOVER = "#37474F"

TEXT_MUTED = "#b8b8b8"
HIGHLIGHT_BG = "#2b2b2b"

# -----------------------------
# Helpers
# -----------------------------


def now_ts() -> int:
	return int(time.time())


def norm(s: str) -> str:
	return (s or "").strip().lower()


def natural_key(s: str):
	return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", s or "")]


def bytes_human(n: int) -> str:
	if n is None:
		return "0 B"
	units = ["B", "KB", "MB", "GB", "TB"]
	f = float(n)
	for u in units:
		if f < 1024.0 or u == units[-1]:
			if u == "B":
				return f"{int(f)} {u}"
			return f"{f:.2f} {u}"
		f /= 1024.0
	return f"{int(n)} B"


def safe_open_file(path: str):
	try:
		if os.name == "nt":
			os.startfile(path)
		elif sys.platform == "darwin":
			subprocess.call(["open", path])
		else:
			subprocess.call(["xdg-open", path])
	except Exception as e:
		messagebox.showerror("Open failed", f"Could not open:\n{path}\n\n{e}")


def reveal_in_file_manager(path: str):
	try:
		# If a file is passed, reveal its parent directory.
		target = path
		if os.path.isfile(path):
			target = os.path.dirname(path)

		if os.name == "nt":
			# Select file when possible.
			if os.path.isfile(path):
				os.system(f'explorer /select,"{path}"')
			else:
				os.startfile(target)  # type: ignore[attr-defined]
		elif sys.platform == "darwin":
			subprocess.call(["open", target])
		else:
			subprocess.call(["xdg-open", target])
	except Exception as e:
		messagebox.showerror("Explorer failed", f"Could not open file manager:\n{path}\n\n{e}")


def folder_is_ignored(folder_name: str) -> bool:
	n = norm(folder_name)
	if n in IGNORED_FOLDER_EXACT:
		return True
	for frag in IGNORED_FOLDER_CONTAINS:
		if frag in n:
			return True
	return False


# -----------------------------
# Data model
# -----------------------------

@dataclass
class CourseRow:
	id: int
	path: str
	name: str
	created_at: int
	last_opened_item_id: Optional[int]
	last_opened_at: Optional[int]


@dataclass
class GlobalLastOpened:
	course_id: int
	course_path: str
	course_name: str
	item_id: int
	abs_path: str
	rel_path: str
	last_opened_at: int


# -----------------------------
# Database layer
# -----------------------------

class DB:
	def __init__(self, db_path: str = DB_FILE):
		self.conn = sqlite3.connect(db_path)
		self.conn.row_factory = sqlite3.Row
		self._init()

	def _init(self):
		cur = self.conn.cursor()
		cur.execute("PRAGMA foreign_keys = ON;")
		cur.execute("PRAGMA journal_mode = WAL;")

		cur.execute("""
		CREATE TABLE IF NOT EXISTS courses (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			path TEXT NOT NULL UNIQUE,
			name TEXT NOT NULL,
			created_at INTEGER NOT NULL,
			last_opened_item_id INTEGER,
			last_opened_at INTEGER
		);
		""")

		cur.execute("""
		CREATE TABLE IF NOT EXISTS items (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			course_id INTEGER NOT NULL,
			rel_path TEXT NOT NULL,
			abs_path TEXT NOT NULL,
			section TEXT NOT NULL,
			name TEXT NOT NULL,
			ext TEXT NOT NULL,
			size_bytes INTEGER NOT NULL,
			mtime INTEGER NOT NULL,
			ignored INTEGER NOT NULL DEFAULT 0,
			UNIQUE(course_id, rel_path),
			FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
		);
		""")

		cur.execute("""
		CREATE TABLE IF NOT EXISTS progress (
			item_id INTEGER PRIMARY KEY,
			completed INTEGER NOT NULL DEFAULT 0,
			completed_at INTEGER,
			last_opened_at INTEGER,
			open_count INTEGER NOT NULL DEFAULT 0,
			FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
		);
		""")

		# Persist collapsed/expanded section state per course
		cur.execute("""
		CREATE TABLE IF NOT EXISTS section_state (
			course_id INTEGER NOT NULL,
			section TEXT NOT NULL,
			collapsed INTEGER NOT NULL DEFAULT 0,
			PRIMARY KEY(course_id, section),
			FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
		);
		""")

		cur.execute("CREATE INDEX IF NOT EXISTS idx_items_course ON items(course_id);")
		cur.execute("CREATE INDEX IF NOT EXISTS idx_items_course_ignored ON items(course_id, ignored);")
		cur.execute("CREATE INDEX IF NOT EXISTS idx_progress_last_opened ON progress(last_opened_at);")
		self.conn.commit()

	def close(self):
		try:
			self.conn.close()
		except Exception:
			pass

	# ----- courses -----

	def upsert_course(self, course_path: str) -> int:
		name = os.path.basename(course_path.rstrip("\\/")) or course_path
		ts = now_ts()
		cur = self.conn.cursor()
		cur.execute("""
		INSERT INTO courses(path, name, created_at)
		VALUES (?, ?, ?)
		ON CONFLICT(path) DO UPDATE SET
			name = excluded.name;
		""", (course_path, name, ts))
		self.conn.commit()
		return self.get_course_id(course_path)

	def get_course_id(self, course_path: str) -> int:
		cur = self.conn.cursor()
		cur.execute("SELECT id FROM courses WHERE path = ?", (course_path,))
		row = cur.fetchone()
		if not row:
			raise RuntimeError("Course not found after upsert.")
		return int(row["id"])

	def list_courses(self) -> List[CourseRow]:
		cur = self.conn.cursor()
		cur.execute("""
		SELECT id, path, name, created_at, last_opened_item_id, last_opened_at
		FROM courses
		ORDER BY COALESCE(last_opened_at, created_at) DESC, name ASC;
		""")
		out: List[CourseRow] = []
		for r in cur.fetchall():
			out.append(CourseRow(
				id=int(r["id"]),
				path=str(r["path"]),
				name=str(r["name"]),
				created_at=int(r["created_at"]),
				last_opened_item_id=(int(r["last_opened_item_id"]) if r["last_opened_item_id"] is not None else None),
				last_opened_at=(int(r["last_opened_at"]) if r["last_opened_at"] is not None else None),
			))
		return out

	def delete_course(self, course_id: int):
		cur = self.conn.cursor()
		cur.execute("DELETE FROM courses WHERE id = ?", (course_id,))
		self.conn.commit()

	# ----- section collapse state -----

	def get_section_collapsed_map(self, course_id: int) -> Dict[str, bool]:
		cur = self.conn.cursor()
		cur.execute("SELECT section, collapsed FROM section_state WHERE course_id = ?", (course_id,))
		out: Dict[str, bool] = {}
		for r in cur.fetchall():
			out[str(r["section"])] = (int(r["collapsed"]) == 1)
		return out

	def set_section_collapsed(self, course_id: int, section: str, collapsed: bool):
		cur = self.conn.cursor()
		cur.execute("""
		INSERT INTO section_state(course_id, section, collapsed)
		VALUES (?, ?, ?)
		ON CONFLICT(course_id, section) DO UPDATE SET
			collapsed = excluded.collapsed;
		""", (course_id, section, 1 if collapsed else 0))
		self.conn.commit()

	def clear_section_state(self, course_id: int):
		"""Reset remembered collapsed sections for this course."""
		cur = self.conn.cursor()
		cur.execute("DELETE FROM section_state WHERE course_id = ?", (course_id,))
		self.conn.commit()

	# ----- items/progress -----

	def upsert_item(
		self,
		course_id: int,
		rel_path: str,
		abs_path: str,
		section: str,
		name: str,
		ext: str,
		size_bytes: int,
		mtime: int,
		ignored: int
	) -> int:
		cur = self.conn.cursor()
		cur.execute("""
		INSERT INTO items(course_id, rel_path, abs_path, section, name, ext, size_bytes, mtime, ignored)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(course_id, rel_path) DO UPDATE SET
			abs_path = excluded.abs_path,
			section = excluded.section,
			name = excluded.name,
			ext = excluded.ext,
			size_bytes = excluded.size_bytes,
			mtime = excluded.mtime,
			ignored = excluded.ignored;
		""", (course_id, rel_path, abs_path, section, name, ext, int(size_bytes), int(mtime), int(ignored)))
		self.conn.commit()

		cur.execute("SELECT id FROM items WHERE course_id=? AND rel_path=?", (course_id, rel_path))
		item_id = int(cur.fetchone()["id"])

		cur.execute("INSERT INTO progress(item_id) VALUES (?) ON CONFLICT(item_id) DO NOTHING;", (item_id,))
		self.conn.commit()
		return item_id

	def delete_missing_items(self, course_id: int, keep_rel_paths: set):
		cur = self.conn.cursor()
		cur.execute("SELECT rel_path FROM items WHERE course_id = ?", (course_id,))
		existing = {str(r["rel_path"]) for r in cur.fetchall()}
		to_delete = existing - keep_rel_paths
		if not to_delete:
			return
		cur.executemany(
			"DELETE FROM items WHERE course_id=? AND rel_path=?",
			[(course_id, rp) for rp in to_delete]
		)
		self.conn.commit()

	def get_course_items(self, course_id: int, include_ignored: bool = False) -> List[sqlite3.Row]:
		cur = self.conn.cursor()
		if include_ignored:
			cur.execute("""
			SELECT i.*, p.completed, p.completed_at, p.last_opened_at, p.open_count
			FROM items i
			LEFT JOIN progress p ON p.item_id = i.id
			WHERE i.course_id = ?;
			""", (course_id,))
		else:
			cur.execute("""
			SELECT i.*, p.completed, p.completed_at, p.last_opened_at, p.open_count
			FROM items i
			LEFT JOIN progress p ON p.item_id = i.id
			WHERE i.course_id = ? AND i.ignored = 0;
			""", (course_id,))
		return cur.fetchall()

	def set_completed(self, item_id: int, completed: bool):
		cur = self.conn.cursor()
		ts = now_ts() if completed else None
		cur.execute("""
		UPDATE progress
		SET completed = ?,
			completed_at = ?
		WHERE item_id = ?;
		""", (1 if completed else 0, ts, item_id))
		self.conn.commit()

	def record_open(self, course_id: int, item_id: int):
		cur = self.conn.cursor()
		ts = now_ts()
		cur.execute("""
		UPDATE progress
		SET last_opened_at = ?,
			open_count = open_count + 1
		WHERE item_id = ?;
		""", (ts, item_id))
		cur.execute("""
		UPDATE courses
		SET last_opened_item_id = ?,
			last_opened_at = ?
		WHERE id = ?;
		""", (item_id, ts, course_id))
		self.conn.commit()

	def get_global_last_opened(self) -> Optional[GlobalLastOpened]:
		cur = self.conn.cursor()
		cur.execute("""
		SELECT
			c.id AS course_id, c.path AS course_path, c.name AS course_name,
			i.id AS item_id, i.abs_path AS abs_path, i.rel_path AS rel_path,
			p.last_opened_at AS last_opened_at
		FROM progress p
		JOIN items i ON i.id = p.item_id
		JOIN courses c ON c.id = i.course_id
		WHERE p.last_opened_at IS NOT NULL AND i.ignored = 0
		ORDER BY p.last_opened_at DESC
		LIMIT 1;
		""")
		r = cur.fetchone()
		if not r:
			return None
		return GlobalLastOpened(
			course_id=int(r["course_id"]),
			course_path=str(r["course_path"]),
			course_name=str(r["course_name"]),
			item_id=int(r["item_id"]),
			abs_path=str(r["abs_path"]),
			rel_path=str(r["rel_path"]),
			last_opened_at=int(r["last_opened_at"]),
		)

	def get_progress_for_course(self, course_id: int) -> Tuple[int, int, int, int]:
		cur = self.conn.cursor()
		cur.execute("""
		SELECT
			COALESCE(SUM(CASE WHEN p.completed=1 THEN 1 ELSE 0 END), 0) AS completed_count,
			COUNT(*) AS total_count,
			COALESCE(SUM(CASE WHEN p.completed=1 THEN i.size_bytes ELSE 0 END), 0) AS completed_bytes,
			COALESCE(SUM(i.size_bytes), 0) AS total_bytes
		FROM items i
		LEFT JOIN progress p ON p.item_id = i.id
		WHERE i.course_id = ? AND i.ignored = 0;
		""", (course_id,))
		r = cur.fetchone()
		return (int(r["completed_count"]), int(r["total_count"]), int(r["completed_bytes"]), int(r["total_bytes"]))

	def get_item_by_id(self, item_id: int) -> Optional[sqlite3.Row]:
		cur = self.conn.cursor()
		cur.execute("""
		SELECT i.*, p.completed, p.completed_at, p.last_opened_at, p.open_count
		FROM items i
		LEFT JOIN progress p ON p.item_id = i.id
		WHERE i.id = ?;
		""", (item_id,))
		return cur.fetchone()


# -----------------------------
# Scanner
# -----------------------------

class Scanner:
	def __init__(self, db: DB):
		self.db = db

	def scan_course(self, course_path: str) -> int:
		course_path = os.path.abspath(course_path)
		if not os.path.isdir(course_path):
			raise RuntimeError("Selected course path is not a folder.")

		course_id = self.db.upsert_course(course_path)
		keep_rel = set()

		try:
			top = [d for d in os.listdir(course_path) if os.path.isdir(os.path.join(course_path, d))]
		except Exception as e:
			raise RuntimeError(str(e))

		top.sort(key=natural_key)

		for section_name in top:
			if folder_is_ignored(section_name):
				continue

			section_path = os.path.join(course_path, section_name)

			for root, dirnames, filenames in os.walk(section_path):
				dirnames.sort(key=natural_key)
				filenames.sort(key=natural_key)

				# prune ignored folders
				pruned = [dn for dn in list(dirnames) if folder_is_ignored(dn)]
				for dn in pruned:
					if dn in dirnames:
						dirnames.remove(dn)

				for fn in filenames:
					abs_path = os.path.join(root, fn)
					if not os.path.isfile(abs_path):
						continue

					ext = os.path.splitext(fn)[1].lower()
					if ext in IGNORED_EXTENSIONS:
						continue

					rel_path = os.path.relpath(abs_path, course_path)
					keep_rel.add(rel_path)

					try:
						size_bytes = os.path.getsize(abs_path)
					except Exception:
						size_bytes = 0

					try:
						mtime = int(os.path.getmtime(abs_path))
					except Exception:
						mtime = 0

					self.db.upsert_item(
						course_id=course_id,
						rel_path=rel_path,
						abs_path=abs_path,
						section=section_name,
						name=fn,
						ext=ext,
						size_bytes=size_bytes,
						mtime=mtime,
						ignored=0
					)

		self.db.delete_missing_items(course_id, keep_rel_paths=keep_rel)
		return course_id


# -----------------------------
# UI Pages
# -----------------------------

class LibraryPage(ctk.CTkFrame):
	def __init__(self, master, app):
		super().__init__(master)
		self.app = app

		self.grid_rowconfigure(2, weight=1)
		self.grid_columnconfigure(0, weight=1)

		header = ctk.CTkFrame(self)
		header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
		header.grid_columnconfigure(2, weight=1)

		self.title_lbl = ctk.CTkLabel(header, text="Library", font=ctk.CTkFont(size=20, weight="bold"))
		self.title_lbl.grid(row=0, column=0, padx=10, pady=10, sticky="w")

		self.view_var = ctk.StringVar(value="Cards")
		self.view_toggle = ctk.CTkSegmentedButton(
			header, values=["Cards", "  List  "], variable=self.view_var, command=self._on_view_changed
		)
		self.view_toggle.grid(row=0, column=1, padx=10, pady=10, sticky="w")

		self.search_var = ctk.StringVar(value="")
		self.search_entry = ctk.CTkEntry(header, textvariable=self.search_var, placeholder_text="Search courses (name/path)...")
		self.search_entry.grid(row=0, column=2, padx=10, pady=10, sticky="ew")
		self.search_entry.bind("<KeyRelease>", lambda _e: self.refresh())

		self.stats_lbl = ctk.CTkLabel(self, text="", anchor="w")
		self.stats_lbl.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 6))

		self.cards_scroll = ctk.CTkScrollableFrame(self, label_text="Your Courses")
		self.cards_scroll.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
		self.cards_scroll.grid_columnconfigure(0, weight=1)

		self.table_frame = ctk.CTkFrame(self)
		self.table_frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
		self.table_frame.grid_rowconfigure(0, weight=1)
		self.table_frame.grid_columnconfigure(0, weight=1)

		self.tree = None
		self._build_table()
		self._on_view_changed(self.view_var.get())

	def _build_table(self):
		style = ttk.Style()
		try:
			style.theme_use("clam")
		except Exception:
			pass

		style.configure(
			"Treeview",
			background="#1f1f1f",
			fieldbackground="#1f1f1f",
			foreground="#eaeaea",
			rowheight=26
		)
		style.configure(
			"Treeview.Heading",
			background="#2b2b2b",
			foreground="#eaeaea"
		)

		columns = ("name", "files_pct", "size_pct", "last", "path")
		self.tree = ttk.Treeview(self.table_frame, columns=columns, show="headings", selectmode="browse")

		self.tree.heading("name", text="Your Courses")
		self.tree.heading("files_pct", text="Files %")
		self.tree.heading("size_pct", text="Size %")
		self.tree.heading("last", text="Last opened")
		self.tree.heading("path", text="Path")

		self.tree.column("name", width=240, anchor="w")
		self.tree.column("files_pct", width=80, anchor="center")
		self.tree.column("size_pct", width=80, anchor="center")
		self.tree.column("last", width=320, anchor="w")
		self.tree.column("path", width=520, anchor="w")

		self.tree.grid(row=0, column=0, sticky="nsew")
		self.tree.bind("<Double-1>", self._on_table_open)

		ysb = ttk.Scrollbar(self.table_frame, orient="vertical", command=self.tree.yview)
		self.tree.configure(yscroll=ysb.set)
		ysb.grid(row=0, column=1, sticky="ns")

	def _on_table_open(self, _evt=None):
		iid = self.tree.focus()
		if not iid:
			return
		try:
			course_id = int(iid)
		except ValueError:
			return
		self.app.open_course(course_id)

	def _on_view_changed(self, value: str):
		# Always forget both, then grid the chosen one with explicit geometry.
		self.cards_scroll.grid_forget()
		self.table_frame.grid_forget()

		grid_opts = dict(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))

		if value == "Cards":
			self.cards_scroll.grid(**grid_opts)
		else:
			self.table_frame.grid(**grid_opts)
		self.refresh()

	def refresh(self):
		courses = self.app.db.list_courses()
		q = norm(self.search_var.get())

		visible: List[CourseRow] = []
		for c in courses:
			if q and (q not in norm(c.name) and q not in norm(c.path)):
				continue
			visible.append(c)

		self.stats_lbl.configure(text=f"{len(visible)} course(s)")

		for w in self.cards_scroll.winfo_children():
			w.destroy()
		for iid in self.tree.get_children():
			self.tree.delete(iid)

		cols = 1
		rr = 0
		cc = 0

		for course in visible:
			completed_count, total_count, completed_bytes, total_bytes = self.app.db.get_progress_for_course(course.id)
			files_pct = (completed_count / total_count * 100.0) if total_count else 0.0
			size_pct = (completed_bytes / total_bytes * 100.0) if total_bytes else 0.0

			last_text = "N/A"
			if course.last_opened_item_id:
				it = self.app.db.get_item_by_id(course.last_opened_item_id)
				if it:
					last_text = str(it["rel_path"])

			# Cards view
			card = ctk.CTkFrame(self.cards_scroll)
			card.grid(row=rr, column=cc, sticky="nsew", padx=8, pady=8)
			card.grid_columnconfigure(0, weight=1)

			title = ctk.CTkLabel(card, text=course.name, font=ctk.CTkFont(size=16, weight="bold"), anchor="w")
			title.grid(row=0, column=0, padx=12, pady=(10, 0), sticky="ew")

			path_lbl = ctk.CTkLabel(card, text=course.path, anchor="w", text_color=TEXT_MUTED)
			path_lbl.grid(row=1, column=0, padx=12, pady=(2, 8), sticky="ew")

			p1 = ctk.CTkLabel(card, text=f"Files: {files_pct:.1f}% ({completed_count}/{total_count})", anchor="w")
			p1.grid(row=2, column=0, padx=12, pady=(2, 0), sticky="ew")

			p2 = ctk.CTkLabel(
				card,
				text=f"Size: {size_pct:.1f}% ({bytes_human(completed_bytes)} / {bytes_human(total_bytes)})",
				anchor="w"
			)
			p2.grid(row=3, column=0, padx=12, pady=(2, 0), sticky="ew")

			last_lbl = ctk.CTkLabel(card, text=f"Last: {last_text}", anchor="w")
			last_lbl.grid(row=4, column=0, padx=12, pady=(2, 10), sticky="ew")

			btn_row = ctk.CTkFrame(card, fg_color="transparent")
			btn_row.grid(row=5, column=0, padx=12, pady=(0, 12), sticky="ew")
			btn_row.grid_columnconfigure((0, 1, 2, 3, 4, 5, 6, 7, 8,), weight=1)

			open_btn = ctk.CTkButton(btn_row, text="Open", command=lambda cid=course.id: self.app.open_course(cid))
			open_btn.grid(row=0, column=0, padx=(0, 6), sticky="ew")

			cont_btn = ctk.CTkButton(
				btn_row,
				text="Continue",
				fg_color=COLOR_GREEN,
				hover_color=COLOR_GREEN_HOVER,
				command=lambda cid=course.id: self.app.continue_course(cid)
			)
			cont_btn.grid(row=0, column=1, padx=6, sticky="ew")

			folder_btn = ctk.CTkButton(
				btn_row,
				text="Folder",
				fg_color=COLOR_NEUTRAL,
				hover_color=COLOR_NEUTRAL_HOVER,
				command=lambda p=course.path: reveal_in_file_manager(p)
			)
			folder_btn.grid(row=0, column=2, padx=6, sticky="ew")

			del_btn = ctk.CTkButton(
				btn_row,
				text="Remove",
				fg_color=COLOR_DANGER,
				hover_color=COLOR_DANGER_HOVER,
				command=lambda cid=course.id, nm=course.name: self._remove_course(cid, nm)
			)
			del_btn.grid(row=0, column=3, padx=(6, 0), sticky="ew")

			cc += 1
			if cc >= cols:
				cc = 0
				rr += 1

			# Table view
			self.tree.insert(
				"", "end", iid=str(course.id),
				values=(course.name, f"{files_pct:.1f}%", f"{size_pct:.1f}%", last_text, course.path)
			)

	def _remove_course(self, course_id: int, course_name: str):
		ok = messagebox.askyesno(
			"Remove course",
			f"Remove '{course_name}' from library?\n\nThis deletes its tracking data (not the files)."
		)
		if not ok:
			return
		self.app.db.delete_course(course_id)
		self.refresh()


class CoursePage(ctk.CTkFrame):
	def __init__(self, master, app):
		super().__init__(master)
		self.app = app

		self.course_id: Optional[int] = None
		self.course_path: Optional[str] = None
		self.course_name: Optional[str] = None

		self.ordered_item_ids: List[int] = []
		self.item_widgets: Dict[int, Dict] = {}

		# section UI state
		self.section_headers: Dict[str, ctk.CTkFrame] = {}
		self.section_containers: Dict[str, ctk.CTkFrame] = {}
		self.section_toggle_btns: Dict[str, ctk.CTkButton] = {}

		# Persisted state (DB-backed)
		self.section_persisted_collapsed: Dict[str, bool] = {}
		# View state (can be temporarily overridden by "collapse all")
		self.section_view_collapsed: Dict[str, bool] = {}

		# Filters
		self.filter_var = ctk.StringVar(value="")
		self.hide_completed_var = ctk.BooleanVar(value=False)

		self.grid_rowconfigure(3, weight=1)
		self.grid_columnconfigure(0, weight=1)

		header = ctk.CTkFrame(self)
		header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
		header.grid_columnconfigure(2, weight=1)

		self.back_btn = ctk.CTkButton(header, text="← Library", width=110, command=self.app.show_library)
		self.back_btn.grid(row=0, column=0, padx=10, pady=10, sticky="w")

		self.title_lbl = ctk.CTkLabel(header, text="Course", font=ctk.CTkFont(size=18, weight="bold"), anchor="w")
		self.title_lbl.grid(row=0, column=1, padx=10, pady=10, sticky="w")

		self.open_folder_btn = ctk.CTkButton(
			header, text="Open folder",
			fg_color=COLOR_NEUTRAL, hover_color=COLOR_NEUTRAL_HOVER,
			command=self._open_course_folder
		)
		self.open_folder_btn.grid(row=0, column=3, padx=10, pady=10, sticky="e")

		self.rescan_btn = ctk.CTkButton(header, text="Rescan", command=self._rescan)
		self.rescan_btn.grid(row=0, column=4, padx=(0, 10), pady=10, sticky="e")

		# Progress bars
		prog = ctk.CTkFrame(self)
		prog.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))
		prog.grid_columnconfigure(1, weight=1)

		self.files_lbl = ctk.CTkLabel(prog, text="Files progress:", width=120, anchor="w")
		self.files_lbl.grid(row=0, column=0, padx=10, pady=(10, 0), sticky="w")

		self.files_bar = ctk.CTkProgressBar(prog)
		self.files_bar.grid(row=0, column=1, padx=10, pady=(10, 0), sticky="ew")

		self.files_text = ctk.CTkLabel(prog, text="0/0 (0%)", width=140, anchor="e")
		self.files_text.grid(row=0, column=2, padx=10, pady=(10, 0), sticky="e")

		self.size_lbl = ctk.CTkLabel(prog, text="Size progress:", width=120, anchor="w")
		self.size_lbl.grid(row=1, column=0, padx=10, pady=(8, 10), sticky="w")

		self.size_bar = ctk.CTkProgressBar(prog)
		self.size_bar.grid(row=1, column=1, padx=10, pady=(8, 10), sticky="ew")

		self.size_text = ctk.CTkLabel(prog, text="0 B / 0 B (0%)", width=220, anchor="e")
		self.size_text.grid(row=1, column=2, padx=10, pady=(8, 10), sticky="e")

		# Filters + section controls
		filters = ctk.CTkFrame(self)
		filters.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 6))
		filters.grid_columnconfigure(0, weight=1)

		self.filter_entry = ctk.CTkEntry(filters, textvariable=self.filter_var, placeholder_text="Filter files by name...")
		self.filter_entry.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
		self.filter_entry.bind("<KeyRelease>", lambda _e: self._rebuild_ui(highlight_last=False))

		self.hide_done_chk = ctk.CTkCheckBox(
			filters, text="Hide completed",
			variable=self.hide_completed_var, command=self._rebuild_ui
		)
		self.hide_done_chk.grid(row=0, column=1, padx=(0, 10), pady=10, sticky="e")

		# UX: label buttons according to semantics
		self.btn_expand_all = ctk.CTkButton(
			filters, text="Expand all",
			fg_color=COLOR_NEUTRAL, hover_color=COLOR_NEUTRAL_HOVER,
			command=self._expand_all_sections_reset_memory
		)
		self.btn_expand_all.grid(row=0, column=2, padx=(0, 10), pady=10, sticky="e")

		self.btn_collapse_all = ctk.CTkButton(
			filters, text="Collapse all",
			fg_color=COLOR_NEUTRAL, hover_color=COLOR_NEUTRAL_HOVER,
			command=self._collapse_all_sections_temporary
		)
		self.btn_collapse_all.grid(row=0, column=3, padx=(0, 10), pady=10, sticky="e")

		# Content
		self.content = ctk.CTkScrollableFrame(self, label_text="Sections & Files")
		self.content.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 10))

	def _open_course_folder(self):
		if self.course_path:
			reveal_in_file_manager(self.course_path)

	def load_course(self, course_id: int):
		self.course_id = course_id

		c = None
		for row in self.app.db.list_courses():
			if row.id == course_id:
				c = row
				break

		if not c:
			messagebox.showerror("Not found", "Course not found in library.")
			self.app.show_library()
			return

		self.course_path = c.path
		self.course_name = c.name
		self.title_lbl.configure(text=self.course_name)

		# Load persisted state; reset view state to match persisted state initially
		self.section_persisted_collapsed = self.app.db.get_section_collapsed_map(course_id)
		self.section_view_collapsed = dict(self.section_persisted_collapsed)

		self._rebuild_ui(highlight_last=True)

	def _rescan(self):
		if not self.course_path:
			return

		try:
			self.app.set_status("Scanning course...")
			self.app.scanner.scan_course(self.course_path)
			self.app.set_status("Scan complete.")
		except Exception as e:
			self.app.set_status("")
			messagebox.showerror("Scan failed", str(e))
			return

		self._rebuild_ui(highlight_last=True)
		self.app.library_page.refresh()

	# --- Memory rules ---

	def _collapse_all_sections_temporary(self):
		"""Temporary UI-only collapse: does not change DB memory."""
		if not self.course_id:
			return
		for s in list(self.section_containers.keys()):
			self.section_view_collapsed[s] = True
			self._apply_section_visibility(s)
		self._refresh_section_toggle_texts()

	def _expand_all_sections_reset_memory(self):
		"""Expand all + reset remembered collapsed sections (DB)."""
		if not self.course_id:
			return

		self.app.db.clear_section_state(self.course_id)

		for s in list(self.section_containers.keys()):
			self.section_persisted_collapsed[s] = False
			self.section_view_collapsed[s] = False
			self._apply_section_visibility(s)

		self._refresh_section_toggle_texts()

	# --- Individual toggles (persisted) ---

	def _toggle_section(self, section: str):
		if not self.course_id:
			return

		current_view = bool(self.section_view_collapsed.get(section, self.section_persisted_collapsed.get(section, False)))
		new_state = not current_view

		# Update view + persisted
		self.section_view_collapsed[section] = new_state
		self.section_persisted_collapsed[section] = new_state
		self.app.db.set_section_collapsed(self.course_id, section, collapsed=new_state)

		self._apply_section_visibility(section)
		self._refresh_section_toggle_texts()

	def _apply_section_visibility(self, section: str):
		container = self.section_containers.get(section)
		header = self.section_headers.get(section)
		if not container or not header:
			return

		collapsed = bool(self.section_view_collapsed.get(section, False))
		if collapsed:
			container.pack_forget()
		else:
			# Critical fix: ensure it reappears directly under its own header
			container.pack(after=header, fill="x", padx=16, pady=(0, 2))

	def _refresh_section_toggle_texts(self):
		for section, btn in self.section_toggle_btns.items():
			collapsed = bool(self.section_view_collapsed.get(section, False))
			btn.configure(text="▸" if collapsed else "▾")

	def _rebuild_ui(self, highlight_last: bool = False):
		for w in self.content.winfo_children():
			w.destroy()

		self.item_widgets.clear()
		self.ordered_item_ids.clear()
		self.section_headers.clear()
		self.section_containers.clear()
		self.section_toggle_btns.clear()

		if not self.course_id:
			return

		items = self.app.db.get_course_items(self.course_id, include_ignored=False)
		q = norm(self.filter_var.get())
		hide_done = bool(self.hide_completed_var.get())

		filtered: List[sqlite3.Row] = []
		for it in items:
			if hide_done and int(it["completed"] or 0) == 1:
				continue
			if q and q not in norm(it["name"]):
				continue
			filtered.append(it)

		items_sorted = sorted(filtered, key=lambda r: (natural_key(r["section"]), natural_key(r["rel_path"])))
		sec_map: Dict[str, List[sqlite3.Row]] = {}
		for it in items_sorted:
			sec_map.setdefault(str(it["section"]), []).append(it)

		# Ensure defaults exist for new sections
		for section in sec_map.keys():
			if section not in self.section_persisted_collapsed:
				self.section_persisted_collapsed[section] = False
			if section not in self.section_view_collapsed:
				self.section_view_collapsed[section] = self.section_persisted_collapsed[section]

		for section in sorted(sec_map.keys(), key=natural_key):
			sec_items = sec_map[section]

			# Header
			sec_header = ctk.CTkFrame(self.content)
			sec_header.pack(fill="x", padx=8, pady=(10, 4))
			self.section_headers[section] = sec_header

			toggle_btn = ctk.CTkButton(
				sec_header,
				text="▾",
				width=38,
				fg_color=COLOR_NEUTRAL,
				hover_color=COLOR_NEUTRAL_HOVER,
				command=lambda s=section: self._toggle_section(s)
			)
			toggle_btn.pack(side="left", padx=(10, 6), pady=8)
			self.section_toggle_btns[section] = toggle_btn

			# Make header label clickable too (small UX improvement)
			sec_lbl = ctk.CTkLabel(
				sec_header,
				text=section,
				font=ctk.CTkFont(size=15, weight="bold"),
				anchor="w"
			)
			sec_lbl.pack(side="left", padx=6, pady=8, fill="x", expand=True)
			sec_lbl.bind("<Button-1>", lambda _e, s=section: self._toggle_section(s))

			sec_total = len(sec_items)
			sec_done = sum(1 for it in sec_items if int(it["completed"] or 0) == 1)
			sec_prog = ctk.CTkLabel(sec_header, text=f"{sec_done}/{sec_total}", anchor="e")
			sec_prog.pack(side="right", padx=10, pady=8)

			# Container for rows (collapsible)
			container = ctk.CTkFrame(self.content, fg_color="transparent")
			self.section_containers[section] = container

			for it in sec_items:
				item_id = int(it["id"])
				self.ordered_item_ids.append(item_id)

				row = ctk.CTkFrame(container)
				row.pack(fill="x", pady=2)

				done = int(it["completed"] or 0) == 1
				var = ctk.BooleanVar(value=done)

				chk = ctk.CTkCheckBox(
					row,
					text="",
					width=22,
					variable=var,
					command=lambda iid=item_id, v=var: self._toggle_done(iid, v.get())
				)
				chk.pack(side="left", padx=(8, 6), pady=6)

				name = str(it["name"])
				size = int(it["size_bytes"] or 0)

				lbl = ctk.CTkLabel(row, text=name, anchor="w", justify="left")
				lbl.pack(side="left", padx=6, pady=6, fill="x", expand=True)

				def _update_wrap(_evt=None, _row=row, _lbl=lbl):
					# Reserve approx width for: checkbox + meta + 3 buttons + paddings (matches your widths). [file:1]
					reserved = 22 + 90 + 34 + 70 + 95 + 180
					w = _row.winfo_width()
					wrap = max(200, w - reserved)
					_lbl.configure(wraplength=wrap)

				row.bind("<Configure>", _update_wrap)

				meta = ctk.CTkLabel(row, text=bytes_human(size), width=90, anchor="e", text_color=TEXT_MUTED)
				meta.pack(side="left", padx=6, pady=6)

				btn_reveal = ctk.CTkButton(
					row,
					text="↗",
					width=34,
					fg_color=COLOR_NEUTRAL,
					hover_color=COLOR_NEUTRAL_HOVER,
					command=lambda p=str(it["abs_path"]): reveal_in_file_manager(p)
				)
				btn_reveal.pack(side="right", padx=(6, 8), pady=6)

				btn_open = ctk.CTkButton(row, text="Open", width=70, command=lambda iid=item_id: self._open_item(iid))
				btn_open.pack(side="right", padx=6, pady=6)

				btn_next = ctk.CTkButton(
					row,
					text="Open Next",
					width=95,
					fg_color=COLOR_GREEN,
					hover_color=COLOR_GREEN_HOVER,
					command=lambda iid=item_id: self._open_next_from(iid)
				)
				btn_next.pack(side="right", padx=6, pady=6)

				self.item_widgets[item_id] = {"frame": row, "var": var}

		# Apply collapse states after building (position-safe)
		for section in list(self.section_containers.keys()):
			self._apply_section_visibility(section)

		self._refresh_section_toggle_texts()
		self._update_progress_ui()

		if highlight_last:
			self._highlight_last_opened_if_any()

	def _highlight_last_opened_if_any(self):
		if not self.course_id:
			return
		last_item_id = None
		for c in self.app.db.list_courses():
			if c.id == self.course_id:
				last_item_id = c.last_opened_item_id
				break
		if last_item_id and last_item_id in self.item_widgets:
			self._highlight(last_item_id)

	def _toggle_done(self, item_id: int, done: bool):
		self.app.db.set_completed(item_id, done)
		self._update_progress_ui()
		self.app.library_page.refresh()

	def _update_progress_ui(self):
		if not self.course_id:
			return
		cc, tc, cb, tb = self.app.db.get_progress_for_course(self.course_id)

		files_ratio = (cc / tc) if tc else 0.0
		size_ratio = (cb / tb) if tb else 0.0

		self.files_bar.set(files_ratio)
		self.size_bar.set(size_ratio)

		self.files_text.configure(text=f"{cc}/{tc} ({files_ratio * 100:.1f}%)")
		self.size_text.configure(text=f"{bytes_human(cb)} / {bytes_human(tb)} ({size_ratio * 100:.1f}%)")

	def _open_item(self, item_id: int):
		if not self.course_id:
			return
		it = self.app.db.get_item_by_id(item_id)
		if not it:
			return

		safe_open_file(str(it["abs_path"]))
		self.app.db.record_open(self.course_id, item_id)
		self.app.library_page.refresh()
		self._highlight(item_id)

	def _open_next_from(self, item_id: int):
		self.app.db.set_completed(item_id, True)

		# NEW: update the checkbox UI state immediately
		w = self.item_widgets.get(item_id)
		if w:
			try:
				w["var"].set(True)
			except Exception:
				pass

		# If "Hide completed" is ON, the row should disappear from the list
		if bool(self.hide_completed_var.get()):
			# rebuild will re-filter out completed items
			self._rebuild_ui(highlight_last=False)

		try:
			idx = self.ordered_item_ids.index(item_id)
		except ValueError:
			return

		if idx + 1 >= len(self.ordered_item_ids):
			return

		next_id = self.ordered_item_ids[idx + 1]
		self._open_item(next_id)

		# Update progress UI and other pages
		self._update_progress_ui()
		self.app.library_page.refresh()

	def _highlight(self, item_id: int):
		for iid, w in self.item_widgets.items():
			frame = w["frame"]
			if iid == item_id:
				frame.configure(fg_color=(HIGHLIGHT_BG, HIGHLIGHT_BG))
			else:
				frame.configure(fg_color=("gray85", "gray20"))


# -----------------------------
# Main App
# -----------------------------

class App(ctk.CTk):
	def __init__(self):
		super().__init__()

		self.title(APP_TITLE)
		self.geometry("1240x780")
		self.minsize(1050, 650)

		self.db = DB(DB_FILE)
		self.scanner = Scanner(self.db)

		self.grid_rowconfigure(0, weight=1)
		self.grid_columnconfigure(1, weight=1)

		# Sidebar
		self.sidebar = ctk.CTkFrame(self, corner_radius=0, width=150)
		self.sidebar.grid(row=0, column=0, sticky="nsew")
		self.sidebar.grid_rowconfigure(20, weight=1)

		title = ctk.CTkLabel(self.sidebar, text="Course Tracker", font=ctk.CTkFont(size=20, weight="bold"))
		title.grid(row=0, column=0, padx=16, pady=(16, 8), sticky="w")

		self.btn_library = ctk.CTkButton(self.sidebar, text="Library", command=self.show_library)
		self.btn_library.grid(row=1, column=0, padx=16, pady=(8, 6), sticky="ew")

		self.btn_add = ctk.CTkButton(self.sidebar, text="Add Course Folder", command=self.add_course_folder)
		self.btn_add.grid(row=2, column=0, padx=16, pady=6, sticky="ew")

		self.btn_show_last = ctk.CTkButton(
			self.sidebar,
			text="Show last file",
			fg_color=COLOR_NEUTRAL,
			hover_color=COLOR_NEUTRAL_HOVER,
			command=self.show_last_file_global
		)
		self.btn_show_last.grid(row=3, column=0, padx=16, pady=(18, 6), sticky="ew")

		self.btn_open_next_last = ctk.CTkButton(
			self.sidebar,
			text="Open next (from last)",
			fg_color=COLOR_GREEN,
			hover_color=COLOR_GREEN_HOVER,
			command=self.open_next_from_last_global
		)
		self.btn_open_next_last.grid(row=4, column=0, padx=16, pady=6, sticky="ew")

		# Quick tip
		tip = ctk.CTkFrame(self.sidebar)
		tip.grid(row=19, column=0, padx=16, pady=(10, 6), sticky="ew")
		ctk.CTkLabel(
			tip,
			text="Tip: Collapse sections to focus.\nCollapse all is temporary; Expand all resets memory.",
			anchor="w",
			text_color=TEXT_MUTED
		).pack(fill="x", padx=10, pady=10)

		self.status_var = ctk.StringVar(value="")
		self.status_lbl = ctk.CTkLabel(self.sidebar, textvariable=self.status_var, anchor="w", text_color=TEXT_MUTED)
		self.status_lbl.grid(row=21, column=0, padx=16, pady=(6, 16), sticky="ew")

		# Main container
		self.container = ctk.CTkFrame(self, corner_radius=0)
		self.container.grid(row=0, column=1, sticky="nsew")
		self.container.grid_rowconfigure(0, weight=1)
		self.container.grid_columnconfigure(0, weight=1)

		self.library_page = LibraryPage(self.container, self)
		self.course_page = CoursePage(self.container, self)

		self.library_page.grid(row=0, column=0, sticky="nsew")
		self.course_page.grid(row=0, column=0, sticky="nsew")

		self.show_library()
		self.library_page.refresh()

		self.protocol("WM_DELETE_WINDOW", self.on_close)

		# at the END of __init__:
		self.after(0, self._maximize_on_start)

	def _maximize_on_start(self):
			try:
				self.update_idletasks()
				if os.name == "nt":
					self.state("zoomed")  # Windows
				else:
					self.attributes("-zoomed", True)  # Linux (many WMs)
			except Exception:
				pass
			
	def on_close(self):
		try:
			self.db.close()
		finally:
			self.destroy()

	def set_status(self, text: str):
		self.status_var.set(text)

	def show_library(self):
		self.library_page.tkraise()
		self.library_page.refresh()

	def open_course(self, course_id: int):
		self.course_page.tkraise()
		self.course_page.load_course(course_id)

	def continue_course(self, course_id: int):
		self.open_course(course_id)
		for c in self.db.list_courses():
			if c.id == course_id and c.last_opened_item_id:
				self.course_page._open_item(c.last_opened_item_id)
				return

	def add_course_folder(self):
		path = filedialog.askdirectory(title="Select a course folder")
		if not path:
			return
		path = os.path.abspath(path)

		try:
			self.set_status("Scanning new course...")
			course_id = self.scanner.scan_course(path)
			self.set_status("Course added.")
		except Exception as e:
			self.set_status("")
			messagebox.showerror("Add course failed", str(e))
			return

		self.library_page.refresh()
		self.open_course(course_id)

	def show_last_file_global(self):
		last = self.db.get_global_last_opened()
		if not last:
			messagebox.showinfo("No history", "No last opened file found yet.")
			return

		self.open_course(last.course_id)
		self.course_page._highlight(last.item_id)

	def open_next_from_last_global(self):
		last = self.db.get_global_last_opened()
		if not last:
			messagebox.showinfo("No history", "No last opened file found yet.")
			return

		self.open_course(last.course_id)
		self.course_page._open_next_from(last.item_id)


if __name__ == "__main__":
	app = App()
	app.mainloop()

# pyinstaller --windowed CourseTracker.py
# pyinstaller --onefile CourseTracker.py