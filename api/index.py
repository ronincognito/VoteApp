import os
import json
import time
import csv
import logging
from datetime import datetime
from io import StringIO
from threading import Lock
from dotenv import load_dotenv

import numpy as np
from flask import Flask, render_template, request, jsonify, Response, g
import psycopg2
import psycopg2.extras

load_dotenv(dotenv_path='.env.local')

# --- Database Helpers ---
# Expect the PostgreSQL connection URL from an environment variable.
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db():
    # Cache the connection on Flask's g object.
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return db

def init_db():
    db = get_db()
    with db:
        with db.cursor() as cur:
            # Create config table
            cur.execute('''
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            # Create users table (user_id -> user_index)
            cur.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    user_index INTEGER
                )
            ''')
            # Create current_votes table
            cur.execute('''
                CREATE TABLE IF NOT EXISTS current_votes (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT,
                    vote_value REAL
                )
            ''')
            # Create rounds table to store outcomes (votes and user indices stored as JSON)
            cur.execute('''
                CREATE TABLE IF NOT EXISTS rounds (
                    id SERIAL PRIMARY KEY,
                    timestamp TEXT,
                    avg REAL,
                    sdev REAL,
                    median REAL,
                    votes TEXT,
                    users TEXT
                )
            ''')
            # Initialize config values if they do not exist
            for key, default in [('voting_open', '0'), ('check_repeated_votes', '1')]:
                cur.execute('SELECT value FROM config WHERE key = %s', (key,))
                if cur.fetchone() is None:
                    cur.execute('INSERT INTO config (key, value) VALUES (%s, %s)', (key, default))
    db.commit()

def close_db(error=None):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# --- End Database Helpers ---

app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.teardown_appcontext(close_db)

# Initialize logging filter as before.
class NoVoteCountFilter(logging.Filter):
    def filter(self, record):
        return '/vote_count' not in record.getMessage()

log = logging.getLogger('werkzeug')
log.addFilter(NoVoteCountFilter())

# A lock for admin actions (if needed, e.g. concurrent writes)
admin_lock = Lock()

# Ensure database tables exist at startup.
with app.app_context():
    init_db()

# Helper functions to get and update config values
def get_config(key):
    db = get_db()
    with db.cursor() as cur:
        cur.execute('SELECT value FROM config WHERE key = %s', (key,))
        row = cur.fetchone()
    return row['value'] if row else None

def set_config(key, value):
    db = get_db()
    with db.cursor() as cur:
        cur.execute('''
            INSERT INTO config (key, value)
            VALUES (%s, %s)
            ON CONFLICT (key) 
            DO UPDATE SET value = EXCLUDED.value
        ''', (key, value))
    db.commit()

# --- Routes ---
@app.route('/')
def home():
    voting_open = get_config('voting_open') == '1'
    return render_template('vote.html', voting_open=voting_open)

@app.route('/admin')
def admin_page():
    return render_template('admin.html')

@app.route('/open_vote', methods=['POST'])
def open_vote():
    with admin_lock:
        db = get_db()
        with db.cursor() as cur:
            cur.execute('DELETE FROM current_votes')
        db.commit()
        set_config('voting_open', '1')
    return jsonify({'status': 'Er kan nu gestemd worden'})

@app.route('/close_vote', methods=['POST'])
def close_vote():
    db = get_db()
    with admin_lock:
        set_config('voting_open', '0')
        with db.cursor() as cur:
            cur.execute('SELECT vote_value FROM current_votes')
            rows = cur.fetchall()
        votes_list = [row['vote_value'] for row in rows]
        if votes_list:
            avg_vote = float(round(np.mean(votes_list), 2))
            stddev_vote = float(round(np.std(votes_list), 2))
            median_vote = float(round(np.median(votes_list), 2))
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # Get user IDs from current_votes
            with db.cursor() as cur:
                cur.execute('SELECT user_id FROM current_votes')
                users_in_votes = [row['user_id'] for row in cur.fetchall()]
            # For each user_id, get the user_index.
            user_indices = []
            with db.cursor() as cur:
                for user in users_in_votes:
                    cur.execute('SELECT user_index FROM users WHERE user_id = %s', (user,))
                    result = cur.fetchone()
                    user_indices.append(result['user_index'])
            with db.cursor() as cur:
                cur.execute('''
                    INSERT INTO rounds (timestamp, avg, sdev, median, votes, users)
                    VALUES (%s, %s, %s, %s, %s, %s)
                ''', (current_time, avg_vote, stddev_vote, median_vote,
                      json.dumps(votes_list), json.dumps(user_indices)))
            db.commit()
        else:
            avg_vote = stddev_vote = None

        # Clear current_votes after closing the round.
        with db.cursor() as cur:
            cur.execute('DELETE FROM current_votes')
        db.commit()

    return jsonify({
        'status': 'Stemronde is gesloten',
        'avg': avg_vote,
        'sdev': stddev_vote,
        'median': median_vote,
        'votes': votes_list
    })

