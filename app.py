from flask import Flask, render_template, request, redirect, session, flash
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mail import Mail, Message
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = "supersecretkey"

# ================= EMAIL CONFIG =================
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'praveenreddie31@gmail.com'  # <-- CHANGE THIS
app.config['MAIL_PASSWORD'] = 'osetjedgugvckxrb'  # <-- CHANGE THIS
app.config['MAIL_DEFAULT_SENDER'] = app.config['MAIL_USERNAME']

mail = Mail(app)

# ================= DATABASE =================
def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

def create_tables():
    conn = get_db()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        email TEXT UNIQUE,
        password TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS tasks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        task TEXT,
        status TEXT,
        due_datetime TEXT,
        priority TEXT,
        created_mail_sent INTEGER DEFAULT 0,
        one_hour_mail_sent INTEGER DEFAULT 0,
        five_min_mail_sent INTEGER DEFAULT 0
    )
    """)

    conn.commit()
    conn.close()

create_tables()

# ================= EMAIL FUNCTION =================
import os

def send_email(user_email, subject, body):
    # Skip email sending in Render production
    if os.environ.get("RENDER") == "true":
        print("Skipping email in production (SMTP blocked).")
        return

    try:
        msg = Message(subject, recipients=[user_email])
        msg.body = body
        mail.send(msg)
        print("Email sent:", subject)
    except Exception as e:
        print("Email failed but app continues:", e)

# ================= REMINDER CHECKER =================
def check_reminders():
    conn = get_db()
    tasks = conn.execute("SELECT * FROM tasks WHERE status='Pending'").fetchall()
    now = datetime.now()

    for task in tasks:
        due_time = datetime.strptime(task["due_datetime"], "%Y-%m-%d %H:%M")
        time_diff = due_time - now

        user = conn.execute(
            "SELECT * FROM users WHERE id=?",
            (task["user_id"],)
        ).fetchone()

        # ---- 1 HOUR REMINDER ----
        if timedelta(minutes=5) < time_diff <= timedelta(hours=1):
            if task["one_hour_mail_sent"] == 0:
                send_email(
                    user["email"],
                    "Reminder: 1 Hour Left",
                    f"Hello {user['username']},\n\n"
                    f"Your task '{task['task']}' is due at {task['due_datetime']}.\n"
                    f"Only 1 hour remaining."
                )
                conn.execute(
                    "UPDATE tasks SET one_hour_mail_sent=1 WHERE id=?",
                    (task["id"],)
                )
                conn.commit()

        # ---- 5 MINUTE REMINDER ----
        if timedelta(seconds=0) < time_diff <= timedelta(minutes=5):
            if task["five_min_mail_sent"] == 0:
                send_email(
                    user["email"],
                    "Reminder: 5 Minutes Left",
                    f"Hello {user['username']},\n\n"
                    f"Your task '{task['task']}' is due at {task['due_datetime']}.\n"
                    f"Only 5 minutes remaining."
                )
                conn.execute(
                    "UPDATE tasks SET five_min_mail_sent=1 WHERE id=?",
                    (task["id"],)
                )
                conn.commit()

    conn.close()

import os

if os.environ.get("RENDER") is None:
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=check_reminders, trigger="interval", minutes=1)
    scheduler.start()

# ================= ROUTES =================

@app.route("/")
def home():
    return redirect("/login")

# REGISTER
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        email = request.form["email"]
        password = generate_password_hash(request.form["password"])

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO users (username,email,password) VALUES (?,?,?)",
                (username, email, password)
            )
            conn.commit()
            flash("Registered successfully!", "success")
            return redirect("/login")
        except:
            flash("Username or Email already exists!", "danger")
        finally:
            conn.close()

    return render_template("register.html")

# LOGIN
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username=?",
            (username,)
        ).fetchone()
        conn.close()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect("/dashboard")
        else:
            flash("Invalid credentials!", "danger")

    return render_template("login.html")

# DASHBOARD
@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect("/login")

    conn = get_db()
    tasks = conn.execute(
        "SELECT * FROM tasks WHERE user_id=?",
        (session["user_id"],)
    ).fetchall()
    conn.close()

    return render_template("dashboard.html", tasks=tasks)

# ADD TASK
@app.route("/add_task", methods=["POST"])
def add_task():
    task = request.form["task"]
    due_date = request.form["due_date"]
    due_time = request.form["due_time"]
    priority = request.form["priority"]

    due_datetime = f"{due_date} {due_time}"

    conn = get_db()
    cursor = conn.execute("""
        INSERT INTO tasks (user_id,task,status,due_datetime,priority)
        VALUES (?,?,?,?,?)
    """, (session["user_id"], task, "Pending", due_datetime, priority))

    task_id = cursor.lastrowid

    user = conn.execute(
        "SELECT * FROM users WHERE id=?",
        (session["user_id"],)
    ).fetchone()
    if user is None:
        flash("Session expired. Please login again.", "danger")
        conn.close()
        session.clear()
        return redirect("/login")

    # ---- IMMEDIATE CREATION EMAIL ----
    send_email(
        user["email"],
        "Task Created Successfully",
        f"Hello {user['username']},\n\n"
        f"Your task '{task}' has been created.\n"
        f"Due at: {due_datetime}"
    )

    conn.execute(
        "UPDATE tasks SET created_mail_sent=1 WHERE id=?",
        (task_id,)
    )

    conn.commit()
    conn.close()

    flash("Task added!", "success")
    return redirect("/dashboard")

# COMPLETE
@app.route("/complete/<int:id>")
def complete(id):
    conn = get_db()
    conn.execute("UPDATE tasks SET status='Completed' WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect("/dashboard")

# DELETE
@app.route("/delete/<int:id>")
def delete(id):
    conn = get_db()
    conn.execute("DELETE FROM tasks WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect("/dashboard")

# LOGOUT
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

if __name__ == "__main__":
    app.run()