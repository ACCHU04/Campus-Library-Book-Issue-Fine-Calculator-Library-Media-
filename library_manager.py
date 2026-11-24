"""
===============================================================================
 Campus Library Book Issue & Fine Calculator
-------------------------------------------------------------------------------
 MCA III Semester - Python Programming Lab Mini Project (2026-27)
 Chanakya University, School of Engineering, Dept. of Computer Applications

 Student   : Acchutha K S
 Reg. No.  : 25PG00004
 Domain    : Library & Media

 Description
 -----------
 A standalone desktop application built with Python Tkinter (GUI) and SQLite
 (database, through the built-in sqlite3 module). The librarian can:
   * add / view / update / delete BOOKS            (full CRUD)
   * add / view / update / delete MEMBERS          (full CRUD)
   * ISSUE a book to a member (due date = issue date + loan period)
   * RETURN a book and store the late fine
   * CALCULATE the fine for any issue before returning it
   * search records and filter issues (All / Active / Overdue / Returned)
   * see the raw database tables update live (Database Tables tab)

 Database tables (library.db)
 ----------------------------
   books(book_id PK, isbn, title, author, total_copies, available_copies)
   members(member_id PK, reg_no, name, max_allowed)
   transactions(issue_id PK, book_id FK, member_id FK,
                issue_date, due_date, return_date, fine_amount)

 Key techniques
 --------------
   * Every SQL statement uses '?' placeholders (parameterized queries) so
     user input can never be executed as SQL (prevents SQL injection).
   * Issue and Return touch two tables; they run as one transaction with
     commit() on success and rollback() on any error.
   * Input validation with regular expressions (ISBN, register number,
     names, dates) and messagebox alerts for every error / confirmation.
   * ttk.Treeview tables with vertical scrollbars; overdue rows in red.
   * DateEntry calendar widget (tkcalendar) for dates. If tkcalendar is not
     installed, a masked YYYY-MM-DD text entry is used instead.
   * Every cursor is opened, used and closed in try/finally; sqlite3.Error is
     caught (rollback + messagebox), and IntegrityError gives a specific
     "duplicate ISBN / register number" message.

 Event bindings used
 -------------------
   command=...               Button clicks (Add, Update, Delete, Issue, ...)
   <<TreeviewSelect>>        clicking a row fills the form above the table
   <KeyRelease>              live search while typing in a search box
   <<ComboboxSelected>>      choosing a table in the Database Tables tab
   <<NotebookTabChanged>>    switching tabs reloads all data
   <<DateEntrySelected>>     picking an issue date recalculates the due date
   WM_DELETE_WINDOW          closing the window closes the database connection

 How to run
 ----------
   python library_manager.py
   (Optional, for the calendar pop-up:  pip install tkcalendar)
   The database file library.db is created next to this script on the first
   run and filled with a few sample records.
===============================================================================
"""

import functools
import re
import sqlite3
import tkinter as tk
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk

# DateEntry gives a drop-down calendar. It comes from the optional
# 'tkcalendar' package, so the program still works without it.
try:
    from tkcalendar import DateEntry as _DateEntry

    class DateEntry(_DateEntry):
        """tkcalendar's DateEntry with one fix: when a messagebox takes the
        focus, focus_get() raises KeyError inside tkcalendar. We catch it and
        simply close the drop-down calendar."""

        def _on_focus_out_cal(self, event):
            try:
                super()._on_focus_out_cal(event)
            except KeyError:
                self._top_cal.withdraw()
                self.state(["!pressed"])

    HAS_TKCALENDAR = True
except ImportError:
    HAS_TKCALENDAR = False

# ----------------------------------------------------------------------------
# Configuration (library rules)
# ----------------------------------------------------------------------------
DB_FILE = Path(__file__).with_name("library.db")
LOAN_DAYS = 14            # a book is issued for 14 days
FINE_PER_DAY = 2.00       # fine in rupees for each day after the due date
DEFAULT_MAX_BOOKS = 3     # default number of books a member may hold
DATE_FMT = "%Y-%m-%d"     # dates are stored in ISO format, e.g. 2026-11-24

# Regular expressions used for validation
REG_NO_PATTERN = re.compile(r"^\d{2}[A-Z]{2}\d{5}$")        # e.g. 25PG00004
NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z .']{1,49}$")    # letters, space, dot
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")            # YYYY-MM-DD
ISBN10_PATTERN = re.compile(r"^\d{9}[\dX]$")
ISBN13_PATTERN = re.compile(r"^\d{13}$")


# ============================================================================
# Validation helpers
# ============================================================================
def clean_isbn(raw):
    """Remove hyphens and spaces from an ISBN and make 'x' upper case."""
    return re.sub(r"[-\s]", "", raw).upper()


def is_valid_isbn(isbn):
    """Check the ISBN-10 / ISBN-13 format with regex and verify its check digit."""
    if ISBN13_PATTERN.match(isbn):
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(isbn))
        return total % 10 == 0
    if ISBN10_PATTERN.match(isbn):
        total = sum((10 - i) * (10 if d == "X" else int(d)) for i, d in enumerate(isbn))
        return total % 11 == 0
    return False


def parse_date(text):
    """Convert 'YYYY-MM-DD' text to a date object; raise ValueError if invalid."""
    text = text.strip()
    if not DATE_PATTERN.match(text):
        raise ValueError(f"'{text}' is not in YYYY-MM-DD format.")
    try:
        return datetime.strptime(text, DATE_FMT).date()   # also rejects 2026-02-30
    except ValueError:
        raise ValueError(f"'{text}' is not a real calendar date.") from None


def calculate_fine(due_date, return_date):
    """Fine = number of days late x FINE_PER_DAY (never negative)."""
    days_late = (return_date - due_date).days
    return max(0, days_late) * FINE_PER_DAY, max(0, days_late)


