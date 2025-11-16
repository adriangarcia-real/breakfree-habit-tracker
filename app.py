import os
from cs50 import SQL
from flask import Flask, flash, redirect, render_template, request, session, jsonify
from flask_session import Session
from werkzeug.security import check_password_hash, generate_password_hash
import secrets
from functools import wraps
from datetime import datetime, date, timedelta
from collections import defaultdict
# import requests # Used for external API, now commented out for mocking

# --- Configuration ---

# Configure application
app = Flask(__name__)

# Use a cryptographically secure key to sign session cookies
app.secret_key = secrets.token_hex(16) 

# Configure session to store data in the filesystem (on the server)
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# Configure CS50 Library to use the local SQLite database
db = SQL("sqlite:///tracker.db")


# --- Helper Functions ---

def login_required(f):
    """Decorator to require user login before accessing a route."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check if user_id exists in session (meaning the user is logged in)
        if not session.get("user_id"):
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated_function


def calculate_streak(entries):
    """Calculates the user's current and longest consecutive day streak."""
    # If no entries exist, return 0 for both streaks
    if not entries:
        return 0, 0

    # 1. Map entries to daily status (True = Success, False = Failure)
    # defaultdict sets the default status to True (success) for any date
    day_status = defaultdict(lambda: True)
    all_entry_dates = set()

    for e in entries:
        # Strip timestamp, convert string to date object
        date_obj = datetime.strptime(e['date'].split()[0], "%Y-%m-%d").date() 
        all_entry_dates.add(date_obj)

        # If entry success is 0 (Failure), override status to False
        if int(e['success']) == 0:
            day_status[date_obj] = False

    sorted_days = sorted(all_entry_dates)
    if not sorted_days:
        return 0, 0

    # 2. Longest Streak Calculation: Iterate through all recorded days
    longest_streak = 0
    current_longest = 0
    prev_day = None

    for day in sorted_days:
        if day_status[day]: # If the day was a success
            # If the current day is exactly one day after the previous day
            if prev_day and (day - prev_day).days == 1:
                current_longest += 1
            else:
                current_longest = 1 # Start new streak
            
            longest_streak = max(longest_streak, current_longest)
        else:
            current_longest = 0 # Failure breaks the streak

        prev_day = day

    # 3. Current Streak Calculation: Check backwards from today
    current_streak = 0
    day_to_check = date.today()

    # Loop for up to a year (more than enough)
    for _ in range(365): 
        if day_to_check in day_status:
            if day_status[day_to_check]:
                current_streak += 1 # Success, continue the streak
            else:
                current_streak = 0 # Failure breaks the current streak
                break
        else:
            # If the day has no entry, it's considered a skip and breaks the streak
            # Only break if the skipped day is in the past
            if day_to_check < date.today():
                break
            # If today has no entry, don't count it, but don't break either (wait for log)
            if day_to_check == date.today():
                break

        # Stop checking if we hit the very first recorded entry
        if sorted_days and day_to_check <= sorted_days[0]:
            break

        day_to_check -= timedelta(days=1) # Move to the previous day

    return current_streak, longest_streak


def initialize_chat_history():
    """Sets up the initial chat context in the session."""
    if "chat_messages" not in session:
        # Hardcoded System Message for the mock AI persona
        session["chat_messages"] = [{"role": "system", "content": "You are BreakFree Buddy, a supportive, non-judgemental, and recovery-focused AI companion. Your goal is to help users manage urges, reflect on their habits, and stay motivated on their self-improvement journey. Adopt a positive, encouraging tone, keeping responses brief and actionable."}]
        session.modified = True 


# --- Public Routes (Unprotected) ---

