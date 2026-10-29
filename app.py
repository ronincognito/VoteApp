from flask import Flask, render_template, request, jsonify, Response, session, redirect, url_for
from threading import Lock
from io import StringIO
from datetime import datetime, timedelta
from functools import wraps
from collections import Counter

import numpy as np
import time  # Import the time module
import csv
import hmac
import json
import logging
import os
import secrets

class NoVoteCountFilter(logging.Filter):
    def filter(self, record):
        return '/vote_count' not in record.getMessage()

log = logging.getLogger('werkzeug')
log.addFilter(NoVoteCountFilter()) # don't pollute the log in the terminal window with vote_count messages

app = Flask(__name__)

# Admin access: a shared code, stored with the session signing key in data/admin_config.json (not on GitHub).
# The file is created on first start with a random code, printed in the log; edit "admin_code" there to change it (takes effect immediately).
ADMIN_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'admin_config.json')

def load_admin_config():
    try:
        with open(ADMIN_CONFIG_FILE) as f:
            config = json.load(f)
    except FileNotFoundError:
        config = {}
    if not config.get('admin_code') or not config.get('secret_key'):
        if not config.get('admin_code'):
            config['admin_code'] = f"{secrets.randbelow(10**6):06d}"  # random, so no installation starts with a code that is public on GitHub
            print(f"New admin code: {config['admin_code']} (change it in {ADMIN_CONFIG_FILE})", flush=True)
        if not config.get('secret_key'):
            config['secret_key'] = secrets.token_hex(32)  # signs the login cookie; changing it logs out every device
        os.makedirs(os.path.dirname(ADMIN_CONFIG_FILE), exist_ok=True)
        with open(ADMIN_CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    return config

app.secret_key = load_admin_config()['secret_key']
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)  # enter the code once per device per month
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Wrong codes: after MAX_LOGIN_ATTEMPTS the login is blocked for LOGIN_LOCKOUT, so the code can't be found by trying them all.
# Counted for all devices together: behind the Synology reverse proxy every request comes from the same address.
MAX_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT = timedelta(minutes=15)
failed_logins = 0
login_locked_until = None

def admin_required(view):
    # page: show the login screen; actions (fetch calls from the admin page): 401, the admin page then reloads to the login screen
    @wraps(view)
    def wrapper(*args, **kwargs):
        if session.get('admin'):
            return view(*args, **kwargs)
        if request.path == '/admin':
            return render_template('admin_login.html')
        if request.path == '/download_csv':  # opened as a page, not via fetch
            return redirect(url_for('admin_page'))
        return jsonify({'error': 'Niet ingelogd'}), 401
    return wrapper

# Global variables to hold voting state
votes = [] 								# list of currently submitted votes and user_id index
unique_users = set()  					# Persistent across rounds, stores all unique user_id's who have at least voted once
user_id_to_index = {} 					#dictionary that holds indices to all unique user_id's, indices are used for compactly presenting users in the results table
current_round_voted_users = set()  		# Track users who voted in the current round
voting_open = False
admin_lock = Lock()
stored_votes = [] 						# collates the results of all voting rounds
max_votes = 100 						# don't store too many voting round. 100 should be enough.
check_repeated_votes = True  			# check for repeated votes by a single user - or not

# All voting state is saved to this file when a round is opened or closed, and on clear/toggle, and read back at startup,
# so a restart or reboot doesn't lose the results. Individual votes are not saved (see vote()).
# The data folder is in .gitignore: results never end up on GitHub and 'git pull' doesn't touch them.
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'votes_state.json')

def save_state():
    # call while holding admin_lock. Writes to a temp file first and then swaps it in, so a crash or power cut can't leave a half-written file
    state = {
        'voting_open': voting_open,
        'votes': votes,
        'current_round_voted_users': list(current_round_voted_users),
        'user_id_to_index': user_id_to_index,
        'stored_votes': stored_votes,
        'check_repeated_votes': check_repeated_votes,
    }
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp_file = STATE_FILE + '.tmp'
        with open(tmp_file, 'w') as f:
            json.dump(state, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, STATE_FILE)
    except OSError as e:
        print(f"ERROR: could not save voting state to {STATE_FILE}: {e}")  # keep running, the state is still in memory

def load_state():
    global votes, unique_users, user_id_to_index, current_round_voted_users, voting_open, stored_votes, check_repeated_votes
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
        voting_open = bool(state['voting_open'])
        votes = [tuple(v) for v in state['votes']]
        current_round_voted_users = set(state['current_round_voted_users'])
        user_id_to_index = dict(state['user_id_to_index'])
        unique_users = set(user_id_to_index)
        stored_votes = list(state['stored_votes'])
        check_repeated_votes = bool(state['check_repeated_votes'])
        print(f"Loaded voting state: {len(stored_votes)} stored rounds" + (f", open round with {len(votes)} votes" if voting_open else ""))
    except (ValueError, KeyError, TypeError) as e:
        # unreadable file: keep it aside for inspection instead of overwriting it, and start with an empty state
        broken_file = STATE_FILE + datetime.now().strftime('.broken-%Y%m%d-%H%M%S')
        os.replace(STATE_FILE, broken_file)
        print(f"ERROR: could not read {STATE_FILE} ({e}), moved it to {broken_file} and started with an empty state")

