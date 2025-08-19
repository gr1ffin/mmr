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
    current_week = mmr_system.current_week
    recent_matches = sorted(mmr_system.matches, key=lambda x: x.week, reverse=True)[:10]
    week_matches = [m for m in mmr_system.matches if m.week == current_week - 1]
    return render_template('index.html', teams=leaderboard, leaderboard=leaderboard, current_week=current_week, recent_matches=recent_matches, week_matches=week_matches, matches=mmr_system.matches)

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
    teams_sorted = mmr_system.get_leaderboard()
    return render_template('leaderboard.html', teams=teams_sorted)

@app.route('/matches')
def matches():
    """Show all matches grouped by week"""
    matches = mmr_system.matches
    matches_by_week = {}
    for m in matches:
        matches_by_week.setdefault(m.week, []).append(m)
    sorted_weeks = sorted(matches_by_week.keys())
    return render_template('matches.html', matches_by_week=matches_by_week, sorted_weeks=sorted_weeks, matches=matches)

@app.route('/teams')
def teams():
    """Show all teams."""
    teams_sorted = sorted(mmr_system.teams, key=lambda x: x.mmr, reverse=True)
    return render_template('teams.html', teams=teams_sorted)

@app.route('/team/<team_name>')
def team_detail(team_name):
    """Show detailed information about a specific team."""
    team = next((t for t in mmr_system.teams if t.name == team_name), None)
    if not team:
        flash('Team not found!', 'error')
        return redirect(url_for('teams'))

    # Get team's match history
    team_matches = [m for m in mmr_system.matches if m.team_a == team_name or m.team_b == team_name]
    team_matches.sort(key=lambda x: x.week, reverse=True)

    return render_template('team_detail.html', team=team, matches=team_matches)

@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    """Admin login page."""
    if request.method == 'POST':
        password = request.form.get('password')
        if password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            flash('Admin access granted!', 'success')
            return redirect(url_for('admin_panel'))
        else:
            flash('Invalid password!', 'error')
    return render_template('admin_login.html')

@app.route('/admin_panel')
@admin_required
def admin_panel():
    """Admin panel dashboard."""
    sorted_teams = sorted(mmr_system.teams, key=lambda x: x.mmr, reverse=True)
    return render_template('admin_panel.html', teams=sorted_teams, matches=mmr_system.matches, current_week=mmr_system.current_week)

@app.route('/manage_teams')
@admin_required
def manage_teams():
    """Manage team rosters."""
    teams = mmr_system.teams
    return render_template('manage_teams.html', teams=teams)

@app.route('/create_team', methods=['GET', 'POST'])
@admin_required
def create_team():
    """Create a new team."""
    if request.method == 'POST':
        team_name = request.form.get('team_name')
        if not team_name:
            flash('Team name is required!', 'error')
            return render_template('create_team.html')

        # Check if team already exists
        if any(team.name == team_name for team in mmr_system.teams):
            flash('Team name already exists!', 'error')
            return render_template('create_team.html')

        # Create and add the new team
        new_team = Team(name=team_name)
        mmr_system.teams.append(new_team)
        mmr_system.save_data()

        flash(f'Team {team_name} created successfully!', 'success')
        return redirect(url_for('manage_teams'))

    return render_template('create_team.html')

@app.route('/generate_matches', methods=['GET', 'POST'])
@admin_required
def generate_matches():
    """Generate new week of matches"""
    if request.method == 'POST':
        try:
            matches_per_team = int(request.form.get('matches_per_team', 1))
        except ValueError:
            matches_per_team = 1
        new_matches = mmr_system.generate_weekly_matches(matches_per_team)
        flash(f"Generated {len(new_matches)} matches for Week {mmr_system.current_week}.", 'success')
        return redirect(url_for('matches'))
    return render_template('generate_matches.html')

@app.route('/match/<match_id>')
def match_detail(match_id):
    """Show detailed information about a specific match"""
    match = next((m for m in mmr_system.matches if m.match_id == match_id), None)
    if not match:
        flash('Match not found!', 'error')
        return redirect(url_for('matches'))
    return render_template('match_detail.html', match=match)

