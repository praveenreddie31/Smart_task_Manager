from flask import Flask, render_template, request, redirect, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mail import Mail, Message
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import psycopg
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "supersecretkey")

# ================= DATABASE CONFIG =================
DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db():
    return psycopg.connect(DATABASE_URL)

def create_tables():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE,
        email TEXT UNIQUE,
        password TEXT
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tasks(
        id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id),
        task TEXT,
        status TEXT,
        due_datetime TEXT,
        priority TEXT,
        created_mail_sent INTEGER DEFAULT 0,
        one_hour_mail_sent INTEGER DEFAULT 0,
        five_min_mail_sent INTEGER DEFAULT 0
    );
    """)

    conn.commit()
    cur.close()
    conn.close()

create_tables()

# ================= EMAIL CONFIG =================
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get("MAIL_USERNAME")
app.config['MAIL_PASSWORD'] = os.environ.get("MAIL_PASSWORD")
app.config['MAIL_DEFAULT_SENDER'] = app.config['MAIL_USERNAME']

mail = Mail(app)

def send_email(user_email, subject, body):
    try:
        # Skip email in Render free tier if needed
        if os.environ.get("RENDER"):
            print("Production environment detected. Skipping SMTP.")
            return

        msg = Message(subject, recipients=[user_email])
        msg.body = body
        mail.send(msg)
        print("Email sent successfully")

    except Exception as e:
        print("Email error but app continues:", e)

# ================= REMINDER SYSTEM =================
def check_reminders():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM tasks WHERE status='Pending'")
    tasks = cur.fetchall()

    now = datetime.now()

    for task in tasks:
        task_id = task[0]
        user_id = task[1]
        task_name = task[2]
        due_datetime = task[4]
        one_hour_sent = task[6]
        five_min_sent = task[7]

        due_time = datetime.strptime(due_datetime, "%Y-%m-%d %H:%M")
        time_diff = due_time - now

        cur.execute("SELECT email, username FROM users WHERE id=%s", (user_id,))
        user = cur.fetchone()

        if not user:
            continue

        email = user[0]
        username = user[1]

        # 1 Hour Reminder
        if timedelta(minutes=5) < time_diff <= timedelta(hours=1):
            if one_hour_sent == 0:
                send_email(
                    email,
                    "1 Hour Reminder",
                    f"Hello {username}, your task '{task_name}' is due at {due_datetime}"
                )
                cur.execute(
                    "UPDATE tasks SET one_hour_mail_sent=1 WHERE id=%s",
                    (task_id,)
                )
                conn.commit()

        # 5 Minute Reminder
        if timedelta(seconds=0) < time_diff <= timedelta(minutes=5):
            if five_min_sent == 0:
                send_email(
                    email,
                    "5 Minute Reminder",
                    f"Hello {username}, your task '{task_name}' is due at {due_datetime}"
                )
                cur.execute(
                    "UPDATE tasks SET five_min_mail_sent=1 WHERE id=%s",
                    (task_id,)
                )
                conn.commit()

    cur.close()
    conn.close()

if not os.environ.get("RENDER"):
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=check_reminders, trigger="interval", minutes=1)
    scheduler.start()

# ================= ROUTES =================

@app.route("/")
def home():
    return redirect("/login")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        email = request.form["email"]
        password = generate_password_hash(request.form["password"])

        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO users (username,email,password) VALUES (%s,%s,%s)",
                (username, email, password)
            )
            conn.commit()
            flash("Registered successfully!", "success")
            return redirect("/login")
        except:
            flash("Username or Email already exists!", "danger")
        finally:
            cur.close()
            conn.close()

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if user and check_password_hash(user[3], password):
            session["user_id"] = user[0]
            session["username"] = user[1]
            return redirect("/dashboard")
        else:
            flash("Invalid credentials!", "danger")

    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect("/login")

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM tasks WHERE user_id=%s ORDER BY id DESC",
        (session["user_id"],)
    )
    tasks = cur.fetchall()
    cur.close()
    conn.close()

    return render_template("dashboard.html", tasks=tasks)

@app.route("/add_task", methods=["POST"])
def add_task():
    if "user_id" not in session:
        return redirect("/login")

    task = request.form["task"]
    due_date = request.form["due_date"]
    due_time = request.form["due_time"]
    priority = request.form["priority"]

    due_datetime = f"{due_date} {due_time}"

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO tasks (user_id,task,status,due_datetime,priority)
        VALUES (%s,%s,%s,%s,%s)
    """, (session["user_id"], task, "Pending", due_datetime, priority))

    conn.commit()

    # Send immediate email
    cur.execute("SELECT email, username FROM users WHERE id=%s",
                (session["user_id"],))
    user = cur.fetchone()

    if user:
        send_email(
            user[0],
            "Task Created",
            f"Hello {user[1]}, your task '{task}' is created. Due at {due_datetime}"
        )

    cur.close()
    conn.close()

    flash("Task added!", "success")
    return redirect("/dashboard")

@app.route("/complete/<int:id>")
def complete(id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE tasks SET status='Completed' WHERE id=%s", (id,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect("/dashboard")

@app.route("/delete/<int:id>")
def delete(id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM tasks WHERE id=%s", (id,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect("/dashboard")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

if __name__ == "__main__":
    app.run()