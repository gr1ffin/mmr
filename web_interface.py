from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, session
import json
import os
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import uuid
from mmr_system import MMRSystem, Team, Match
import logging
import requests

# Configure logging for the web server
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # Change this in production
ADMIN_PASSWORD = "starsadmin*"  # Admin password

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1407186075209830441/aIiJZZaAkTw_dDojhbCE1W_g4E8se9Ie-RQ-ohwXv2mnIErQW0MNYeE4Pg8FSe7z3kMn"  # Add your Discord webhook URL here

# Global MMR system instance
mmr_system = MMRSystem()

# Load configuration from JSON file
CONFIG_FILE = 'mmr_config.json'
if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
else:
    config = {
        "BASE_MMR": 1000,
        "PLACEMENT_MATCHES": 3,
        "K_FACTOR": 20,
        "CHALLENGE_MULTIPLIER": 0.5,
        "INACTIVITY_PENALTY": 10,
        "MARGIN_BONUS": {
            "3_0": 5,
            "3_1": 3,
            "3_2": 1
        },
        "POINT_DIFF_MULTIPLIER": 0.1
    }

# Define global variables for MMR settings
BASE_MMR = config["BASE_MMR"]
PLACEMENT_MATCHES = config["PLACEMENT_MATCHES"]
K_FACTOR = config["K_FACTOR"]
CHALLENGE_MULTIPLIER = config["CHALLENGE_MULTIPLIER"]
INACTIVITY_PENALTY = config["INACTIVITY_PENALTY"]
MARGIN_BONUS = {tuple(map(int, k.replace(',', '_').split('_'))): v for k, v in config["MARGIN_BONUS"].items()}
POINT_DIFF_MULTIPLIER = config["POINT_DIFF_MULTIPLIER"]

# Admin authentication decorator
def admin_required(f):
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash('Admin access required. Please log in.', 'error')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

@app.route('/')
def index():
    leaderboard = mmr_system.get_leaderboard()
    return render_template('index.html', leaderboard=leaderboard)

@app.route('/record_match', methods=['POST'])
def record_match():
    team_a = request.form.get('team_a')
    team_b = request.form.get('team_b')
    score_a = int(request.form.get('score_a', 0))
    score_b = int(request.form.get('score_b', 0))

    if not team_a or not team_b:
        flash('Both teams must be specified.', 'error')
        return redirect(url_for('index'))

    mmr_system.record_match(team_a, team_b, (score_a, score_b))
    flash(f'Match recorded: {team_a} {score_a} - {score_b} {team_b}', 'success')
    return redirect(url_for('index'))

@app.route('/leaderboard')
def leaderboard():
    leaderboard = mmr_system.get_leaderboard()
    return render_template('leaderboard.html', leaderboard=leaderboard)

@app.route('/matches')
def matches():
    """Show all matches."""
    matches = mmr_system.matches
    return render_template('matches.html', matches=matches)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