@app.route('/input_result/<match_id>', methods=['GET', 'POST'])
@admin_required
def input_result(match_id):
    """Input or update match result"""
    match = next((m for m in mmr_system.matches if m.match_id == match_id), None)
    if not match:
        flash('Match not found!', 'error')
        return redirect(url_for('matches'))

    if request.method == 'POST':
        try:
            score_a = int(request.form.get('score_a', 0))
            score_b = int(request.form.get('score_b', 0))
        except ValueError:
            flash('Invalid scores provided.', 'error')
            return render_template('input_result.html', match=match)

        # Collect set scores from form set_1_a/set_1_b ... set_5_a/set_5_b
        set_scores: List[str] = []
        for i in range(1, 6):
            a = request.form.get(f'set_{i}_a')
            b = request.form.get(f'set_{i}_b')
            if a and b:
                set_scores.append(f"{a}:{b}")

        # Update MMR
        team_a = next((t for t in mmr_system.teams if t.name == match.team_a), None)
        team_b = next((t for t in mmr_system.teams if t.name == match.team_b), None)
        if not team_a or not team_b:
            flash('Teams for this match could not be found.', 'error')
            return render_template('input_result.html', match=match)

        winner, loser = (team_a, team_b) if score_a > score_b else (team_b, team_a)
        mmr_system.update_mmr(winner, loser, (score_a, score_b), set_scores)

        # Persist match result
        match.score = (score_a, score_b)
        match.set_scores = set_scores
        match.completed = True
        match.timestamp = datetime.now().isoformat()
        mmr_system.save_data()

        flash('Match result updated successfully.', 'success')
        return redirect(url_for('matches'))

    return render_template('input_result.html', match=match)

@app.route('/delete_match/<match_id>', methods=['POST'])
@admin_required
def delete_match(match_id):
    """Delete a match by ID."""
    match = next((m for m in mmr_system.matches if m.match_id == match_id), None)
    if not match:
        flash('Match not found.', 'error')
        return redirect(url_for('matches'))

    mmr_system.matches = [m for m in mmr_system.matches if m.match_id != match_id]
    mmr_system.save_data()
    flash('Match deleted successfully.', 'success')
    return redirect(url_for('matches'))

@app.route('/simulate_match', methods=['POST'])
@admin_required
def simulate_match():
    """Simulate a match between two teams."""
    team_a = request.form.get('team_a')
    team_b = request.form.get('team_b')

    if not team_a or not team_b:
        flash('Both teams must be specified.', 'error')
        return redirect(url_for('admin_panel'))

    score_a, score_b = 3, 2  # Simulated score
    set_scores = ["25:20", "23:25", "25:18", "20:25", "15:13"]

    mmr_system.record_match(team_a, team_b, (score_a, score_b), set_scores)
    flash(f'Simulated match between {team_a} and {team_b} logged successfully.', 'success')
    return redirect(url_for('matches'))

@app.route('/update_week', methods=['POST'])
@admin_required
def update_week():
    """Update the current week manually."""
    try:
        new_week = int(request.form.get('new_week', mmr_system.current_week))
        mmr_system.current_week = new_week
        mmr_system.save_data()
        flash(f'Current week updated to Week {new_week}.', 'success')
    except ValueError:
        flash('Invalid week number.', 'error')
    return redirect(url_for('admin_panel'))

@app.route('/admin_logout')
def admin_logout():
    """Log out the admin session."""
    session.pop('admin_logged_in', None)
    flash('Admin session ended.', 'info')
    return redirect(url_for('index'))

@app.route('/admin/refresh_data', methods=['POST'])
@admin_required
def refresh_data():
    """Reload teams/matches from disk (used by Matches page Refresh button)"""
    try:
        mmr_system.load_data()
        return jsonify({'success': True, 'teams_count': len(mmr_system.teams), 'matches_count': len(mmr_system.matches), 'current_week': mmr_system.current_week})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/manage_matches')
@admin_required
def manage_matches():
    """Manage existing matches."""
    matches = mmr_system.matches
    return render_template('manage_matches.html', matches=matches)