load_state()

@app.route('/')
def home():
    if voting_open:
        return render_template('vote.html', voting_open=True)
    else:
        return render_template('vote.html', voting_open=False)

@app.route('/apple-touch-icon.png')
@app.route('/apple-touch-icon-precomposed.png')
def apple_touch_icon():
    # standard location where iOS/iPadOS also looks for the home screen icon
    return app.send_static_file('images/apple-touch-icon.png')

@app.route('/favicon.ico')
def favicon():
    return app.send_static_file('images/favicon.ico')

@app.route('/upload')
@app.route('/upload/')
def upload_redirect():
    # Short address for the photo upload app (PhotoUploader), which runs on its own HTTPS port on the same NAS.
    # Set UPLOAD_URL to send visitors somewhere else.
    url = os.environ.get('UPLOAD_URL') or f"https://{request.host.split(':')[0]}:8443/"
    return redirect(url)

@app.route('/admin')
@admin_required
def admin_page():
    return render_template('admin.html')

@app.route('/admin/login', methods=['POST'])
def admin_login():
    global failed_logins, login_locked_until
    now = datetime.now()
    if login_locked_until and now < login_locked_until:
        minutes = int((login_locked_until - now).total_seconds() // 60) + 1
        return render_template('admin_login.html', error=f'Te veel pogingen, probeer het over {minutes} min opnieuw'), 429
    login_locked_until = None
    code = request.form.get('code', '').strip()
    if hmac.compare_digest(code.encode(), str(load_admin_config()['admin_code']).encode()):
        failed_logins = 0
        session.clear()
        session['admin'] = True
        session.permanent = True
        return redirect(url_for('admin_page'))
    failed_logins += 1
    if failed_logins >= MAX_LOGIN_ATTEMPTS:
        failed_logins = 0
        login_locked_until = now + LOGIN_LOCKOUT
        return render_template('admin_login.html', error=f'Te veel pogingen, probeer het over {int(LOGIN_LOCKOUT.total_seconds() // 60)} min opnieuw'), 429
    return render_template('admin_login.html', error='Onjuiste code'), 401

@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.clear()
    return redirect(url_for('admin_page'))

@app.route('/open_vote', methods=['POST'])
@admin_required
def open_vote():
    global votes, voting_open, current_round_voted_users
    with admin_lock:
        votes = [] # clear stored votes when starting new voting round
        current_round_voted_users = set() # also clear set of stored voters
        voting_open = True
        save_state()
    return jsonify({'status': 'Er kan nu gestemd worden'})

@app.route('/close_vote', methods=['POST'])
@admin_required
def close_vote():
    global voting_open, stored_votes
    median_vote = None  # Ensure it's always defined
    with admin_lock:
        voting_open = False
        if votes:
            vote_values = [v[0] for v in votes]  # Extract vote values
            avg_vote = round(np.mean(vote_values), 2)
            stddev_vote = round(np.std(vote_values), 2)
            median_vote = round(np.median(vote_values), 2)
            
            # Get the current date and time
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # Store the current vote outcome in the stored_votes list
            stored_votes.append({
                'timestamp': current_time,
                'avg': avg_vote,
                'sdev': stddev_vote,
                'median': median_vote,  # Store median
                'votes': [v[0] for v in votes],  # Store vote values
                'users': [v[1] for v in votes]  # Store user reference index
            })
            # Keep only the last max_votes outcomes
            if len(stored_votes) > max_votes:
                stored_votes.pop(0)
                
        else:
            avg_vote = stddev_vote = None
        save_state()
            
    return jsonify({
    	'status': 'Stemronde is gesloten', 
    	'avg': avg_vote, 
    	'sdev': stddev_vote, 
    	'median': median_vote,  # Include median in response
    	'votes': votes
    })
    

@app.route('/vote', methods=['POST'])
def vote():

    if not voting_open:
        return jsonify({'error': 'Stemronde is gesloten'}), 403

    user_id = request.form.get('user_id')
    vote_value = request.form.get('vote_value')
    try:
        vote_value = float(vote_value)
    except (ValueError, TypeError):
        return jsonify({'error': 'Ongeldige stemwaarde'}), 400
    
    print(f"Received vote from user: {user_id}")  # Debugging
    print(f"vote value: {vote_value}")  # Debugging


    # Check if user has already voted in the current round
    if check_repeated_votes and user_id in current_round_voted_users:
        print(f"Duplicate vote detected for user: {user_id}")  # Debugging
        return jsonify({'error': 'Je hebt al gestemd'}), 403

    with admin_lock:
        # Track if user ID has already been stored
        if user_id not in unique_users:
            # Assign a new index for new users
            unique_users.add(user_id) # add to the set of all unique voter ID's
            user_index = len(user_id_to_index)
            user_id_to_index[user_id] = user_index # add ID to user_id index dictionary 
        else:
            # Use existing index for already existing users
            user_index = user_id_to_index[user_id]
        
        # Mark user as voted
        current_round_voted_users.add(user_id)

        # Append the vote with user index
        votes.append((vote_value, user_index))
        # not saved here: votes arrive in bursts and shouldn't wait for the disk. The round is saved when it's closed
        # (a crash during an open round loses only that round's votes; after a restart it's open again and members can re-vote)

        
    print(f"votes array: {votes}")  # Debugging
    print(f"unique_users array: {unique_users}")  # Debugging
    print(f"ID's that have already voted: {current_round_voted_users}")  # Debugging

    return jsonify({'status': 'Je stem is opgeslagen'})

@app.route('/has_voted', methods=['GET'])
def has_voted():
    # lets the vote page show 'already voted' right after a refresh, instead of only after a second submit
    user_id = request.args.get('user_id')
    return jsonify({'has_voted': check_repeated_votes and user_id in current_round_voted_users})

@app.route('/vote_count', methods=['GET'])
@admin_required
def vote_count():
    return jsonify({'count': len(votes)})
    
@app.route('/stored_vote_count', methods=['GET'])
@admin_required
def stored_vote_count():
    # Get the count of stored votes
    vote_count = len(stored_votes)

    # Get the last 10 rounds (assuming stored_votes is a list of rounds)
    last_10_votes = stored_votes[-10:] if len(stored_votes) > 10 else stored_votes

    return jsonify({
        'count': vote_count,
        'last_10_votes': last_10_votes
    })

@app.route('/voting_status', methods=['GET'])
def voting_status():
    global voting_open
    return jsonify({'voting_open': voting_open})
    
@app.route('/events')
def events():
    def event_stream():
        last_state = None
        idle_seconds = 0
        while True:
            global voting_open
            if voting_open != last_state:
                last_state = voting_open
                idle_seconds = 0
                yield f"data: {voting_open}\n\n"
            elif idle_seconds >= 10:
                idle_seconds = 0
                yield "event: ping\ndata: \n\n"  # keeps the reverse proxy from closing an idle connection; the vote page reconnects when pings stop arriving
            time.sleep(1)  # Sleep for a short time to avoid overwhelming the server
            idle_seconds += 1

    # tell the (Synology nginx) reverse proxy not to buffer or cache the stream, otherwise updates arrive late or not at all
    return Response(event_stream(), mimetype="text/event-stream",
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

@app.route('/download_csv')
@admin_required
def download_csv():
    global stored_votes

    # Create a CSV in memory
    si = StringIO()
    writer = csv.writer(si)

    # Write CSV header
    writer.writerow(['Time','Average', 'Standard Deviation', 'Votes', 'Users'])

    # Write the last max_votes votes outcomes
    for vote_outcome in stored_votes:
        writer.writerow([vote_outcome['timestamp'], vote_outcome['avg'], vote_outcome['sdev'], ','.join(map(str, vote_outcome['votes'])), ','.join(map(str, vote_outcome['users']))])

    # Serve the CSV file as a downloadable file
    output = si.getvalue()
    si.close()

    return Response(output, mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=stored_votes.csv"})

@app.route('/clear_votes', methods=['POST'])
@admin_required
def clear_votes():
    global stored_votes
    with admin_lock:
        stored_votes.clear()
        # fresh start: voter numbers start at 0 again. Not during an open round, its votes refer to the current numbers
        if not voting_open:
            unique_users.clear()
            user_id_to_index.clear()
        save_state()
    return jsonify({'status': 'Stemrondes gewist'})
    
@app.route('/get_vote_check_status', methods=['GET'])
@admin_required
def get_vote_check_status():
    return jsonify({'status': check_repeated_votes})
    
@app.route('/toggle_vote_check', methods=['POST'])
@admin_required
def toggle_vote_check():
    global check_repeated_votes
    with admin_lock:
        check_repeated_votes = not check_repeated_votes
        save_state()
    status = "enabled" if check_repeated_votes else "disabled"
    return jsonify({'status': f'Check for repeated votes is now {status}'})
    
if __name__ == '__main__':
	# app.run(debug=True)
    app.run(host='0.0.0.0', port=8080, debug=False) # never use debug=True when the app is reachable from the internet
    

