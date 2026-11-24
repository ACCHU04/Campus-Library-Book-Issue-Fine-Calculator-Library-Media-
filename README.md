# Campus Library Book Issue & Fine Calculator (Library & Media)

**MCA III Semester - Python Programming Lab Mini Project (2026-27)**  
**Chanakya University, School of Engineering, Department of Computer Applications**

* **Student:** Acchutha K S  
* **Reg. No.:** 25PG00004  
* **Domain:** Library & Media  

---

## 📖 Overview

A standalone desktop application built with Python Tkinter (GUI) and SQLite (database via the built-in `sqlite3` module). The application assists campus librarians in tracking book inventories, student/member records, book issuance, returns, and automatic fine calculations for overdue items.

---

## ✨ Features

- **Books Management (CRUD):** Add, view, update, and delete book records with ISBN, title, author, total copies, and available stock.
- **Members Management (CRUD):** Manage student/faculty members, register numbers, and borrowing quotas.
- **Book Issue & Return Transactions:**
  - Issue books with dynamic loan period calculation.
  - Return books with automatic stock restoration and late fine calculation.
  - Atomic database transactions with `commit()` and `rollback()` handling.
- **Fine Calculator:** Automatically computes late fines based on overdue days.
- **Live Search & Filter:** Filter transactions by All, Active, Overdue, and Returned statuses. Overdue records are highlighted in red.
- **Database Tables Viewer:** Real-time inspect view of SQLite tables (`books`, `members`, `transactions`) within the GUI.
- **Input Validation & Security:**
  - Parameterized SQL queries (`?` placeholders) protecting against SQL injection.
  - Regular expression validations for ISBN, register numbers, names, and dates.

---

## 🛠️ Tech Stack & Requirements

- **Language:** Python 3.x
- **GUI Framework:** Tkinter / `tkinter.ttk` (Standard Library)
- **Database:** SQLite3 (`sqlite3` module - Standard Library)
- **Optional Dependencies:**
  - `tkcalendar` (Optional, for calendar date picker widget; falls back to standard masked YYYY-MM-DD input if not installed)

---

## 🚀 How to Run

1. **Clone the repository:**
   ```bash
   git clone https://github.com/ACCHU04/Campus-Library-Book-Issue-Fine-Calculator-Library-Media-.git
   cd Campus-Library-Book-Issue-Fine-Calculator-Library-Media-
   ```

2. **(Optional) Install tkcalendar:**
   ```bash
   pip install tkcalendar
   ```

3. **Run the application:**
   ```bash
   python library_manager.py
   ```

---

## 🗄️ Database Schema (`library.db`)

- **`books`**: `book_id` (PK), `isbn`, `title`, `author`, `total_copies`, `available_copies`
- **`members`**: `member_id` (PK), `reg_no`, `name`, `max_allowed`
- **`transactions`**: `issue_id` (PK), `book_id` (FK), `member_id` (FK), `issue_date`, `due_date`, `return_date`, `fine_amount`

---

## 📄 Project Report

The project screenshot verification report is included in [Screenshot_Report_Acchutha_KS.pdf](Screenshot_Report_Acchutha_KS.pdf).