@app.route("/")
def index():
    # If the user is logged in, redirect them straight to the dashboard
    if session.get("user_id"):
        return redirect("/dashboard")
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    # Handle user submission
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        confirmation = request.form.get("confirmation")

        # Validation checks
        if not username:
            flash("Please enter a username")
            return redirect("/register")
        if not password:
            flash("Please enter a password")
            return redirect("/register")
        if password != confirmation:
            flash("Passwords don't match")
            return redirect("/register")

        # Check if username already exists
        rows = db.execute("SELECT * FROM users WHERE username = ?", username)
        if rows:
            flash("Username already taken")
            return redirect("/register")

        # Store new user in the database with a hashed password
        hash_pw = generate_password_hash(password)
        db.execute("INSERT INTO users (username, hash) VALUES (?, ?)", username, hash_pw)

        flash("Registered successfully! Please log in.")
        return redirect("/login")

    # Display registration form
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    # Handle user submission
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        # Validation checks
        if not username:
            flash("Please enter a username")
            return redirect("/login")
        if not password:
            flash("Please enter a password")
            return redirect("/login")

        # Find user and check password hash
        rows = db.execute("SELECT * FROM users WHERE username = ?", username)
        if len(rows) != 1 or not check_password_hash(rows[0]["hash"], password):
            flash("Invalid username and/or password")
            return redirect("/login")

        # Log user in by setting session ID
        session["user_id"] = rows[0]["id"]
        return redirect("/dashboard")

    # Display login form
    return render_template("login.html")


@app.route("/logout")
def logout():
    # Clear the session data
    session.clear()
    return redirect("/login")


# --- Protected Routes (Login Required) ---

@app.route("/dashboard")
@login_required
def dashboard():
    today = date.today().strftime("%Y-%m-%d")
    user_id = session["user_id"]
    # Fetch all habits for the logged-in user
    habits = db.execute("SELECT * FROM habits WHERE user_id = ?", user_id)

    for habit in habits:
        # Fetch all entries for the current habit
        entries = db.execute(
            "SELECT * FROM entries WHERE habit_id = ? ORDER BY date DESC", habit["id"]
        )

        # Clean up date and success fields for template usage
        for e in entries:
            e_date = e['date'].split()[0]
            e['date'] = e_date
            e['success'] = int(e['success'])

        habit["entries"] = entries
        # Calculate streaks using the helper function
        habit["current_streak"], habit["longest_streak"] = calculate_streak(entries)

        habit["recent_entry"] = entries[0] if entries else None

        # Calculate mood frequency for statistics
        moods = ["happy", "sad", "neutral", "anxious", "calm"]
        habit["mood_counts"] = {m: sum(1 for e in entries if e["mood"] == m) for m in moods}

    return render_template("dashboard.html", habits=habits, today=today)


@app.route("/add_habit", methods=["GET", "POST"])
@login_required
def add_habit():
    # Handle form submission to add a new habit
    if request.method == "POST":
        habit_name = request.form.get("habit_name").strip()
        
        # Validation
        if not habit_name or len(habit_name) > 50:
            flash("Please enter a habit name (max 50 chars).")
            return render_template("add_habit.html")

        # Check for existing habit with the same name for this user
        existing = db.execute(
            "SELECT * FROM habits WHERE user_id = ? AND habit_name = ?",
            session["user_id"], habit_name
        )
        if existing:
            flash(f"You already have a habit named '{habit_name}'.")
            return render_template("add_habit.html")

        # Insert new habit into the database
        db.execute(
            "INSERT INTO habits (user_id, habit_name, start_date) VALUES (?, ?, ?)",
            session["user_id"], habit_name, datetime.today().strftime("%Y-%m-%d")
        )
        flash(f"Habit '{habit_name}' added successfully!")
        return redirect("/dashboard")

    # Display add habit form
    return render_template("add_habit.html")


@app.route("/add_entry/<int:habit_id>", methods=["GET", "POST"])
@login_required
def add_entry(habit_id):
    # Verify the habit belongs to the user
    habit = db.execute("SELECT * FROM habits WHERE id = ? AND user_id = ?",
                         habit_id, session["user_id"])
    if not habit:
        flash("Habit not found.")
        return redirect("/dashboard")

    # Handle form submission to log an entry
    if request.method == "POST":
        success = request.form.get("success")
        mood = request.form.get("mood")
        journal = request.form.get("journal")

        # Basic form validation
        if not success or not mood or not journal:
            flash("Please fill in the form.")
            return render_template("add_entry.html", habit_id=habit_id, habit_name=habit[0]["habit_name"])

        # Check if an entry for today already exists
        today_date = datetime.today().strftime("%Y-%m-%d")
        existing_entry = db.execute(
            "SELECT * FROM entries WHERE habit_id = ? AND date = ?",
            habit_id, today_date
        )

        if existing_entry:
            flash("You have already logged an entry for today.", "warning")
            return redirect(f"/dashboard")

        # Insert the new entry
        db.execute(
            "INSERT INTO entries (habit_id, date, success, mood, journal) VALUES (?, ?, ?, ?, ?)",
            habit_id, datetime.today().strftime("%Y-%m-%d"), int(success), mood, journal
        )
        flash("Entry added successfully!")
        return redirect("/dashboard")

    # Display add entry form
    return render_template("add_entry.html", habit_id=habit_id, habit_name=habit[0]["habit_name"])