# ============================================================================
# Database layer: all SQL lives here
# ============================================================================
class LibraryDB:
    """Wraps the SQLite connection and every query the GUI needs.

    Cursor lifecycle: every method opens its own cursor, uses it, and closes it
    in a 'finally' block. Changes are saved with commit(); if any sqlite3.Error
    happens during a change, rollback() undoes it before the error is passed
    on to the GUI, which shows it in a messagebox.
    """

    # Fixed SELECT statements for the raw "Database Tables" viewer. Table names
    # cannot be '?' parameters, so only these three ready-made queries are used.
    RAW_TABLE_QUERIES = {
        "books": "SELECT * FROM books ORDER BY book_id",
        "members": "SELECT * FROM members ORDER BY member_id",
        "transactions": "SELECT * FROM transactions ORDER BY issue_id",
    }

    def __init__(self, path=DB_FILE):
        self.path = path
        self.conn = sqlite3.connect(path)               # opens / creates the .db file
        self._execute_write("PRAGMA foreign_keys = ON")  # enforce FK rules
        self.create_tables()
        if self.book_count() == 0:
            self.insert_sample_data()

    # ------------------------------------------------------ cursor helpers
    def _fetch_all(self, sql, params=()):
        """Run a SELECT with '?' parameters and return all rows."""
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            return cur.fetchall()
        finally:
            cur.close()

    def _fetch_one(self, sql, params=()):
        """Run a SELECT with '?' parameters and return the first row (or None)."""
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            return cur.fetchone()
        finally:
            cur.close()

    def _execute_write(self, sql, params=()):
        """Run INSERT / UPDATE / DELETE, commit it, and return the new row id."""
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            self.conn.commit()                 # save the change to library.db
            return cur.lastrowid
        except sqlite3.Error:
            self.conn.rollback()               # undo a half-done change
            raise                              # GUI shows the error message
        finally:
            cur.close()

    # ------------------------------------------------------------- schema
    def create_tables(self):
        cur = self.conn.cursor()
        try:
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS books (
                    book_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    isbn             TEXT    NOT NULL UNIQUE,
                    title            TEXT    NOT NULL,
                    author           TEXT    NOT NULL,
                    total_copies     INTEGER NOT NULL CHECK (total_copies > 0),
                    available_copies INTEGER NOT NULL
                        CHECK (available_copies BETWEEN 0 AND total_copies)
                );

                CREATE TABLE IF NOT EXISTS members (
                    member_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                    reg_no      TEXT    NOT NULL UNIQUE,
                    name        TEXT    NOT NULL,
                    max_allowed INTEGER NOT NULL DEFAULT 3 CHECK (max_allowed > 0)
                );

                CREATE TABLE IF NOT EXISTS transactions (
                    issue_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    book_id     INTEGER NOT NULL
                                REFERENCES books(book_id) ON DELETE CASCADE,
                    member_id   INTEGER NOT NULL
                                REFERENCES members(member_id) ON DELETE CASCADE,
                    issue_date  TEXT    NOT NULL,
                    due_date    TEXT    NOT NULL,
                    return_date TEXT,                   -- NULL while book is out
                    fine_amount REAL    NOT NULL DEFAULT 0
                );
            """)
            self.conn.commit()
        finally:
            cur.close()

    def book_count(self):
        return self._fetch_one("SELECT COUNT(*) FROM books")[0]

    def insert_sample_data(self):
        """Fill an empty database with demo records (dates relative to today)."""
        books = [
            ("9781718502703", "Python Crash Course (3rd Ed.)", "Eric Matthes", 4),
            ("9781593279929", "Automate the Boring Stuff with Python", "Al Sweigart", 3),
            ("9781492056355", "Fluent Python (2nd Ed.)", "Luciano Ramalho", 2),
            ("9781449355739", "Learning Python (5th Ed.)", "Mark Lutz", 2),
            ("9780262033848", "Introduction to Algorithms (3rd Ed.)", "Cormen, Leiserson, Rivest, Stein", 3),
            ("9780078022159", "Database System Concepts (7th Ed.)", "Silberschatz, Korth, Sudarshan", 3),
            ("9780132350884", "Clean Code", "Robert C. Martin", 2),
            ("9780134685991", "Effective Java (3rd Ed.)", "Joshua Bloch", 1),
        ]
        members = [
            ("25PG00004", "Acchutha K S", 3),
            ("25PG00101", "Rahul Menon", 3),
            ("25PG00102", "Sneha Rao", 2),
            ("25PG00103", "Imran Pasha", 3),
            ("25PG00104", "Divya Hegde", 4),
        ]
        cur = self.conn.cursor()
        try:
            cur.executemany(
                "INSERT INTO books (isbn, title, author, total_copies, available_copies) "
                "VALUES (?, ?, ?, ?, ?)", [(i, t, a, c, c) for i, t, a, c in books])
            cur.executemany(
                "INSERT INTO members (reg_no, name, max_allowed) VALUES (?, ?, ?)", members)
            self.conn.commit()
        except sqlite3.Error:
            self.conn.rollback()
            raise
        finally:
            cur.close()

        today = date.today()
        # (book_id, member_id, issued N days ago, returned N days ago or None)
        history = [
            (1, 1, 5, None),     # active, not yet due
            (3, 2, 20, None),    # overdue by 6 days
            (5, 3, 30, 10),      # returned 6 days late (fine Rs 12)
            (6, 1, 25, 13),      # returned on time
            (2, 4, 16, None),    # overdue by 2 days
            (7, 5, 3, None),     # active
        ]
        for book_id, member_id, issued_ago, returned_ago in history:
            issued = today - timedelta(days=issued_ago)
            issue_id = self.issue_book(book_id, member_id, issued,
                                       issued + timedelta(days=LOAN_DAYS))
            if returned_ago is not None:
                self.return_book(issue_id, today - timedelta(days=returned_ago))

    # ---------------------------------------------------------------- books
    def get_books(self, search=""):
        like = f"%{search}%"          # the value is passed as a '?' parameter
        return self._fetch_all(
            "SELECT book_id, isbn, title, author, total_copies, available_copies "
            "FROM books WHERE title LIKE ? OR author LIKE ? OR isbn LIKE ? "
            "ORDER BY title", (like, like, like))

    def add_book(self, isbn, title, author, copies):
        return self._execute_write(
            "INSERT INTO books (isbn, title, author, total_copies, available_copies) "
            "VALUES (?, ?, ?, ?, ?)", (isbn, title, author, copies, copies))

    def update_book(self, book_id, isbn, title, author, new_total):
        """Change details; available copies move by the change in total copies."""
        row = self._fetch_one(
            "SELECT total_copies, available_copies FROM books WHERE book_id = ?", (book_id,))
        if row is None:
            raise ValueError("This book no longer exists.")
        total, available = row
        issued_now = total - available
        if new_total < issued_now:
            raise ValueError(f"{issued_now} copies are currently issued, so total "
                             f"copies cannot be less than {issued_now}.")
        self._execute_write(
            "UPDATE books SET isbn = ?, title = ?, author = ?, total_copies = ?, "
            "available_copies = ? WHERE book_id = ?",
            (isbn, title, author, new_total, new_total - issued_now, book_id))

    def delete_book(self, book_id):
        self._execute_write("DELETE FROM books WHERE book_id = ?", (book_id,))

    def available_books(self):
        return self._fetch_all(
            "SELECT book_id, title, available_copies FROM books "
            "WHERE available_copies > 0 ORDER BY title")

    # -------------------------------------------------------------- members
    def get_members(self, search=""):
        like = f"%{search}%"
        return self._fetch_all(
            "SELECT m.member_id, m.reg_no, m.name, m.max_allowed, "
            "       (SELECT COUNT(*) FROM transactions t "
            "        WHERE t.member_id = m.member_id AND t.return_date IS NULL) "
            "FROM members m WHERE m.reg_no LIKE ? OR m.name LIKE ? "
            "ORDER BY m.reg_no", (like, like))

    def add_member(self, reg_no, name, max_allowed):
        return self._execute_write(
            "INSERT INTO members (reg_no, name, max_allowed) VALUES (?, ?, ?)",
            (reg_no, name, max_allowed))

    def update_member(self, member_id, reg_no, name, max_allowed):
        self._execute_write(
            "UPDATE members SET reg_no = ?, name = ?, max_allowed = ? WHERE member_id = ?",
            (reg_no, name, max_allowed, member_id))

    def delete_member(self, member_id):
        self._execute_write("DELETE FROM members WHERE member_id = ?", (member_id,))

    # ---------------------------------------------------------- transactions
    def active_issues_of_book(self, book_id):
        return self._fetch_one("SELECT COUNT(*) FROM transactions WHERE book_id = ? "
                               "AND return_date IS NULL", (book_id,))[0]

    def active_issues_of_member(self, member_id):
        return self._fetch_one("SELECT COUNT(*) FROM transactions WHERE member_id = ? "
                               "AND return_date IS NULL", (member_id,))[0]

    def book_history_count(self, book_id):
        return self._fetch_one("SELECT COUNT(*) FROM transactions WHERE book_id = ?",
                               (book_id,))[0]

    def member_history_count(self, member_id):
        return self._fetch_one("SELECT COUNT(*) FROM transactions WHERE member_id = ?",
                               (member_id,))[0]

    def issue_book(self, book_id, member_id, issue_date, due_date):
        """Insert an issue record and reduce available copies - ONE transaction."""
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT title, available_copies FROM books WHERE book_id = ?",
                        (book_id,))
            book = cur.fetchone()
            cur.execute("SELECT name, max_allowed FROM members WHERE member_id = ?",
                        (member_id,))
            member = cur.fetchone()
            if book is None or member is None:
                raise ValueError("Selected book or member no longer exists.")
            if book[1] <= 0:
                raise ValueError(f"No copies of '{book[0]}' are available right now.")
            cur.execute("SELECT COUNT(*) FROM transactions WHERE member_id = ? "
                        "AND return_date IS NULL", (member_id,))
            held = cur.fetchone()[0]
            if held >= member[1]:
                raise ValueError(f"{member[0]} already holds {held} book(s), "
                                 f"which is the maximum allowed ({member[1]}).")
            cur.execute("SELECT COUNT(*) FROM transactions WHERE book_id = ? AND "
                        "member_id = ? AND return_date IS NULL", (book_id, member_id))
            if cur.fetchone()[0]:
                raise ValueError(f"{member[0]} already has a copy of '{book[0]}'.")

            cur.execute("INSERT INTO transactions (book_id, member_id, issue_date, due_date) "
                        "VALUES (?, ?, ?, ?)",
                        (book_id, member_id, issue_date.strftime(DATE_FMT),
                         due_date.strftime(DATE_FMT)))
            new_id = cur.lastrowid
            cur.execute("UPDATE books SET available_copies = available_copies - 1 "
                        "WHERE book_id = ?", (book_id,))
            self.conn.commit()               # both changes saved together
            return new_id
        except (ValueError, sqlite3.Error):
            self.conn.rollback()             # undo both if anything failed
            raise
        finally:
            cur.close()

    def get_issue(self, issue_id):
        return self._fetch_one(
            "SELECT t.issue_id, b.title, m.name, t.issue_date, t.due_date, t.return_date, "
            "t.fine_amount, t.book_id FROM transactions t "
            "JOIN books b ON b.book_id = t.book_id "
            "JOIN members m ON m.member_id = t.member_id WHERE t.issue_id = ?",
            (issue_id,))

    def return_book(self, issue_id, return_date):
        """Store return date + fine and add the copy back - ONE transaction."""
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT book_id, issue_date, due_date, return_date "
                        "FROM transactions WHERE issue_id = ?", (issue_id,))
            row = cur.fetchone()
            if row is None:
                raise ValueError("Issue record not found.")
            book_id, issue_d, due_d, returned = row
            if returned is not None:
                raise ValueError(f"This book was already returned on {returned}.")
            if return_date < parse_date(issue_d):
                raise ValueError("Return date cannot be before the issue date.")
            fine, days_late = calculate_fine(parse_date(due_d), return_date)
            cur.execute("UPDATE transactions SET return_date = ?, fine_amount = ? "
                        "WHERE issue_id = ?",
                        (return_date.strftime(DATE_FMT), fine, issue_id))
            cur.execute("UPDATE books SET available_copies = available_copies + 1 "
                        "WHERE book_id = ?", (book_id,))
            self.conn.commit()
            return fine, days_late
        except (ValueError, sqlite3.Error):
            self.conn.rollback()
            raise
        finally:
            cur.close()

    def get_transactions(self, status="All", search=""):
        """The SQL is built only from fixed text; user input goes in '?' params."""
        today = date.today().strftime(DATE_FMT)
        sql = ("SELECT t.issue_id, m.reg_no, m.name, b.title, t.issue_date, t.due_date, "
               "t.return_date, t.fine_amount FROM transactions t "
               "JOIN books b ON b.book_id = t.book_id "
               "JOIN members m ON m.member_id = t.member_id "
               "WHERE (m.reg_no LIKE ? OR m.name LIKE ? OR b.title LIKE ?)")
        like = f"%{search}%"
        params = [like, like, like]
        if status == "Active":
            sql += " AND t.return_date IS NULL"
        elif status == "Overdue":
            sql += " AND t.return_date IS NULL AND t.due_date < ?"
            params.append(today)
        elif status == "Returned":
            sql += " AND t.return_date IS NOT NULL"
        sql += " ORDER BY t.issue_id DESC"
        return self._fetch_all(sql, params)

    def get_raw_table(self, table):
        """Return (column names, rows) of a whole table for the live DB viewer."""
        sql = self.RAW_TABLE_QUERIES[table]       # only the 3 fixed queries
        cur = self.conn.cursor()
        try:
            cur.execute(sql)
            columns = [d[0] for d in cur.description]
            return columns, cur.fetchall()
        finally:
            cur.close()

    def summary(self):
        today = date.today().strftime(DATE_FMT)
        one = self._fetch_one
        return {
            "titles": one("SELECT COUNT(*) FROM books")[0],
            "copies": one("SELECT COALESCE(SUM(total_copies), 0) FROM books")[0],
            "issued": one("SELECT COUNT(*) FROM transactions WHERE return_date IS NULL")[0],
            "overdue": one("SELECT COUNT(*) FROM transactions WHERE return_date IS NULL "
                           "AND due_date < ?", (today,))[0],
            "fines": one("SELECT COALESCE(SUM(fine_amount), 0) FROM transactions")[0],
        }

    def close(self):
        """Close the connection when the window is closed."""
        self.conn.close()


# ============================================================================
# Date input widget: calendar DateEntry, or masked text as a fallback
# ============================================================================
class DateField(ttk.Frame):
    """A date input with the same get_date() / set_date() API either way."""

    def __init__(self, parent, width=12):
        super().__init__(parent)
        if HAS_TKCALENDAR:
            self.widget = DateEntry(self, width=width, date_pattern="yyyy-mm-dd",
                                    background="#1f4e79", foreground="white",
                                    borderwidth=1)
            self.widget.pack(fill="x")
        else:
            # Masked entry: only digits and '-' allowed, at most 10 characters.
            vcmd = (self.register(self._allowed), "%P")
            self.var = tk.StringVar(value=date.today().strftime(DATE_FMT))
            self.widget = ttk.Entry(self, width=width, textvariable=self.var,
                                    validate="key", validatecommand=vcmd)
            self.widget.pack(fill="x")

    @staticmethod
    def _allowed(proposed):
        return len(proposed) <= 10 and all(c.isdigit() or c == "-" for c in proposed)

    def get_date(self):
        """Return a datetime.date, raising ValueError for bad input."""
        if HAS_TKCALENDAR:
            return parse_date(self.widget.get())
        return parse_date(self.var.get())

    def set_date(self, value):
        if HAS_TKCALENDAR:
            self.widget.set_date(value)
        else:
            self.var.set(value.strftime(DATE_FMT))

    def bind_change(self, callback):
        """Call callback whenever the user picks / edits a date."""
        if HAS_TKCALENDAR:
            self.widget.bind("<<DateEntrySelected>>", lambda e: callback())
        self.widget.bind("<FocusOut>", lambda e: callback())


# ============================================================================
# Reusable helper: a Treeview with a vertical scrollbar
# ============================================================================
def make_table(parent, columns, widths, height=12):
    frame = ttk.Frame(parent)
    tree = ttk.Treeview(frame, columns=[c for c, _ in columns], show="headings",
                        height=height, selectmode="browse")
    for (key, heading), width in zip(columns, widths):
        tree.heading(key, text=heading)
        anchor = "w" if width >= 140 else "center"
        tree.column(key, width=width, anchor=anchor, stretch=width >= 140)
    vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.pack(side="left", fill="both", expand=True)
    vsb.pack(side="right", fill="y")
    tree.tag_configure("odd", background="#f4f7fb")
    tree.tag_configure("overdue", background="#fbe3e3", foreground="#9b1c1c")
    tree.tag_configure("returned", foreground="#4b5563")
    return frame, tree


# ============================================================================
# Error handling for GUI actions
# ============================================================================
def handle_db_errors(method):
    """Decorator for button handlers: any sqlite3.Error that was not handled
    more specifically (e.g. IntegrityError for duplicates) is shown to the user
    in a messagebox instead of crashing the program."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except sqlite3.Error as err:
            messagebox.showerror("Database error",
                                 f"The database reported an error:\n\n{err}")
    return wrapper