@app.route('/vote', methods=['POST'])
def vote():
    if get_config('voting_open') != '1':
        return jsonify({'error': 'Stemronde is gesloten'}), 403

    user_id = request.form.get('user_id')
    vote_value = request.form.get('vote_value')
    try:
        vote_value = float(vote_value)
    except (ValueError, TypeError):
        return jsonify({'error': 'Ongeldige stemwaarde'}), 400

    print(f"Received vote from user: {user_id}")
    print(f"vote value: {vote_value}")

    db = get_db()
    check_repeated_votes = get_config('check_repeated_votes') == '1'
    with db.cursor() as cur:
        if check_repeated_votes:
            cur.execute('SELECT 1 FROM current_votes WHERE user_id = %s', (user_id,))
            if cur.fetchone():
                print(f"Duplicate vote detected for user: {user_id}")
                return jsonify({'error': '\nJe hebt al gestemd'}), 403

    with admin_lock:
        with db.cursor() as cur:
            cur.execute('SELECT user_index FROM users WHERE user_id = %s', (user_id,))
            row = cur.fetchone()
            if row is None:
                cur.execute('SELECT COUNT(*) as cnt FROM users')
                new_index = cur.fetchone()['cnt']
                cur.execute('INSERT INTO users (user_id, user_index) VALUES (%s, %s)', (user_id, new_index))
                user_index = new_index
            else:
                user_index = row['user_index']
            cur.execute('INSERT INTO current_votes (user_id, vote_value) VALUES (%s, %s)', (user_id, vote_value))
        db.commit()

    print("Vote stored in database.")
    return jsonify({'status': '\nJe stem is opgeslagen'})

@app.route('/vote_count', methods=['GET'])
def vote_count():
    db = get_db()
    with db.cursor() as cur:
        cur.execute('SELECT COUNT(*) as cnt FROM current_votes')
        count = cur.fetchone()['cnt']
    return jsonify({'count': count})

@app.route('/stored_vote_count', methods=['GET'])
def stored_vote_count():
    db = get_db()
    with db.cursor() as cur:
        cur.execute('SELECT COUNT(*) as cnt FROM rounds')
        vote_count_val = cur.fetchone()['cnt']
        cur.execute('SELECT * FROM rounds ORDER BY id DESC LIMIT 10')
        rounds_data = cur.fetchall()
    last_10_votes = []
    for row in rounds_data:
        last_10_votes.append({
            'timestamp': row['timestamp'],
            'avg': row['avg'],
            'sdev': row['sdev'],
            'median': row['median'],
            'votes': json.loads(row['votes']),
            'users': json.loads(row['users'])
        })
    return jsonify({
        'count': vote_count_val,
        'last_10_votes': last_10_votes
    })

@app.route('/voting_status', methods=['GET'])
def voting_status():
    status = get_config('voting_open') == '1'
    return jsonify({'voting_open': status})

@app.route('/events')
def events():
    def event_stream():
        last_state = None
        with app.app_context():  # Ensure that we are in the application context
            while True:
                current_state = get_config('voting_open') == '1'
                if current_state != last_state:
                    last_state = current_state
                    yield f"data: {current_state}\n\n"
                time.sleep(1)
    return Response(event_stream(), mimetype="text/event-stream")

@app.route('/download_csv')
def download_csv():
    db = get_db()
    with db.cursor() as cur:
        cur.execute('SELECT * FROM rounds ORDER BY id')
        rounds_data = cur.fetchall()

    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(['Time', 'Average', 'Standard Deviation', 'Votes', 'Users'])
    for row in rounds_data:
        writer.writerow([
            row['timestamp'],
            row['avg'],
            row['sdev'],
            row['median'],
            json.loads(row['votes']),
            json.loads(row['users'])
        ])
    output = si.getvalue()
    si.close()

    return Response(output, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment;filename=stored_votes.csv"})

@app.route('/clear_votes', methods=['POST'])
def clear_votes():
    db = get_db()
    with db.cursor() as cur:
        cur.execute('DELETE FROM rounds')
    db.commit()
    return jsonify({'status': 'Stemrondes gewist'})

@app.route('/get_vote_check_status', methods=['GET'])
def get_vote_check_status():
    status = get_config('check_repeated_votes') == '1'
    return jsonify({'status': status})

@app.route('/toggle_vote_check', methods=['POST'])
def toggle_vote_check():
    current = get_config('check_repeated_votes') == '1'
    new_status = '0' if current else '1'
    set_config('check_repeated_votes', new_status)
    status_str = "enabled" if new_status == '1' else "disabled"
    return jsonify({'status': f'Check for repeated votes is now {status_str}'})

# No need for app.run here when using a serverless deployment.
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80, debug=True)