@app.route("/habit_history/<int:habit_id>")
@login_required
def habit_history(habit_id):
    user_id = session["user_id"]

    # Verify the habit belongs to the user
    habit = db.execute("SELECT * FROM habits WHERE id = ? AND user_id = ?", habit_id, user_id)
    if not habit:
        flash("Habit not found.")
        return redirect("/dashboard")

    # Get optional year and month filters from URL
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)

    # Build the query for entries, allowing filtering by year/month
    query = "SELECT * FROM entries WHERE habit_id = ?"
    params = [habit_id]

    if year and month:
        query += " AND strftime('%Y', date) = ? AND strftime('%m', date) = ?"
        params += [str(year), f"{month:02d}"] # Format month to '01', '02', etc.

    query += " ORDER BY date DESC"
    entries = db.execute(query, *params)

    # Fetch available years/months for the filter dropdowns
    entry_years = db.execute(
        "SELECT DISTINCT strftime('%Y', date) AS y FROM entries WHERE habit_id = ? ORDER BY y DESC", habit_id)
    years = [int(e['y']) for e in entry_years if int(e['y']) >= 2025] # Filter out placeholder/test years

    entry_months = db.execute(
        "SELECT DISTINCT strftime('%m', date) AS m FROM entries WHERE habit_id = ? ORDER BY m ASC", habit_id)
    months = [int(e['m']) for e in entry_months]

    MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    return render_template(
        "habit_history.html",
        habit=habit[0],
        entries=entries,
        year=year,
        month=month,
        years=years,
        months=months,
        month_names=MONTH_NAMES
    )


@app.route("/delete_habit/<int:habit_id>", methods=["POST"])
@login_required
def delete_habit(habit_id):
    # Verify the habit belongs to the user
    habit = db.execute("SELECT * FROM habits WHERE id = ? AND user_id = ?",
                         habit_id, session["user_id"])
    if not habit:
        flash("Habit not found or unauthorized.")
        return redirect("/dashboard")

    # Delete all associated entries, then delete the habit itself (CASCADE manually)
    db.execute("DELETE FROM entries WHERE habit_id = ?", habit_id)
    db.execute("DELETE FROM habits WHERE id = ?", habit_id)
    flash(f'Habit "{habit[0]["habit_name"]}" deleted successfully!')
    return redirect("/dashboard")


@app.route("/delete_entry/<int:entry_id>", methods=["POST"])
@login_required
def delete_entry(entry_id):
    # Get the entry and verify the associated habit belongs to the user
    entry = db.execute("SELECT * FROM entries WHERE id = ?", entry_id)
    if not entry:
        flash("Entry not found.")
        return redirect("/dashboard")

    habit_id = entry[0]["habit_id"]
    
    habit = db.execute("SELECT * FROM habits WHERE id = ? AND user_id = ?",
                         habit_id, session["user_id"])
    if not habit:
        flash("Unauthorized action.")
        return redirect("/dashboard")

    # Delete the specific entry
    db.execute("DELETE FROM entries WHERE id = ?", entry_id)
    flash("Entry deleted successfully.")
    return redirect(f"/habit_history/{habit_id}")