# ============================================================================
# GUI application
# ============================================================================
class LibraryApp:
    def __init__(self, root, db):
        self.root = root
        self.db = db
        root.title("Campus Library - Book Issue & Fine Calculator")
        root.geometry("1100x700")
        root.minsize(980, 620)
        self._style()

        # ---- Header bar
        header = tk.Frame(root, bg="#1f4e79")
        header.pack(fill="x")
        tk.Label(header, text="Campus Library - Book Issue & Fine Calculator",
                 font=("Segoe UI", 16, "bold"), fg="white", bg="#1f4e79"
                 ).pack(side="left", padx=16, pady=10)
        tk.Label(header, text=f"Loan period: {LOAN_DAYS} days   |   "
                              f"Fine: Rs {FINE_PER_DAY:.2f} per day late",
                 font=("Segoe UI", 10), fg="#dbe7f3", bg="#1f4e79"
                 ).pack(side="right", padx=16)

        # ---- Status bar with live summary (packed first so it always stays visible)
        self.status = tk.StringVar()
        ttk.Label(root, textvariable=self.status, style="Status.TLabel",
                  anchor="w").pack(fill="x", side="bottom")

        # ---- Tabs
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(8, 4))
        self.books_tab = ttk.Frame(self.notebook, padding=10)
        self.members_tab = ttk.Frame(self.notebook, padding=10)
        self.issue_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.issue_tab, text="  Issue / Return / Fine  ")
        self.notebook.add(self.books_tab, text="  Books  ")
        self.db_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.members_tab, text="  Members  ")
        self.notebook.add(self.db_tab, text="  Database Tables  ")

        self._build_issue_tab()
        self._build_books_tab()
        self._build_members_tab()
        self._build_db_tab()
        # Event binding: switching tabs reloads every table from the database
        self.notebook.bind("<<NotebookTabChanged>>", lambda e: self.refresh_all())

        self.refresh_all()

    # ------------------------------------------------------------------ style
    def _style(self):
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("Treeview", rowheight=24)
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"),
                        background="#dbe7f3")
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"),
                        foreground="white", background="#1f4e79")
        style.map("Accent.TButton", background=[("active", "#2d6aa3")])
        style.configure("Danger.TButton", foreground="white", background="#b42318")
        style.map("Danger.TButton", background=[("active", "#d92d20")])
        style.configure("Status.TLabel", background="#e5e7eb", padding=(10, 4))
        style.configure("Fine.TLabel", font=("Segoe UI", 12, "bold"), foreground="#1f4e79")
        style.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"),
                        foreground="#1f4e79")

    # ============================================================ BOOKS TAB
    def _build_books_tab(self):
        tab = self.books_tab
        form = ttk.LabelFrame(tab, text="Book Details", padding=10)
        form.pack(fill="x")

        self.b_id = None                       # book_id of the selected row
        self.b_isbn = tk.StringVar()
        self.b_title = tk.StringVar()
        self.b_author = tk.StringVar()
        self.b_copies = tk.StringVar(value="1")

        ttk.Label(form, text="ISBN *").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(form, textvariable=self.b_isbn, width=22).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(form, text="Title *").grid(row=0, column=2, sticky="w", padx=4)
        ttk.Entry(form, textvariable=self.b_title, width=44).grid(row=0, column=3, sticky="we", padx=4)
        ttk.Label(form, text="Author *").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(form, textvariable=self.b_author, width=22).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(form, text="Total copies *").grid(row=1, column=2, sticky="w", padx=4)
        ttk.Spinbox(form, from_=1, to=999, textvariable=self.b_copies, width=8
                    ).grid(row=1, column=3, sticky="w", padx=4)
        form.columnconfigure(3, weight=1)

        buttons = ttk.Frame(form)
        buttons.grid(row=0, column=4, rowspan=2, padx=(16, 0))
        ttk.Button(buttons, text="Add Book", style="Accent.TButton",
                   command=self.add_book).grid(row=0, column=0, padx=3, pady=2, sticky="we")
        ttk.Button(buttons, text="Update", command=self.update_book
                   ).grid(row=0, column=1, padx=3, pady=2, sticky="we")
        ttk.Button(buttons, text="Delete", style="Danger.TButton",
                   command=self.delete_book).grid(row=1, column=0, padx=3, pady=2, sticky="we")
        ttk.Button(buttons, text="Clear", command=self.clear_book_form
                   ).grid(row=1, column=1, padx=3, pady=2, sticky="we")

        self.b_search = tk.StringVar()
        self._search_bar(tab, self.b_search, self.load_books, "Search title / author / ISBN:")

        cols = [("id", "ID"), ("isbn", "ISBN"), ("title", "Title"), ("author", "Author"),
                ("total", "Total"), ("avail", "Available")]
        frame, self.books_tree = make_table(tab, cols, [50, 130, 330, 260, 70, 80])
        frame.pack(fill="both", expand=True)
        self.books_tree.bind("<<TreeviewSelect>>", self.on_book_select)

    def _search_bar(self, parent, var, callback, label):
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(10, 6))
        ttk.Label(bar, text=label).pack(side="left")
        entry = ttk.Entry(bar, textvariable=var, width=36)
        entry.pack(side="left", padx=6)
        entry.bind("<KeyRelease>", lambda e: callback())
        ttk.Button(bar, text="Show All", command=lambda: (var.set(""), callback())
                   ).pack(side="left")
        return bar

    def read_book_form(self):
        """Validate the book form; return clean values or None after an alert."""
        isbn = clean_isbn(self.b_isbn.get())
        title = self.b_title.get().strip()
        author = self.b_author.get().strip()
        if not (isbn and title and author):
            messagebox.showwarning("Missing data", "ISBN, Title and Author are required.")
            return None
        if not is_valid_isbn(isbn):
            messagebox.showerror("Invalid ISBN",
                                 f"'{self.b_isbn.get()}' is not a valid ISBN.\n\n"
                                 "Enter 10 or 13 digits (hyphens allowed) with a "
                                 "correct check digit, e.g. 978-1-71850-270-3.")
            return None
        if not re.match(r"^[A-Za-z .,'&-]{2,80}$", author):
            messagebox.showerror("Invalid author", "Author name may contain only letters, "
                                 "spaces and . , ' & - characters.")
            return None
        try:
            copies = int(self.b_copies.get())
            if not 1 <= copies <= 999:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid copies", "Total copies must be a whole number from 1 to 999.")
            return None
        return isbn, title, author, copies

    @handle_db_errors
    def add_book(self):
        data = self.read_book_form()
        if data is None:
            return
        try:
            self.db.add_book(*data)
        except sqlite3.IntegrityError:
            messagebox.showerror("Duplicate ISBN", f"A book with ISBN {data[0]} already exists.")
            return
        messagebox.showinfo("Book added", f"'{data[1]}' added with {data[3]} copies.")
        self.clear_book_form()
        self.refresh_all()

    @handle_db_errors
    def update_book(self):
        if self.b_id is None:
            messagebox.showwarning("No selection", "Select a book in the table to update.")
            return
        data = self.read_book_form()
        if data is None:
            return
        try:
            self.db.update_book(self.b_id, *data)
        except sqlite3.IntegrityError:
            messagebox.showerror("Duplicate ISBN", f"Another book already uses ISBN {data[0]}.")
            return
        except ValueError as err:
            messagebox.showerror("Cannot update", str(err))
            return
        messagebox.showinfo("Book updated", f"'{data[1]}' was updated.")
        self.clear_book_form()
        self.refresh_all()

    @handle_db_errors
    def delete_book(self):
        if self.b_id is None:
            messagebox.showwarning("No selection", "Select a book in the table to delete.")
            return
        if self.db.active_issues_of_book(self.b_id):
            messagebox.showerror("Cannot delete", "Some copies of this book are still issued. "
                                 "Return them before deleting the book.")
            return
        past = self.db.book_history_count(self.b_id)
        note = f"\n\n{past} past issue record(s) of this book will also be deleted." if past else ""
        if messagebox.askyesno("Confirm delete", f"Delete '{self.b_title.get()}'?{note}"):
            self.db.delete_book(self.b_id)
            self.clear_book_form()
            self.refresh_all()

    def on_book_select(self, _event=None):
        sel = self.books_tree.selection()
        if not sel:
            return
        book_id, isbn, title, author, total, _avail = self.books_tree.item(sel[0], "values")
        self.b_id = int(book_id)
        self.b_isbn.set(isbn)
        self.b_title.set(title)
        self.b_author.set(author)
        self.b_copies.set(total)

    def clear_book_form(self):
        self.b_id = None
        for var in (self.b_isbn, self.b_title, self.b_author):
            var.set("")
        self.b_copies.set("1")
        self.books_tree.selection_remove(self.books_tree.selection())

    def load_books(self):
        tree = self.books_tree
        tree.delete(*tree.get_children())
        for i, row in enumerate(self.db.get_books(self.b_search.get().strip())):
            tree.insert("", "end", values=row, tags=("odd",) if i % 2 else ())

    # ========================================================== MEMBERS TAB
    def _build_members_tab(self):
        tab = self.members_tab
        form = ttk.LabelFrame(tab, text="Member Details", padding=10)
        form.pack(fill="x")

        self.m_id = None
        self.m_reg = tk.StringVar()
        self.m_name = tk.StringVar()
        self.m_max = tk.StringVar(value=str(DEFAULT_MAX_BOOKS))

        ttk.Label(form, text="Register No. *").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(form, textvariable=self.m_reg, width=18).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(form, text="(format: 25PG00004)", foreground="#6b7280"
                  ).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(form, text="Name *").grid(row=0, column=2, sticky="w", padx=(16, 4))
        ttk.Entry(form, textvariable=self.m_name, width=34).grid(row=0, column=3, sticky="w", padx=4)
        ttk.Label(form, text="Max books allowed *").grid(row=0, column=4, sticky="w", padx=(16, 4))
        ttk.Spinbox(form, from_=1, to=10, textvariable=self.m_max, width=6
                    ).grid(row=0, column=5, sticky="w", padx=4)

        buttons = ttk.Frame(form)
        buttons.grid(row=1, column=3, columnspan=3, sticky="e", pady=(6, 0))
        ttk.Button(buttons, text="Add Member", style="Accent.TButton",
                   command=self.add_member).pack(side="left", padx=3)
        ttk.Button(buttons, text="Update", command=self.update_member).pack(side="left", padx=3)
        ttk.Button(buttons, text="Delete", style="Danger.TButton",
                   command=self.delete_member).pack(side="left", padx=3)
        ttk.Button(buttons, text="Clear", command=self.clear_member_form).pack(side="left", padx=3)

        self.m_search = tk.StringVar()
        self._search_bar(tab, self.m_search, self.load_members, "Search register no. / name:")

        cols = [("id", "ID"), ("reg", "Register No."), ("name", "Name"),
                ("max", "Max Allowed"), ("held", "Books Held")]
        frame, self.members_tree = make_table(tab, cols, [60, 150, 360, 120, 120])
        frame.pack(fill="both", expand=True)
        self.members_tree.bind("<<TreeviewSelect>>", self.on_member_select)

    def read_member_form(self):
        reg = self.m_reg.get().strip().upper()
        name = " ".join(self.m_name.get().split())
        if not (reg and name):
            messagebox.showwarning("Missing data", "Register number and name are required.")
            return None
        if not REG_NO_PATTERN.match(reg):
            messagebox.showerror("Invalid register number",
                                 f"'{reg}' is not valid.\n\nUse 2 digits, 2 letters and "
                                 "5 digits, e.g. 25PG00004.")
            return None
        if not NAME_PATTERN.match(name):
            messagebox.showerror("Invalid name", "Name must start with a letter and contain "
                                 "only letters, spaces, dots or apostrophes (2-50 characters).")
            return None
        try:
            max_allowed = int(self.m_max.get())
            if not 1 <= max_allowed <= 10:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid limit", "Max books allowed must be a number from 1 to 10.")
            return None
        return reg, name, max_allowed

    @handle_db_errors
    def add_member(self):
        data = self.read_member_form()
        if data is None:
            return
        try:
            self.db.add_member(*data)
        except sqlite3.IntegrityError:
            messagebox.showerror("Duplicate member", f"Register number {data[0]} already exists.")
            return
        messagebox.showinfo("Member added", f"{data[1]} ({data[0]}) is now a library member.")
        self.clear_member_form()
        self.refresh_all()

    @handle_db_errors
    def update_member(self):
        if self.m_id is None:
            messagebox.showwarning("No selection", "Select a member in the table to update.")
            return
        data = self.read_member_form()
        if data is None:
            return
        held = self.db.active_issues_of_member(self.m_id)
        if data[2] < held:
            messagebox.showerror("Cannot update", f"This member currently holds {held} books, "
                                 f"so the limit cannot be set below {held}.")
            return
        try:
            self.db.update_member(self.m_id, *data)
        except sqlite3.IntegrityError:
            messagebox.showerror("Duplicate member", f"Register number {data[0]} is already used.")
            return
        messagebox.showinfo("Member updated", f"Details of {data[1]} were updated.")
        self.clear_member_form()
        self.refresh_all()

    @handle_db_errors
    def delete_member(self):
        if self.m_id is None:
            messagebox.showwarning("No selection", "Select a member in the table to delete.")
            return
        if self.db.active_issues_of_member(self.m_id):
            messagebox.showerror("Cannot delete", "This member still holds library books. "
                                 "Return them before deleting the member.")
            return
        past = self.db.member_history_count(self.m_id)
        note = f"\n\n{past} past issue record(s) will also be deleted." if past else ""
        if messagebox.askyesno("Confirm delete", f"Delete member {self.m_name.get()}?{note}"):
            self.db.delete_member(self.m_id)
            self.clear_member_form()
            self.refresh_all()

    def on_member_select(self, _event=None):
        sel = self.members_tree.selection()
        if not sel:
            return
        member_id, reg, name, max_allowed, _held = self.members_tree.item(sel[0], "values")
        self.m_id = int(member_id)
        self.m_reg.set(reg)
        self.m_name.set(name)
        self.m_max.set(max_allowed)

    def clear_member_form(self):
        self.m_id = None
        self.m_reg.set("")
        self.m_name.set("")
        self.m_max.set(str(DEFAULT_MAX_BOOKS))
        self.members_tree.selection_remove(self.members_tree.selection())

    def load_members(self):
        tree = self.members_tree
        tree.delete(*tree.get_children())
        for i, row in enumerate(self.db.get_members(self.m_search.get().strip())):
            tree.insert("", "end", values=row, tags=("odd",) if i % 2 else ())

    # ===================================================== ISSUE / RETURN TAB
    def _build_issue_tab(self):
        tab = self.issue_tab
        top = ttk.Frame(tab)
        top.pack(fill="x")

        # -- Left: issue a book
        issue = ttk.LabelFrame(top, text="Issue a Book", padding=10)
        issue.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self.member_combo = ttk.Combobox(issue, state="readonly", width=34)
        self.book_combo = ttk.Combobox(issue, state="readonly", width=34)
        self.issue_date = DateField(issue)
        self.due_date = DateField(issue)
        ttk.Label(issue, text="Member *").grid(row=0, column=0, sticky="w", pady=3)
        self.member_combo.grid(row=0, column=1, columnspan=3, sticky="we", padx=4)
        ttk.Label(issue, text="Book *").grid(row=1, column=0, sticky="w", pady=3)
        self.book_combo.grid(row=1, column=1, columnspan=3, sticky="we", padx=4)
        ttk.Label(issue, text="Issue date").grid(row=2, column=0, sticky="w", pady=3)
        self.issue_date.grid(row=2, column=1, sticky="w", padx=4)
        ttk.Label(issue, text="Due date").grid(row=2, column=2, sticky="e", padx=(10, 4))
        self.due_date.grid(row=2, column=3, sticky="w", padx=4)
        ttk.Button(issue, text="Issue Book", style="Accent.TButton", command=self.issue_book
                   ).grid(row=3, column=1, sticky="w", padx=4, pady=(8, 0))
        ttk.Button(issue, text="Clear", command=self.clear_issue_form
                   ).grid(row=3, column=2, sticky="w", padx=4, pady=(8, 0))
        self.issue_date.bind_change(self.set_default_due)
        self.clear_issue_form()

        # -- Right: return a book / calculate fine
        ret = ttk.LabelFrame(top, text="Return a Book / Calculate Fine", padding=10)
        ret.pack(side="left", fill="both", expand=True, padx=(6, 0))
        self.sel_issue = tk.StringVar(value="Select an issue record in the\ntable below.")
        self.return_date = DateField(ret)
        self.fine_text = tk.StringVar(value="Fine: -")
        ttk.Label(ret, text="Selected:").grid(row=0, column=0, sticky="nw", pady=3)
        ttk.Label(ret, textvariable=self.sel_issue, wraplength=360, justify="left"
                  ).grid(row=0, column=1, columnspan=3, sticky="w", padx=4)
        ttk.Label(ret, text="Return date").grid(row=1, column=0, sticky="w", pady=3)
        self.return_date.grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(ret, textvariable=self.fine_text, style="Fine.TLabel"
                  ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 2))
        ttk.Button(ret, text="Calculate Fine", command=self.show_fine
                   ).grid(row=3, column=1, sticky="w", padx=4, pady=(6, 0))
        ttk.Button(ret, text="Return Book", style="Accent.TButton", command=self.return_book
                   ).grid(row=3, column=2, sticky="w", padx=4, pady=(6, 0))
        self.selected_issue_id = None

        # -- Filter bar
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(10, 6))
        ttk.Label(bar, text="Show:").pack(side="left")
        self.t_filter = tk.StringVar(value="All")
        for option in ("All", "Active", "Overdue", "Returned"):
            ttk.Radiobutton(bar, text=option, value=option, variable=self.t_filter,
                            command=self.load_transactions).pack(side="left", padx=4)
        self.t_search = tk.StringVar()
        ttk.Label(bar, text="   Search:").pack(side="left")
        entry = ttk.Entry(bar, textvariable=self.t_search, width=28)
        entry.pack(side="left", padx=6)
        entry.bind("<KeyRelease>", lambda e: self.load_transactions())

        cols = [("id", "Issue ID"), ("reg", "Reg. No."), ("member", "Member"), ("book", "Book"),
                ("issue", "Issue Date"), ("due", "Due Date"), ("ret", "Returned On"),
                ("fine", "Fine (Rs)"), ("status", "Status")]
        frame, self.trans_tree = make_table(tab, cols, [65, 95, 140, 290, 92, 92, 100, 78, 95])
        frame.pack(fill="both", expand=True)
        self.trans_tree.bind("<<TreeviewSelect>>", self.on_issue_select)

    def set_default_due(self):
        """Due date = issue date + LOAN_DAYS whenever the issue date changes."""
        try:
            self.due_date.set_date(self.issue_date.get_date() + timedelta(days=LOAN_DAYS))
        except ValueError:
            pass

    def clear_issue_form(self):
        self.member_combo.set("")
        self.book_combo.set("")
        self.issue_date.set_date(date.today())
        self.set_default_due()

    def load_combos(self):
        self.members_map = {f"{reg} - {name}": mid
                            for mid, reg, name, _mx, _held in self.db.get_members()}
        self.books_map = {f"{title}  ({avail} available)": bid
                          for bid, title, avail in self.db.available_books()}
        self.member_combo["values"] = list(self.members_map)
        self.book_combo["values"] = list(self.books_map)

    @handle_db_errors
    def issue_book(self):
        member_key, book_key = self.member_combo.get(), self.book_combo.get()
        if not member_key or not book_key:
            messagebox.showwarning("Missing data", "Choose both a member and a book.")
            return
        try:
            issued, due = self.issue_date.get_date(), self.due_date.get_date()
        except ValueError as err:
            messagebox.showerror("Invalid date", str(err))
            return
        if issued > date.today():
            messagebox.showerror("Invalid date", "Issue date cannot be in the future.")
            return
        if due <= issued:
            messagebox.showerror("Invalid date", "Due date must be after the issue date.")
            return
        try:
            issue_id = self.db.issue_book(self.books_map[book_key],
                                          self.members_map[member_key], issued, due)
        except ValueError as err:
            messagebox.showerror("Cannot issue", str(err))
            return
        messagebox.showinfo("Book issued",
                            f"Issue ID {issue_id}\n\nBook: {book_key.split('  (')[0]}\n"
                            f"Member: {member_key}\nDue on: {due.strftime('%d %b %Y')}")
        self.clear_issue_form()
        self.refresh_all()

    def on_issue_select(self, _event=None):
        sel = self.trans_tree.selection()
        if not sel:
            return
        values = self.trans_tree.item(sel[0], "values")
        self.selected_issue_id = int(values[0])
        self.sel_issue.set(f"#{values[0]}  {values[3]}\n{values[2]} ({values[1]}), due {values[5]}")
        if values[6]:      # already returned
            self.fine_text.set(f"Returned on {values[6]} - fine paid Rs {values[7]}")
        else:
            self.return_date.set_date(date.today())
            self.fine_text.set("Fine: click 'Calculate Fine'")

    def _selected_open_issue(self):
        """Return (issue row, return date) for the selected unreturned issue, or None."""
        if self.selected_issue_id is None:
            messagebox.showwarning("No selection", "Select an issue record in the table first.")
            return None
        row = self.db.get_issue(self.selected_issue_id)
        if row is None:
            messagebox.showerror("Not found", "That issue record no longer exists.")
            return None
        if row[5] is not None:
            messagebox.showinfo("Already returned",
                                f"'{row[1]}' was returned on {row[5]} with a fine of Rs {row[6]:.2f}.")
            return None
        try:
            ret_date = self.return_date.get_date()
        except ValueError as err:
            messagebox.showerror("Invalid date", str(err))
            return None
        if ret_date < parse_date(row[3]):
            messagebox.showerror("Invalid date", "Return date cannot be before the issue date.")
            return None
        return row, ret_date

    @handle_db_errors
    def show_fine(self):
        """Calculate Fine button: shows the fine without saving anything."""
        picked = self._selected_open_issue()
        if picked is None:
            return
        row, ret_date = picked
        fine, days_late = calculate_fine(parse_date(row[4]), ret_date)
        self.fine_text.set(f"Fine: Rs {fine:.2f}  ({days_late} day(s) late)")
        if days_late:
            messagebox.showwarning("Fine due",
                                   f"Book: {row[1]}\nMember: {row[2]}\n\nDue date: {row[4]}\n"
                                   f"Return date: {ret_date}\nDays late: {days_late}\n"
                                   f"Rate: Rs {FINE_PER_DAY:.2f} per day\n\n"
                                   f"Fine payable: Rs {fine:.2f}")
        else:
            messagebox.showinfo("No fine", f"'{row[1]}' is returned on time. No fine is due.")

    @handle_db_errors
    def return_book(self):
        picked = self._selected_open_issue()
        if picked is None:
            return
        row, ret_date = picked
        fine, days_late = calculate_fine(parse_date(row[4]), ret_date)
        if not messagebox.askyesno("Confirm return",
                                   f"Return '{row[1]}' from {row[2]} on {ret_date}?\n\n"
                                   f"Fine to collect: Rs {fine:.2f} ({days_late} day(s) late)"):
            return
        try:
            self.db.return_book(row[0], ret_date)
        except ValueError as err:
            messagebox.showerror("Cannot return", str(err))
            return
        messagebox.showinfo("Book returned", f"'{row[1]}' returned. Fine collected: Rs {fine:.2f}")
        self.selected_issue_id = None
        self.sel_issue.set("Select an issue record in the\ntable below.")
        self.fine_text.set("Fine: -")
        self.refresh_all()

    def load_transactions(self):
        tree = self.trans_tree
        tree.delete(*tree.get_children())
        today = date.today()
        rows = self.db.get_transactions(self.t_filter.get(), self.t_search.get().strip())
        for i, (iid, reg, name, title, issued, due, returned, fine) in enumerate(rows):
            if returned:
                status, tags = "Returned", ("returned",)
            elif parse_date(due) < today:
                days = (today - parse_date(due)).days
                status, tags = f"Overdue {days}d", ("overdue",)
                fine = calculate_fine(parse_date(due), today)[0]    # fine so far
            else:
                status, tags = "Issued", ("odd",) if i % 2 else ()
            tree.insert("", "end", values=(iid, reg, name, title, issued, due,
                                           returned or "", f"{fine:.2f}", status), tags=tags)

    # ================================================== DATABASE TABLES TAB
    def _build_db_tab(self):
        """Shows the raw rows stored in library.db, so every insert, update,
        delete, issue and return can be seen reflected in the tables live."""
        tab = self.db_tab
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Table:").pack(side="left")
        self.raw_table = tk.StringVar(value="transactions")
        combo = ttk.Combobox(bar, textvariable=self.raw_table, state="readonly", width=16,
                             values=list(LibraryDB.RAW_TABLE_QUERIES))
        combo.pack(side="left", padx=6)
        combo.bind("<<ComboboxSelected>>", lambda e: self.load_raw_table())
        ttk.Button(bar, text="Refresh", command=self.load_raw_table).pack(side="left")
        self.raw_info = tk.StringVar()
        ttk.Label(bar, textvariable=self.raw_info, foreground="#6b7280"
                  ).pack(side="left", padx=14)
        ttk.Label(tab, foreground="#6b7280",
                  text="Rows exactly as stored in library.db (SELECT * FROM table). "
                       "This view reloads after every add, update, delete, issue and return."
                  ).pack(anchor="w", pady=(0, 6))

        frame = ttk.Frame(tab)
        frame.pack(fill="both", expand=True)
        self.raw_tree = ttk.Treeview(frame, show="headings", height=14)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.raw_tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self.raw_tree.xview)
        self.raw_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.raw_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.raw_tree.tag_configure("odd", background="#f4f7fb")

    @handle_db_errors
    def load_raw_table(self):
        columns, rows = self.db.get_raw_table(self.raw_table.get())
        tree = self.raw_tree
        tree.delete(*tree.get_children())
        tree["columns"] = columns
        for col in columns:
            wide = col in ("title", "author", "name")
            tree.heading(col, text=col)
            tree.column(col, width=230 if wide else 115, anchor="w" if wide else "center",
                        stretch=wide)
        for i, row in enumerate(rows):
            shown = ["NULL" if value is None else value for value in row]
            tree.insert("", "end", values=shown, tags=("odd",) if i % 2 else ())
        self.raw_info.set(f"{len(rows)} rows   |   last refreshed at "
                          f"{datetime.now().strftime('%H:%M:%S')}")

    # ------------------------------------------------------------- refresh
    @handle_db_errors
    def refresh_all(self):
        """Reload every table and the status bar from the database."""
        self.load_books()
        self.load_members()
        self.load_transactions()
        self.load_combos()
        self.load_raw_table()
        s = self.db.summary()
        self.status.set(f"Titles: {s['titles']}   |   Copies: {s['copies']}   |   "
                        f"Currently issued: {s['issued']}   |   Overdue: {s['overdue']}   |   "
                        f"Fines collected: Rs {s['fines']:.2f}   |   Database: {DB_FILE.name}")


# ============================================================================
# Program entry point
# ============================================================================
def main():
    root = tk.Tk()
    try:
        db = LibraryDB()                          # connect / create library.db
    except sqlite3.Error as err:
        messagebox.showerror("Database error", f"Could not open {DB_FILE.name}:\n\n{err}")
        root.destroy()
        return
    LibraryApp(root, db)

    def on_close():
        db.close()                  # close the database connection cleanly
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
