from flask import Flask, render_template, request, jsonify, Response
from threading import Lock
from io import StringIO
from datetime import datetime
from collections import Counter

import numpy as np
import time  # Import the time module
import csv
import logging

class NoVoteCountFilter(logging.Filter):
    def filter(self, record):
        return '/vote_count' not in record.getMessage()

log = logging.getLogger('werkzeug')
log.addFilter(NoVoteCountFilter()) # don't pollute the log in the terminal window with vote_count messages

app = Flask(__name__)
app.secret_key = 'your_secret_key'

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

@app.route('/')
def home():
    if voting_open:
        return render_template('vote.html', voting_open=True)
    else:
        return render_template('vote.html', voting_open=False)

@app.route('/admin')
def admin_page():
    return render_template('admin.html')

@app.route('/open_vote', methods=['POST'])
def open_vote():
    global votes, voting_open, current_round_voted_users
    with admin_lock:
        votes = [] # clear stored votes when starting new voting round
        current_round_voted_users = set() # also clear set of stored voters
        voting_open = True
    return jsonify({'status': 'Er kan nu gestemd worden'})

@app.route('/close_vote', methods=['POST'])
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
        return jsonify({'error': '\nJe hebt al gestemd'}), 403

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

        
    print(f"votes array: {votes}")  # Debugging
    print(f"unique_users array: {unique_users}")  # Debugging
    print(f"ID's that have already voted: {current_round_voted_users}")  # Debugging

    return jsonify({'status': '\nJe stem is opgeslagen'})

@app.route('/vote_count', methods=['GET'])
def vote_count():
    return jsonify({'count': len(votes)})
    
@app.route('/stored_vote_count', methods=['GET'])
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
        while True:
            global voting_open
            if voting_open != last_state:
                last_state = voting_open
                yield f"data: {voting_open}\n\n"
            time.sleep(1)  # Sleep for a short time to avoid overwhelming the server

    return Response(event_stream(), mimetype="text/event-stream")

@app.route('/download_csv')
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
def clear_votes():
    global stored_votes
    stored_votes.clear()
    return jsonify({'status': 'Stemrondes gewist'})
    
@app.route('/get_vote_check_status', methods=['GET'])
def get_vote_check_status():
    return jsonify({'status': check_repeated_votes})
    
@app.route('/toggle_vote_check', methods=['POST'])
def toggle_vote_check():
    global check_repeated_votes
    check_repeated_votes = not check_repeated_votes
    status = "enabled" if check_repeated_votes else "disabled"
    return jsonify({'status': f'Check for repeated votes is now {status}'})
    
if __name__ == '__main__':
	# app.run(debug=True)
    app.run(host='0.0.0.0', port=80, debug=True)
    