@app.route('/system_settings', methods=['GET', 'POST'])
@admin_required
def system_settings():
    """System MMR settings."""
    if request.method == 'POST':
        try:
            new_k_factor = int(request.form.get('k_factor', K_FACTOR))
            new_inactivity_penalty = int(request.form.get('inactivity_penalty', INACTIVITY_PENALTY))
            # Update settings in the MMR system
            mmr_system.update_settings(k_factor=new_k_factor, inactivity_penalty=new_inactivity_penalty)
            flash("System settings updated successfully!", 'success')
        except ValueError:
            flash("Invalid input. Please enter valid numbers.", 'error')
        return redirect(url_for('admin_panel'))
    return render_template('system_settings.html', k_factor=K_FACTOR, inactivity_penalty=INACTIVITY_PENALTY)

@app.route('/backup_data')
@admin_required
def backup_data():
    """Backup system data."""
    try:
        # Create backup with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = "backups"
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)

        # Copy current data files
        import shutil
        shutil.copy2("teams.json", f"{backup_dir}/teams_backup_{timestamp}.json")
        shutil.copy2("matches.json", f"{backup_dir}/matches_backup_{timestamp}.json")

        flash(f'Data backed up successfully! Backup created at {timestamp}', 'success')
    except Exception as e:
        flash(f'Backup failed: {str(e)}', 'error')

    return redirect(url_for('admin_panel'))

@app.route('/restore_data')
@admin_required
def restore_data():
    """Restore system data from the latest backup."""
    try:
        backup_dir = "backups"
        if not os.path.exists(backup_dir):
            flash('No backup directory found.', 'error')
            return redirect(url_for('admin_panel'))

        # Find the latest backup files
        backups = sorted(os.listdir(backup_dir), reverse=True)
        teams_backup = next((f for f in backups if f.startswith('teams_backup_')), None)
        matches_backup = next((f for f in backups if f.startswith('matches_backup_')), None)

        if not teams_backup or not matches_backup:
            flash('No valid backup files found.', 'error')
            return redirect(url_for('admin_panel'))

        # Restore the backup files
        import shutil
        shutil.copy2(f"{backup_dir}/{teams_backup}", "teams.json")
        shutil.copy2(f"{backup_dir}/{matches_backup}", "matches.json")

        # Reload the data into the system
        mmr_system.load_data()

        flash('Data restored successfully from the latest backup.', 'success')
    except Exception as e:
        flash(f'Restore failed: {str(e)}', 'error')

    return redirect(url_for('admin_panel'))

@app.route('/apply_inactivity_penalties')
@admin_required
def apply_inactivity_penalties():
    """Apply inactivity penalties to teams."""
    try:
        for team in mmr_system.teams:
            # Check if team has been inactive (simplified logic)
            if team.matches_played == 0:
                penalty = INACTIVITY_PENALTY
                team.mmr = max(0, team.mmr - penalty)
                team.history.append(f"Inactivity penalty applied: -{penalty} MMR")

        mmr_system.save_data()
        flash('Inactivity penalties applied successfully!', 'success')
    except Exception as e:
        flash(f'Error applying penalties: {str(e)}', 'error')

    return redirect(url_for('admin_panel'))

@app.route('/reset_week')
@admin_required
def reset_week():
    """Reset the current week to the next week."""
    try:
        mmr_system.current_week += 1
        mmr_system.save_data()
        flash('Week reset successfully!', 'success')
    except Exception as e:
        flash(f'Error resetting week: {str(e)}', 'error')
    return redirect(url_for('admin_panel'))

@app.route('/export_data')
@admin_required
def export_data():
    """Export system data to a JSON file."""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_dir = "exports"
        if not os.path.exists(export_dir):
            os.makedirs(export_dir)

        export_path = f"{export_dir}/mmr_export_{timestamp}.json"
        with open(export_path, 'w') as f:
            json.dump({
                'teams': [team.to_dict() for team in mmr_system.teams],
                'matches': [match.to_dict() for match in mmr_system.matches],
                'current_week': mmr_system.current_week
            }, f, indent=2)

        flash(f'Data exported successfully! Export created at {timestamp}', 'success')
    except Exception as e:
        flash(f'Export failed: {str(e)}', 'error')
    return redirect(url_for('admin_panel'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