@app.route("/edit_entry/<int:entry_id>", methods=["GET", "POST"])
@login_required
def edit_entry(entry_id):
    # Fetch entry details along with the habit name, ensuring user ownership
    entry_rows = db.execute(
        "SELECT e.id, e.habit_id, e.date, e.success, e.mood, e.journal, h.habit_name "
        "FROM entries e JOIN habits h ON e.habit_id = h.id "
        "WHERE e.id = ? AND h.user_id = ?",
        entry_id, session["user_id"]
    )

    if not entry_rows:
        flash("Entry not found or unauthorized.", "danger")
        return redirect("/dashboard")

    entry = entry_rows[0]
    entry['date'] = entry['date'].split()[0] # Clean date for display

    # Handle form submission to update the entry
    if request.method == "POST":
        success = request.form.get("success")
        mood = request.form.get("mood")
        journal = request.form.get("journal")

        # Validation
        if not success or not mood or not journal:
            flash("Please fill in the form.", "warning")
            return render_template("edit_entry.html", entry=entry)

        # Update the entry fields in the database
        db.execute(
            "UPDATE entries SET success = ?, mood = ?, journal = ? WHERE id = ?",
            int(success), mood, journal, entry_id
        )
        flash("Entry updated successfully!", "success")
        return redirect(f"/habit_history/{entry['habit_id']}")

    # Display edit form
    return render_template("edit_entry.html", entry=entry)


# --- AI Chat Routes (Mocked API) ---

@app.route("/ai_chat")
@login_required
def ai_chat():
    # Ensure chat history is initialized when the chat page is opened
    initialize_chat_history()
    return render_template("ai_chat.html")


@app.route("/get_ai_response", methods=["POST"])
@login_required
def get_ai_response():
    """Mocks the AI response based on user input for supportive interaction."""
    
    # 1. RETRIEVE USERNAME for personalized greeting
    user_id = session["user_id"]
    user_row = db.execute("SELECT username FROM users WHERE id = ?", user_id)
    username = user_row[0]["username"] if user_row else "Pioneer"
    
    initialize_chat_history()
    data = request.get_json()
    user_message = data.get('message', '').lower()
    
    # Set a supportive, general fallback response
    ai_reply = f"Thanks for sharing, {username}. Sometimes it helps just to write things down. I'm here to listen, or we can look for a quick action you can take right now."

    # 2. Mock Logic: Respond based on user's keywords (Intent Detection)
    
    # Struggle/Urge/Temptation Response
    if any(keyword in user_message for keyword in ["struggle", "tempted", "urge", "crave", "hard", "relapse"]):
        ai_reply = f"Hold on a minute, {username}. I know this is a tough moment, but you're stronger than this urge. Take three deep, slow breaths. What is one tiny thing you can do *right now* to distract yourself or change your environment?"

    # Positive/Success/Goal Response
    elif any(keyword in user_message for keyword in ["good", "success", "win", "happy", "achieve", "goal", "focused"]):
        ai_reply = f"That is fantastic news, {username}! Seriously, that takes real effort. Take a moment to appreciate this win, no matter how small. What made today a success?"

    # Seeking Advice/Question Response
    elif any(keyword in user_message for keyword in ["how to", "should i", "what if", "advice", "plan"]):
        ai_reply = f"That’s a great question to ask, {username}. Let's break it down. What steps have you tried already, and what feels like the biggest roadblock right now?"

    # General Greeting/Check-in Response
    elif any(keyword in user_message for keyword in ["hi", "hello", "check in", "listen", "today"]):
        ai_reply = f"Hey {username}, thanks for checking in. I'm ready to listen. What's on your mind today?"
        
    # 3. Update session history with the user's message and the mock reply
    session["chat_messages"].append({"role": "user", "content": data.get('message', '')})
    session["chat_messages"].append({"role": "assistant", "content": ai_reply})
    session.modified = True 
    
    # Send the reply back to the front-end as JSON
    return jsonify({"reply": ai_reply})

@app.route("/reset_chat", methods=["POST"])
@login_required
def reset_chat():
    """Clears the chat history from the session."""
    if "chat_messages" in session:
        session.pop("chat_messages")
        initialize_chat_history() # Re-initialize with the system message
        flash("Chat history reset. Buddy is ready!", "info")
    return jsonify({"success": True})





if __name__ == '__main__':
    # Start the app with debug mode enabled
    app.run(debug=True)