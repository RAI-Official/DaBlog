from flask import Flask, render_template, request, redirect, session, Response, jsonify
import sqlite3
import hashlib
from datetime import datetime, timezone, timedelta
import html
import re
import json 
import time 

app = Flask(__name__)
app.secret_key = "super_secret_key"  # fine for friends-only

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, "database.db")


def get_db():
    return sqlite3.connect(DB)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def is_admin_user(user_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT username FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    db.close()
    return row and row[0] == "Raulnistel"

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = hash_password(request.form["password"])

        db = get_db()
        cur = db.cursor()
        cur.execute(
            "SELECT id FROM users WHERE username=? AND password=? AND is_deleted=0",
            (username, password)
        )

        user = cur.fetchone()
        db.close()

        if user:
            session["user_id"] = user[0]
            session["username"] = username
            return redirect("/feed")
        else:
            # Pass the error message back to the login.html template
            return render_template("login.html", error="Invalid username or password")

    return render_template("login.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form["username"]
        password = hash_password(request.form["password"])

        db = get_db()
        cur = db.cursor()

        cur.execute("SELECT 1 FROM users WHERE username=?", (username,))
        if cur.fetchone():
            db.close()
            return render_template(
                "signup.html",
                error="Username already exists"
            )

        cur.execute(
            "INSERT INTO users (username, password) VALUES (?, ?)",
            (username, password)
        )

        db.commit()
        db.close()
        return redirect("/")

    return render_template("signup.html")

@app.route("/api/send_message", methods=["POST"])
def send_message():
    if "user_id" not in session:
        return {"error": "unauthorized"}, 401
    
    receiver_id = request.form.get("receiver_id")
    content = request.form.get("content")
    
    if not receiver_id or not content:
        return {"error": "missing data"}, 400

    db = get_db()
    cur = db.cursor()
    cur.execute(
        "INSERT INTO messages (sender_id, receiver_id, content) VALUES (?, ?, ?)",
        (session["user_id"], receiver_id, content)
    )
    db.commit()
    db.close()
    return {"success": True}

@app.route("/chat")
@app.route("/chat/<username>") # Add this to handle /chat/dummyuser
def chat_page(username=None):
    if "user_id" not in session:
        return redirect("/")
    
    # We pass the username from the URL to the template
    return render_template("chat.html", 
                           current_username=session.get("username"),
                           target_username=username)

@app.route("/api/dm_list")
def get_dm_list():
    if "user_id" not in session:
        return {"error": "unauthorized"}, 401
    
    db = get_db()
    cur = db.cursor()
    
    # This query finds everyone the user has exchanged messages with
    # and gets the very last message content for the preview
    cur.execute("""
        SELECT u.id, u.username, 
               (SELECT content FROM messages 
                WHERE (sender_id = u.id AND receiver_id = ?) 
                   OR (sender_id = ? AND receiver_id = u.id)
                ORDER BY id DESC LIMIT 1) as last_msg
        FROM users u
        WHERE u.id IN (
            SELECT DISTINCT sender_id FROM messages WHERE receiver_id = ?
            UNION
            SELECT DISTINCT receiver_id FROM messages WHERE sender_id = ?
        ) AND u.id != ?
    """, (session["user_id"], session["user_id"], session["user_id"], session["user_id"], session["user_id"]))
    
    users = [{"id": row[0], "username": row[1], "last_msg": row[2]} for row in cur.fetchall()]
    db.close()
    return {"users": users}

@app.route("/api/search_users")
def search_users():
    if "user_id" not in session:
        return {"error": "unauthorized"}, 401
    
    query = request.args.get("q", "").strip()
    if not query:
        return {"users": []}

    db = get_db()
    cur = db.cursor()
    # Fuzzy search using LIKE with wildcards
    cur.execute(
        "SELECT id, username FROM users WHERE username LIKE ? AND is_deleted = 0 AND id != ? LIMIT 10",
        (f"%{query}%", session["user_id"])
    )
    users = [{"id": row[0], "username": row[1]} for row in cur.fetchall()]
    db.close()
    return {"users": users}

@app.route("/api/get_messages/<int:other_id>")
def get_messages(other_id):
    if "user_id" not in session:
        return {"error": "unauthorized"}, 401
    
    user_id = session["user_id"]
    db = get_db()
    cur = db.cursor()
    
    # Fetch conversation between the two users
    cur.execute("""
        SELECT sender_id, content, id 
        FROM messages 
        WHERE (sender_id = ? AND receiver_id = ?) 
           OR (sender_id = ? AND receiver_id = ?)
        ORDER BY id ASC
    """, (user_id, other_id, other_id, user_id))
    
    rows = cur.fetchall()
    db.close()
    
    messages = [{"sender_id": r[0], "content": r[1]} for r in rows]
    return jsonify({"messages": messages})

@app.route("/api/user_by_name/<username>")
def user_by_name(username):
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id, username FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    db.close()
    if row:
        return {"id": row[0], "username": row[1]}
    return {"error": "not found"}, 404

@app.route("/api/user_by_id/<int:user_id>")
def user_by_id(user_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT id, username FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    db.close()
    if row:
        return {"id": row[0], "username": row[1]}
    return {"error": "not found"}, 404

@app.route("/api/stream_messages")
def stream_messages():
    if "user_id" not in session:
        return "Unauthorized", 401
    
    user_id = session["user_id"]

    def event_stream():
        last_id = 0
        # Initialize last_id with the current highest message ID
        db = get_db()
        cur = db.cursor()
        cur.execute("SELECT MAX(id) FROM messages WHERE receiver_id = ?", (user_id,))
        row = cur.fetchone()
        if row and row[0]:
            last_id = row[0]
        db.close()

        while True:
            time.sleep(2) # Check every 2 seconds
            db = get_db()
            cur = db.cursor()
            # Look for messages newer than the last one seen
            cur.execute("""
                SELECT m.id, m.content, u.username 
                FROM messages m 
                JOIN users u ON m.sender_id = u.id 
                WHERE m.receiver_id = ? AND m.id > ? 
                ORDER BY m.id ASC
            """, (user_id, last_id))
            
            new_messages = cur.fetchall()
            db.close()

            for msg_id, content, sender in new_messages:
                last_id = msg_id
                # Only yield when there is an actual message
                yield f"data: {json.dumps({'sender': sender})}\n\n"

    return Response(event_stream(), mimetype="text/event-stream")

@app.route("/post", methods=["POST"])
def create_post():
    if "user_id" not in session:
        return {"error": "unauthorized"}, 401
    
    db = get_db()
    cur = db.cursor()
    
    cur.execute("SELECT is_muted FROM users WHERE id=?", (session["user_id"],))
    if cur.fetchone()[0]:
        db.close()
        return {"error": "muted"}, 403
    
    post_type = request.form.get("type", "text")
    
    if post_type == "poll":
        question = request.form.get("question", "").strip()
        options = request.form.getlist("options[]")
        options = [o.strip() for o in options if o.strip()]
      
        if not question:
            return {"error": "Poll question required"}, 400
      
        if len(options) < 2 or len(options) > 5:
            return {"error": "Poll must have 2–5 options"}, 400
      
        # 1️⃣ create post
        cur.execute(
            "INSERT INTO posts (user_id, content, created_at, is_public, type) VALUES (?, '', ?, 1, 'poll')",
            (session["user_id"], datetime.now(timezone.utc))
        )
        post_id = cur.lastrowid
      
        # 2️⃣ create poll
        cur.execute(
            "INSERT INTO polls (post_id, question) VALUES (?, ?)",
            (post_id, question)
        )
      
        # 3️⃣ create options
        for opt in options:
            cur.execute(
                "INSERT INTO poll_options (post_id, option_text) VALUES (?, ?)",
                (post_id, opt)
            )
      
        db.commit()
        db.close()
        return {
            "success": True,
            "post_count": get_post_count(session["user_id"])
        }




    # TEXT POST
    content = request.form["content"]

    cur.execute(
        "INSERT INTO posts (user_id, content, created_at, is_public, type) VALUES (?, ?, ?, 1, 'text')",
        (session["user_id"], content, datetime.now(timezone.utc))
    )
    post_id = cur.lastrowid
    db.commit()

    cur.execute("""
        SELECT posts.id, users.username, posts.created_at
        FROM posts
        JOIN users ON posts.user_id = users.id
        WHERE posts.id = ?
    """, (post_id,))
    row = cur.fetchone()
    db.close()

    IST = timezone(timedelta(hours=5, minutes=30))
    pretty_time = datetime.fromisoformat(row[2]).astimezone(IST).strftime("%d/%m/%Y - %I:%M %p").lower()

    return {
        "id": post_id,
        "type": "text",
        "username": row[1],
        "content": render_post(content),
        "time": pretty_time,
        "like_count": 0,
        "is_admin": is_admin_user(session["user_id"]),
        "post_count": get_post_count(session["user_id"])
    }

def censor_text(text):
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT word FROM curse_words")
    words = [w[0] for w in cur.fetchall()]
    db.close()

    for w in words:
        def repl(m):
            first = m.group(0)[0]
            return first + "*" * (len(m.group(0)) - 1)

        text = re.sub(rf'\b{re.escape(w)}\b', repl, text, flags=re.IGNORECASE)

    return text

@app.route("/vote/<int:option_id>", methods=["POST"])
def vote(option_id):
    if "user_id" not in session:
        return {"error": "unauthorized"}, 401

    user_id = session["user_id"]
    db = get_db()
    cur = db.cursor()

    # find poll
    cur.execute("SELECT post_id FROM poll_options WHERE id=?", (option_id,))
    row = cur.fetchone()
    if not row:
        db.close()
        return {"error": "invalid option"}, 400

    post_id = row[0]

    # did user already vote THIS option?
    cur.execute(
        "SELECT 1 FROM poll_votes WHERE user_id=? AND option_id=?",
        (user_id, option_id)
    )
    already_voted = cur.fetchone()

    if already_voted:
        # 🔁 toggle OFF
        cur.execute(
            "DELETE FROM poll_votes WHERE user_id=? AND option_id=?",
            (user_id, option_id)
        )
        action = "removed"
    else:
        # ❌ remove previous vote in THIS poll
        cur.execute("""
            DELETE FROM poll_votes
            WHERE user_id=?
            AND option_id IN (
                SELECT id FROM poll_options WHERE post_id=?
            )
        """, (user_id, post_id))

        # ✅ add new vote
        cur.execute(
            "INSERT INTO poll_votes (user_id, option_id) VALUES (?, ?)",
            (user_id, option_id)
        )
        action = "voted"

    # 🔄 re-fetch updated results
    cur.execute("""
        SELECT
            po.id,
            COUNT(pv.user_id) AS votes,
            MAX(pv.user_id = ?) AS voted_by_me
        FROM poll_options po
        LEFT JOIN poll_votes pv ON pv.option_id = po.id
        WHERE po.post_id=?
        GROUP BY po.id
    """, (user_id, post_id))

    options = [
        {
            "id": o[0],
            "votes": o[1],
            "voted_by_me": bool(o[2])
        }
        for o in cur.fetchall()
    ]

    db.commit()
    db.close()

    return {
        "action": action,
        "options": options
    }

@app.route("/like/<int:post_id>", methods=["POST"])
def like(post_id):
    if "user_id" not in session:
        return {"error": "unauthorized"}, 401

    db = get_db()
    cur = db.cursor()

    cur.execute(
        "SELECT 1 FROM likes WHERE user_id=? AND post_id=?",
        (session["user_id"], post_id)
    )
    liked = cur.fetchone()

    if liked:
        cur.execute(
            "DELETE FROM likes WHERE user_id=? AND post_id=?",
            (session["user_id"], post_id)
        )
        action = "unliked"
    else:
        cur.execute(
            "INSERT INTO likes (user_id, post_id) VALUES (?, ?)",
            (session["user_id"], post_id)
        )
        action = "liked"

    cur.execute(
        "SELECT COUNT(*) FROM likes WHERE post_id=?",
        (post_id,)
    )
    like_count = cur.fetchone()[0]

    db.commit()
    db.close()

    return {
        "action": action,
        "like_count": like_count
    }

def render_post(text):
    text = html.escape(text)
    text = censor_text(text)
    
    replacements = [
        (r'\[b\](.*?)\[/b\]', r'<strong>\1</strong>'),
        (r'\[i\](.*?)\[/i\]', r'<em>\1</em>'),
        (r'\[u\](.*?)\[/u\]', r'<u>\1</u>'),
        (r'\[s\](.*?)\[/s\]', r'<s>\1</s>'),
        (r'\[size=medium\](.*?)\[/size\]', r'<span class="text-medium">\1</span>'),
        (r'\[size=large\](.*?)\[/size\]', r'<span class="text-large">\1</span>')
    ]

    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text, flags=re.DOTALL)
    
    # Auto-link URLs
    url_pattern = r'(https?://[^\s<]+|www\.[^\s<]+)'
    
    def linkify(match):
        url = match.group(0)
        href = url if url.startswith("http") else "https://" + url
        return f'<a href="{href}" target="_blank" rel="noopener noreferrer">{url}</a>'

    text = re.sub(url_pattern, linkify, text)
    
    return text

@app.route("/feed")
def feed():
    if "user_id" not in session:
        return redirect("/")

    db = get_db()
    cur = db.cursor()

    # cur.execute("SELECT username FROM users WHERE id=?", (session["user_id"],))
    # current_username = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM posts WHERE user_id=?", (session["user_id"],))
    post_count = cur.fetchone()[0]

    cur.execute("""
        SELECT
            posts.id,
            posts.user_id,
            posts.content,
            posts.type,
            CASE
                WHEN users.is_deleted = 1
                THEN 'Deleted User [' || users.id || ']'
                ELSE users.username
            END AS username,
            users.is_deleted,
            posts.created_at,
            posts.is_public,
            COUNT(likes.post_id),
            MAX(likes.user_id = ?) AS liked
        FROM posts
        LEFT JOIN users ON posts.user_id = users.id
        LEFT JOIN likes ON posts.id = likes.post_id
        WHERE posts.is_public = 1 OR posts.user_id = ?
        GROUP BY posts.id
        ORDER BY posts.created_at DESC

    """, (session["user_id"], session["user_id"]))

    rows = cur.fetchall()
    posts = []

    IST = timezone(timedelta(hours=5, minutes=30))

    for r in rows:
        (
            post_id, user_id, content, ptype,
            username, is_deleted,
            created_at, is_public,
            likes, liked
        ) = r
        pretty_time = datetime.fromisoformat(created_at).astimezone(IST).strftime("%d/%m/%Y - %I:%M %p").lower()

        post = {
            "id": post_id,
            "user_id": user_id,
            "type": ptype,
            "username": username,
            "time": pretty_time,
            "is_public": is_public,
            "like_count": likes,
            "liked_by_me": liked,
            "is_admin": is_admin_user(user_id)
        }
        post["is_deleted_user"] = bool(is_deleted)
        if ptype == "poll":
            cur.execute("SELECT question FROM polls WHERE post_id=?", (post_id,))
            post["question"] = cur.fetchone()[0]
        
            cur.execute("""
                SELECT
                    po.id,
                    po.option_text,
                    COUNT(pv.user_id) AS votes,
                    MAX(pv.user_id = ?) AS voted_by_me
                FROM poll_options po
                LEFT JOIN poll_votes pv ON pv.option_id = po.id
                WHERE po.post_id=?
                GROUP BY po.id
            """, (session["user_id"], post_id))
        
            options = []
            total_votes = 0
        
            rows2 = cur.fetchall()
            for o in rows2:
                total_votes += o[2]
        
            for o in rows2:
                percent = (o[2] / total_votes * 100) if total_votes else 0
                options.append({
                    "id": o[0],
                    "text": o[1],
                    "votes": o[2],
                    "percent": round(percent, 1),
                    "voted_by_me": bool(o[3])
                })
        
            post["options"] = options

        else:
            post["content"] = render_post(content)

        posts.append(post)

    db.close()

    return render_template(
        "feed.html",
        posts=posts,
        current_user=session["user_id"],
        current_username=session["username"],
        post_count=post_count,
        is_admin=is_admin_user(session["user_id"])
    )

@app.route("/edit/<int:post_id>", methods=["GET", "POST"])
def edit_post(post_id):
    if "user_id" not in session:
        return redirect("/")

    db = get_db()
    cur = db.cursor()

    if is_admin_user(session["user_id"]):
        cur.execute(
            "SELECT id, content, is_public FROM posts WHERE id=?",
            (post_id,)
        )
    else:
        cur.execute(
            "SELECT id, content, is_public FROM posts WHERE id=? AND user_id=?",
            (post_id, session["user_id"])
        )

    row = cur.fetchone()

    if not row:
        db.close()
        return redirect("/feed")

    post = {
        "id": row[0],
        "content": row[1],
        "is_public": bool(row[2])
    }

    if request.method == "POST":
        new_content = request.form["content"]
        is_public = int(request.form["is_public"])  # 👈 FIXED NAME

        cur.execute(
            "UPDATE posts SET content=?, is_public=? WHERE id=?",
            (new_content, is_public, post_id)
        )
        db.commit()
        db.close()
        return redirect("/feed")

    db.close()
    return render_template("edit.html", post=post)

@app.route("/delete/<int:post_id>", methods=["POST"])
def delete_post(post_id):
    if "user_id" not in session:
        return {"error": "unauthorized"}, 401

    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT user_id, type FROM posts WHERE id=?", (post_id,))
    post = cur.fetchone()

    if not post:
        return {"error": "not found"}, 404

    post_user_id, post_type = post

    if not is_admin_user(session["user_id"]) and post_user_id != session["user_id"]:
        return {"error": "forbidden"}, 403

    # delete likes
    cur.execute("DELETE FROM likes WHERE post_id=?", (post_id,))

    if post_type == "poll":
        # delete votes → options → poll
        cur.execute("""
            DELETE FROM poll_votes
            WHERE option_id IN (
                SELECT id FROM poll_options WHERE post_id=?
            )
        """, (post_id,))

        cur.execute("DELETE FROM poll_options WHERE post_id=?", (post_id,))
        cur.execute("DELETE FROM polls WHERE post_id=?", (post_id,))

    # finally delete post
    cur.execute("DELETE FROM posts WHERE id=?", (post_id,))

    db.commit()
    db.close()

    return {
        "success": True,
        "post_count": get_post_count(session["user_id"])
    }

@app.route("/api/user/<username>")
def get_user_profile(username):
    db = get_db()
    cur = db.cursor()

    cur.execute(
        "SELECT id, username, is_muted FROM users WHERE username=? AND is_deleted=0",
        (username,)
    )

    user = cur.fetchone()
    if not user:
        return {"error": "not found"}, 404

    user_id = user[0]

    cur.execute("SELECT COUNT(*) FROM posts WHERE user_id=?", (user_id,))
    post_count = cur.fetchone()[0]

    cur.execute("""
        SELECT
            p.id,
            p.content,
            p.created_at,
            p.type,
            COUNT(l.post_id) AS likes
        FROM posts p
        LEFT JOIN likes l ON l.post_id = p.id
        WHERE p.user_id=?
        GROUP BY p.id
        ORDER BY p.created_at DESC
    """, (user_id,))

    rows = cur.fetchall()
    IST = timezone(timedelta(hours=5, minutes=30))
    posts = []

    for r in rows:
        post_id, content, created_at, ptype, likes = r
        time = datetime.fromisoformat(created_at)\
            .astimezone(IST)\
            .strftime("%d/%m/%Y - %I:%M %p").lower()

        post = {
            "id": post_id,
            "type": ptype,
            "time": time,
            "like_count": likes
        }

        if ptype == "poll":
            cur.execute("SELECT question FROM polls WHERE post_id=?", (post_id,))
            post["question"] = cur.fetchone()[0]

            cur.execute("""
                SELECT option_text, COUNT(pv.user_id)
                FROM poll_options po
                LEFT JOIN poll_votes pv ON pv.option_id = po.id
                WHERE po.post_id=?
                GROUP BY po.id
            """, (post_id,))
            post["options"] = [
                {"text": o[0], "votes": o[1]}
                for o in cur.fetchall()
            ]
        else:
            post["content"] = render_post(content)

        posts.append(post)

    db.close()

    return {
        "user": {
            "id": user_id,
            "username": user[1],
            "post_count": post_count,
            "is_me": session.get("user_id") == user_id,
            "is_muted": bool(user[2])
        },
        "posts": posts
    }

@app.route("/admin/mute/<int:user_id>", methods=["POST"])
def toggle_mute(user_id):
    if "user_id" not in session or not is_admin_user(session["user_id"]):
        return {"error": "forbidden"}, 403

    db = get_db()
    cur = db.cursor()

    cur.execute("SELECT is_muted FROM users WHERE id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        db.close()
        return {"error": "not found"}, 404

    new_state = 0 if row[0] else 1
    cur.execute(
        "UPDATE users SET is_muted=? WHERE id=?",
        (new_state, user_id)
    )

    db.commit()
    db.close()

    return {"muted": bool(new_state)}

@app.route("/admin/panel")
def admin_panel():
    if "user_id" not in session or not is_admin_user(session["user_id"]):
        return {"error": "forbidden"}, 403

    db = get_db()
    cur = db.cursor()

    cur.execute("""
        SELECT id, username, is_muted
        FROM users
        WHERE is_deleted=0
        ORDER BY username
    """)
    users = [
        {"id": u[0], "username": u[1], "is_muted": bool(u[2])}
        for u in cur.fetchall()
    ]

    cur.execute("""
        SELECT
            p.id,
            p.content,
            p.created_at,
            p.type,
            u.username,
            COUNT(l.post_id) AS likes
        FROM posts p
        JOIN users u ON p.user_id = u.id
        LEFT JOIN likes l ON l.post_id = p.id
        WHERE p.is_public = 0
        GROUP BY p.id
        ORDER BY p.created_at DESC
    """)
    IST = timezone(timedelta(hours=5, minutes=30))
    private_posts = []
    
    for r in cur.fetchall():
        post_id, content, created_at, ptype, username, likes = r
        time = datetime.fromisoformat(created_at)\
            .astimezone(IST)\
            .strftime("%d/%m/%Y - %I:%M %p").lower()
    
        post = {
            "id": post_id,
            "type": ptype,
            "username": username,
            "time": time,
            "like_count": likes,
            "readonly": True
        }
    
        if ptype == "poll":
            cur.execute("SELECT question FROM polls WHERE post_id=?", (post_id,))
            post["question"] = cur.fetchone()[0]
    
            cur.execute("""
                SELECT option_text, COUNT(pv.user_id)
                FROM poll_options po
                LEFT JOIN poll_votes pv ON pv.option_id = po.id
                WHERE po.post_id=?
                GROUP BY po.id
            """, (post_id,))
    
            post["options"] = [
                {"text": o[0], "votes": o[1]}
                for o in cur.fetchall()
            ]
        else:
            post["content"] = render_post(content)
    
        private_posts.append(post)

  #  private_posts = cur.fetchall()

    db.close()

    return {
        "users": users,
        "private_posts": private_posts
    }

@app.route("/admin/curse", methods=["POST"])
def add_curse():
    if "user_id" not in session or not is_admin_user(session["user_id"]):
        return {"error": "forbidden"}, 403

    word = request.get_json().get("word", "").lower().strip()
    if not word:
        return {"error": "empty"}, 400

    db = get_db()
    cur = db.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO curse_words(word) VALUES (?)",
        (word,)
    )
    db.commit()
    db.close()

    return {"success": True}

@app.route("/account/username", methods=["POST"])
def change_username():
    if "user_id" not in session:
        return {"error": "unauthorized"}, 401

    data = request.get_json()
    new_username = data.get("username", "").strip()
    password = data.get("password", "")

    if len(new_username) < 3:
        return {"error": "username too short"}, 400

    db = get_db()
    cur = db.cursor()

    # verify password first
    cur.execute(
        "SELECT password FROM users WHERE id=?",
        (session["user_id"],)
    )
    row = cur.fetchone()

    if not row or row[0] != hash_password(password):
        db.close()
        return {"error": "wrong password"}, 403

    # prevent duplicates
    cur.execute(
        "SELECT 1 FROM users WHERE username=?",
        (new_username,)
    )
    if cur.fetchone():
        db.close()
        return {"error": "username already taken"}, 400

    cur.execute(
        "UPDATE users SET username=? WHERE id=?",
        (new_username, session["user_id"])
    )

    db.commit()
    db.close()

    session["username"] = new_username
    return {"success": True}

@app.route("/account/password", methods=["POST"])
def change_password():
    if "user_id" not in session:
        return {"error": "unauthorized"}, 401

    data = request.get_json()
    old = data.get("old_password", "")
    new = data.get("new_password", "")

    if len(new) < 6:
        return {"error": "password too short"}, 400

    db = get_db()
    cur = db.cursor()

    # fetch stored hash
    cur.execute(
        "SELECT password FROM users WHERE id=?",
        (session["user_id"],)
    )
    row = cur.fetchone()

    if not row:
        db.close()
        return {"error": "user not found"}, 404

    # 🔐 hash OLD password before compare
    if row[0] != hash_password(old):
        db.close()
        return {"error": "wrong password"}, 403

    # 🔐 hash NEW password before saving
    cur.execute(
        "UPDATE users SET password=? WHERE id=?",
        (hash_password(new), session["user_id"])
    )

    db.commit()
    db.close()
    return {"success": True}

@app.route("/account/delete", methods=["POST"])
def delete_account():
    if "user_id" not in session:
        return {"error": "unauthorized"}, 401

    user_id = session["user_id"]
    db = get_db()
    cur = db.cursor()

    # preserve username before deletion
    cur.execute(
        """
        UPDATE users
         SET is_deleted = 1,
             deleted_username = username,
             username = NULL
         WHERE id = ?
         """,
        (user_id,)
    )

    db.commit()
    db.close()

    session.clear()
    return {"success": True}

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

def get_post_count(user_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT COUNT(*) FROM posts WHERE user_id=?", (user_id,))
    count = cur.fetchone()[0]
    db.close()
    return count

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0')
