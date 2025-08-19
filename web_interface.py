from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, session
import json
import os
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import uuid
from mmr_system import MMRSystem, Team, Match
import logging
import requests
from werkzeug.middleware.proxy_fix import ProxyFix

# Configure logging for the web server
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

app = Flask(__name__)
# Trust one reverse proxy (nginx) for forwarded headers
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)
# Use environment variables for secrets in production
app.secret_key = os.getenv('SECRET_KEY') or os.urandom(32)
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'stars')  # Admin password (override in production)

# Production cookie/security defaults (can be overridden via env)
app.config.update(
    SESSION_COOKIE_SECURE=(os.getenv('SESSION_COOKIE_SECURE', 'true').lower() in ('1', 'true', 'yes')),
    SESSION_COOKIE_SAMESITE=os.getenv('SESSION_COOKIE_SAMESITE', 'Lax'),
    SESSION_COOKIE_HTTPONLY=True,
    PREFERRED_URL_SCHEME=os.getenv('PREFERRED_URL_SCHEME', 'https'),
)

# Discord webhook URL from environment variable (no hard-coded secrets)
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# Helper: send a plain text message to Discord via webhook (best-effort)
def _post_discord_webhook_message(content: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json={'content': content}, timeout=5)
        if resp.status_code not in (200, 204):
            logging.warning(f"Discord webhook returned status {resp.status_code}: {resp.text}")
    except Exception as e:
        logging.warning(f"Failed to post Discord webhook: {e}")

# Helper: send an embed to Discord via webhook (best-effort)
def _post_discord_embed(embed: Dict) -> None:
    if not DISCORD_WEBHOOK_URL:
        return
    payload = {
        # You can optionally set a username/avatar for the webhook appearance
        # 'username': 'MMR Bot',
        # 'avatar_url': 'https://example.com/avatar.png',
        'embeds': [embed]
    }
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
        if resp.status_code not in (200, 204):
            logging.warning(f"Discord webhook (embed) returned status {resp.status_code}: {resp.text}")
    except Exception as e:
        logging.warning(f"Failed to post Discord embed: {e}")

# Helper: format and send a match result notification
def notify_discord_match_result(match: Match, score_a: int, score_b: int, set_scores: Optional[List[str]]) -> None:
    try:
        match_url = request.url_root.rstrip('/') + url_for('match_detail', match_id=match.match_id)
        sets_text = ', '.join(set_scores) if set_scores else 'N/A'
        # Choose a color (green if team_a won, red if team_b won, gray if tie)
        color = 0x57F287 if score_a > score_b else (0xED4245 if score_b > score_a else 0x99AAB5)
        embed = {
            'title': f"Match Result - Week {match.week}",
            'description': f"{match.team_a} {score_a}-{score_b} {match.team_b}",
            'color': color,
            'fields': [
                {'name': 'Set Scores', 'value': sets_text, 'inline': False},
                {'name': 'Details', 'value': f"[View Match]({match_url})", 'inline': False}
            ],
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'footer': {'text': 'MMR System'}
        }
        _post_discord_embed(embed)
    except Exception as e:
        logging.warning(f"Failed to compose/send Discord notification: {e}")

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

# After initializing MMRSystem, hydrate settings from DB so runtime reflects persisted config
try:
    db_cfg = mmr_system.get_mmr_config() or {}
    # Merge DB config over file/defaults
    config.update(db_cfg)
    BASE_MMR = config["BASE_MMR"]
    PLACEMENT_MATCHES = config["PLACEMENT_MATCHES"]
    K_FACTOR = config["K_FACTOR"]
    CHALLENGE_MULTIPLIER = config["CHALLENGE_MULTIPLIER"]
    INACTIVITY_PENALTY = config["INACTIVITY_PENALTY"]
    POINT_DIFF_MULTIPLIER = config["POINT_DIFF_MULTIPLIER"]
    MARGIN_BONUS = { (3,0): config["MARGIN_BONUS"].get("3_0", 5),
                     (3,1): config["MARGIN_BONUS"].get("3_1", 3),
                     (3,2): config["MARGIN_BONUS"].get("3_2", 1) }
    # Apply to running system
    mmr_system.update_settings(
        k_factor=K_FACTOR,
        inactivity_penalty=INACTIVITY_PENALTY,
        point_diff_multiplier=POINT_DIFF_MULTIPLIER,
        margin_bonus=MARGIN_BONUS,
    )
    mmr_system.placement_matches = PLACEMENT_MATCHES
except Exception as e:
    logging.warning(f"Failed to hydrate settings from DB: {e}")

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
    # Pull full dashboard title from DB (empty string falls back in template)
    dashboard_title = mmr_system.get_setting('dashboard_header')
    return render_template(
        'index.html',
        teams=leaderboard,
        leaderboard=leaderboard,
        current_week=current_week,
        recent_matches=recent_matches,
        week_matches=week_matches,
        matches=mmr_system.matches,
        dashboard_title=dashboard_title,
    )

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
    """Show all teams with placed and provisional separation."""
    teams_sorted = sorted(mmr_system.teams, key=lambda x: x.mmr, reverse=True)
    placed_teams = [t for t in teams_sorted if t.matches_played >= mmr_system.placement_matches]
    provisional_teams = [t for t in teams_sorted if t.matches_played < mmr_system.placement_matches]
    return render_template('teams.html', teams=teams_sorted, placed_teams=placed_teams, provisional_teams=provisional_teams, placement_matches=mmr_system.placement_matches)

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

    # Compute derived stats
    placed = team.matches_played >= mmr_system.placement_matches
    display_mmr = team.mmr if placed else 'TBD'

    total_wins = team.wins
    total_losses = team.losses
    total_games = total_wins + total_losses
    win_rate = (total_wins / total_games) if total_games > 0 else 0.0

    # Average points from set scores
    total_points = 0
    total_sets = 0
    for m in team_matches:
        if not m.set_scores:
            continue
        for s in m.set_scores:
            try:
                a_str, b_str = s.split(':')
                a = int(a_str); b = int(b_str)
                if m.team_a == team.name:
                    total_points += a
                elif m.team_b == team.name:
                    total_points += b
                total_sets += 1
            except Exception:
                continue
    avg_points = (total_points / total_sets) if total_sets > 0 else 0

    # Rank in leaderboard (only if placed)
    lb = mmr_system.get_leaderboard()
    try:
        rank = next(i+1 for i, t in enumerate(lb) if t.name == team.name)
    except StopIteration:
        rank = None

    return render_template('team_detail.html', team=team, matches=team_matches, display_mmr=display_mmr, win_rate=win_rate, avg_points=avg_points, rank=rank, placement_matches=mmr_system.placement_matches)

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
    db_status = mmr_system.get_db_status()
    return render_template('admin_panel.html', teams=sorted_teams, matches=mmr_system.matches, current_week=mmr_system.current_week, db_status=db_status)

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
        try:
            initial_mmr = int(request.form.get('initial_mmr', BASE_MMR))
        except ValueError:
            initial_mmr = BASE_MMR
        active = True if request.form.get('active') in ('on', 'true', '1') else False
        player_names = request.form.getlist('player_names[]')
        player_roles = request.form.getlist('player_roles[]')

        if not team_name:
            flash('Team name is required!', 'error')
            return render_template('create_team.html')

        # Check if team already exists
        if any(team.name == team_name for team in mmr_system.teams):
            flash('Team name already exists!', 'error')
            return render_template('create_team.html')

        # Create and add the new team
        new_team = Team(name=team_name, mmr=initial_mmr)
        new_team.active = active
        new_team.provisional = True
        # Build roster
        roster = []
        for n, r in zip(player_names, player_roles):
            n = (n or '').strip()
            if n:
                roster.append({'name': n, 'role': (r or '').strip(), 'matches_played': 0})
        new_team.roster = roster

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
        if not new_matches:
            flash("Could not generate a complete schedule with the current constraints. No matches were created.", 'error')
        else:
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
        totals_a = 0
        totals_b = 0
        for i in range(1, 6):
            a = request.form.get(f'set_{i}_a')
            b = request.form.get(f'set_{i}_b')
            if a and b:
                set_scores.append(f"{a}:{b}")
                try:
                    totals_a += int(a)
                    totals_b += int(b)
                except ValueError:
                    pass

        # Update MMR
        team_a = next((t for t in mmr_system.teams if t.name == match.team_a), None)
        team_b = next((t for t in mmr_system.teams if t.name == match.team_b), None)
        if not team_a or not team_b:
            flash('Teams for this match could not be found.', 'error')
            return render_template('input_result.html', match=match)

        winner, loser = (team_a, team_b) if score_a > score_b else (team_b, team_a)
        # Map totals to winner/loser orientation
        if winner is team_a:
            pw, pl = totals_a, totals_b
        else:
            pw, pl = totals_b, totals_a
        mmr_system.update_mmr(winner, loser, (score_a, score_b), set_scores=set_scores, points_winner=pw, points_loser=pl)

        # Persist match result
        match.score = (score_a, score_b)
        match.set_scores = set_scores
        match.completed = True
        match.timestamp = datetime.now().isoformat()
        mmr_system.save_data()

        # Notify Discord webhook about the result (best-effort)
        notify_discord_match_result(match, score_a, score_b, set_scores)

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
    # Ensure template has access to full config including margin bonuses, pulling from DB
    cfg = mmr_system.get_mmr_config()
    return render_template('system_settings.html', config=cfg)

@app.route('/backup_data')
@admin_required
def backup_data():
    """Backup system data (SQLite DB preferred)."""
    try:
        # Create backup with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = "backups"
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)

        # Prefer backing up the SQLite database if present
        db_path = os.getenv('MMR_DB_PATH') or os.path.join(os.path.dirname(__file__), 'mmr.db')
        if os.path.exists(db_path):
            import shutil
            shutil.copy2(db_path, f"{backup_dir}/mmr_backup_{timestamp}.db")
        else:
            # Fallback: copy current JSON files
            import shutil
            if os.path.exists("teams.json"):
                shutil.copy2("teams.json", f"{backup_dir}/teams_backup_{timestamp}.json")
            if os.path.exists("matches.json"):
                shutil.copy2("matches.json", f"{backup_dir}/matches_backup_{timestamp}.json")

        flash(f'Data backed up successfully! Backup created at {timestamp}', 'success')
    except Exception as e:
        logging.exception("Backup failed")
        flash(f'Backup failed: {str(e)}', 'error')

    return redirect(url_for('admin_panel'))

@app.route('/restore_data')
@admin_required
def restore_data():
    """Restore system data from the latest backup (SQLite DB preferred)."""
    try:
        backup_dir = "backups"
        if not os.path.exists(backup_dir):
            flash('No backup directory found.', 'error')
            return redirect(url_for('admin_panel'))

        files = sorted(os.listdir(backup_dir), reverse=True)
        # Prefer DB backups
        db_backup = next((f for f in files if f.startswith('mmr_backup_') and f.endswith('.db')), None)
        import shutil
        if db_backup:
            db_path = os.getenv('MMR_DB_PATH') or os.path.join(os.path.dirname(__file__), 'mmr.db')
            shutil.copy2(os.path.join(backup_dir, db_backup), db_path)
        else:
            # Fallback to JSON backups
            teams_backup = next((f for f in files if f.startswith('teams_backup_')), None)
            matches_backup = next((f for f in files if f.startswith('matches_backup_')), None)
            if not teams_backup or not matches_backup:
                flash('No valid backup files found.', 'error')
                return redirect(url_for('admin_panel'))
            shutil.copy2(f"{backup_dir}/{teams_backup}", "teams.json")
            shutil.copy2(f"{backup_dir}/{matches_backup}", "matches.json")

        # Reload the data into the system
        mmr_system.load_data()

        flash('Data restored successfully from the latest backup.', 'success')
    except Exception as e:
        logging.exception("Restore failed")
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

@app.route('/admin/recalculate_mmr', methods=['POST'])
@admin_required
def recalculate_mmr():
    mmr_system.load_data()  # reload latest data from DB
    mmr_system.recalculate_all_mmr()
    flash('Recalculated MMR from complete match history.', 'success')
    return redirect(request.referrer or url_for('leaderboard'))

@app.route('/admin/edit_team/<team_name>', methods=['POST'])
@admin_required
def edit_team(team_name):
    team = next((t for t in mmr_system.teams if t.name == team_name), None)
    if not team:
        flash('Team not found.', 'error')
        return redirect(url_for('manage_teams'))
    new_name = request.form.get('team_name', team.name).strip()
    try:
        new_mmr = int(request.form.get('mmr', team.mmr))
    except ValueError:
        new_mmr = team.mmr
    active = True if request.form.get('active') in ('on', 'true', '1') else False

    # Handle rename: update matches references
    old_name = team.name
    team.name = new_name
    team.mmr = new_mmr
    team.active = active

    if old_name != new_name:
        for m in mmr_system.matches:
            if m.team_a == old_name:
                m.team_a = new_name
            if m.team_b == old_name:
                m.team_b = new_name

    mmr_system.save_data()
    flash('Team updated successfully.', 'success')
    return redirect(url_for('manage_teams'))

# Roster management
@app.route('/admin/add_player/<team_name>', methods=['POST'])
@admin_required
def add_player(team_name):
    team = next((t for t in mmr_system.teams if t.name == team_name), None)
    if not team:
        flash('Team not found.', 'error')
        return redirect(url_for('manage_teams'))
    player_name = request.form.get('player_name')
    role = request.form.get('role')
    if not player_name:
        flash('Player name is required.', 'error')
        return redirect(url_for('manage_teams'))
    if team.roster is None:
        team.roster = []
    # Avoid duplicates by name (case-insensitive)
    if any(p.get('name', '').lower() == player_name.lower() for p in team.roster):
        flash('Player already in roster.', 'error')
        return redirect(url_for('manage_teams'))
    team.roster.append({'name': player_name, 'role': role, 'matches_played': 0})
    mmr_system.save_data()
    flash('Player added.', 'success')
    return redirect(url_for('manage_teams'))

@app.route('/admin/remove_player/<team_name>/<player_name>', methods=['POST'])
@admin_required
def remove_player(team_name, player_name):
    team = next((t for t in mmr_system.teams if t.name == team_name), None)
    if not team:
        return ('Team not found', 404)
    team.roster = [p for p in (team.roster or []) if p.get('name') != player_name]
    mmr_system.save_data()
    return ('', 204)

@app.route('/admin/reset_team_mmr/<team_name>', methods=['POST'])
@admin_required
def reset_team_mmr(team_name):
    team = next((t for t in mmr_system.teams if t.name == team_name), None)
    if not team:
        return ('Team not found', 404)
    team.mmr = BASE_MMR
    mmr_system.save_data()
    return ('', 204)

@app.route('/admin/clear_team_history/<team_name>', methods=['POST'])
@admin_required
def clear_team_history(team_name):
    # Remove matches involving this team, then recalc
    before = len(mmr_system.matches)
    mmr_system.matches = [m for m in mmr_system.matches if m.team_a != team_name and m.team_b != team_name]
    mmr_system.save_data()
    mmr_system.recalculate_all_mmr()
    logging.info(f"Cleared {before - len(mmr_system.matches)} matches for {team_name} and recalculated.")
    return ('', 204)

@app.route('/admin/delete_team/<team_name>', methods=['POST'])
@admin_required
def delete_team(team_name):
    # Remove team
    mmr_system.teams = [t for t in mmr_system.teams if t.name != team_name]
    # Remove related matches and recalc
    mmr_system.matches = [m for m in mmr_system.matches if m.team_a != team_name and m.team_b != team_name]
    mmr_system.save_data()
    mmr_system.recalculate_all_mmr()
    return ('', 204)

# Update system settings (form action in system_settings.html)
@app.route('/update_system_settings', methods=['POST'])
@admin_required
def update_system_settings():
    try:
        global config, BASE_MMR, PLACEMENT_MATCHES, K_FACTOR, CHALLENGE_MULTIPLIER, INACTIVITY_PENALTY, POINT_DIFF_MULTIPLIER, MARGIN_BONUS
        base_mmr = int(request.form.get('base_mmr', BASE_MMR))
        k_factor = int(request.form.get('k_factor', K_FACTOR))
        placement_matches = int(request.form.get('placement_matches', 2))
        challenge_multiplier = float(request.form.get('challenge_multiplier', 0.5))
        inactivity_penalty = int(request.form.get('inactivity_penalty', 10))
        point_diff_multiplier = float(request.form.get('point_diff_multiplier', 0.1))
        margin_3_0 = int(request.form.get('margin_3_0', 5))
        margin_3_1 = int(request.form.get('margin_3_1', 3))
        margin_3_2 = int(request.form.get('margin_3_2', 1))

        # Persist to config file
        cfg = {
            "BASE_MMR": base_mmr,
            "PLACEMENT_MATCHES": placement_matches,
            "K_FACTOR": k_factor,
            "CHALLENGE_MULTIPLIER": challenge_multiplier,
            "INACTIVITY_PENALTY": inactivity_penalty,
            "MARGIN_BONUS": {
                "3_0": margin_3_0,
                "3_1": margin_3_1,
                "3_2": margin_3_2
            },
            "POINT_DIFF_MULTIPLIER": point_diff_multiplier
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(cfg, f, indent=2)

        # Also persist to DB (authoritative), ensuring string keys for JSON
        try:
            mmr_system.update_mmr_config_in_db(
                base_mmr=base_mmr,
                placement_matches=placement_matches,
                k_factor=k_factor,
                challenge_multiplier=challenge_multiplier,
                inactivity_penalty=inactivity_penalty,
                margin_bonus={"3_0": margin_3_0, "3_1": margin_3_1, "3_2": margin_3_2},
                point_diff_multiplier=point_diff_multiplier,
            )
        except Exception:
            # Non-fatal: continue with in-memory update and file save
            pass

        # Update in-memory config and globals so routes use latest values
        config = cfg
        BASE_MMR = config["BASE_MMR"]
        PLACEMENT_MATCHES = config["PLACEMENT_MATCHES"]
        K_FACTOR = config["K_FACTOR"]
        CHALLENGE_MULTIPLIER = config["CHALLENGE_MULTIPLIER"]
        INACTIVITY_PENALTY = config["INACTIVITY_PENALTY"]
        POINT_DIFF_MULTIPLIER = config["POINT_DIFF_MULTIPLIER"]
        MARGIN_BONUS = {(3,0): margin_3_0, (3,1): margin_3_1, (3,2): margin_3_2}

        # Apply to running system (for next calculations)
        mmr_system.update_settings(k_factor=k_factor, inactivity_penalty=inactivity_penalty, point_diff_multiplier=point_diff_multiplier, margin_bonus={(3,0): margin_3_0, (3,1): margin_3_1, (3,2): margin_3_2})
        mmr_system.placement_matches = placement_matches
        # Update provisional flags based on new threshold
        for t in mmr_system.teams:
            t.provisional = t.matches_played < mmr_system.placement_matches
        mmr_system.save_data()

        flash('Settings updated.', 'success')
    except Exception as e:
        logging.exception("Error updating settings")
        flash(f'Failed to update settings: {e}', 'error')
    return redirect(url_for('system_settings'))

@app.route('/reset_data', methods=['POST'])
@admin_required
def reset_data():
    try:
        # Clear all matches
        mmr_system.matches = []
        # Reset all teams to base state
        for t in mmr_system.teams:
            t.mmr = BASE_MMR
            t.matches_played = 0
            t.wins = 0
            t.losses = 0
            t.history = []
            t.provisional = True
        # Optionally reset week counter
        mmr_system.current_week = 1
        # Persist changes
        mmr_system.save_data()
        return ('', 204)
    except Exception as e:
        logging.exception('Failed to reset data')
        return (str(e), 500)

@app.route('/admin/db_status')
@admin_required
def admin_db_status():
    try:
        return jsonify(mmr_system.get_db_status())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/remigrate', methods=['POST'])
@admin_required
def admin_remigrate():
    ok = mmr_system.remigrate_from_json()
    if ok:
        flash('Re-imported data from legacy JSON and refreshed in-memory state.', 'success')
    else:
        flash('Re-import failed. Ensure legacy teams.json and matches.json exist and are valid.', 'error')
    return redirect(url_for('admin_panel'))

@app.route('/admin/import_team', methods=['POST'])
@admin_required
def admin_import_team():
    """Import a new team from pasted JSON in admin panel modal."""
    raw = request.form.get('team_json', '').strip()
    if not raw:
        flash('No JSON provided.', 'error')
        return redirect(url_for('admin_panel'))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        flash(f'Invalid JSON: {e}', 'error')
        return redirect(url_for('admin_panel'))

    # Support single object or list of objects but only add new teams
    if isinstance(data, dict) and 'teams' in data and isinstance(data['teams'], list):
        teams_payload = data['teams']
    elif isinstance(data, list):
        teams_payload = data
    elif isinstance(data, dict):
        teams_payload = [data]
    else:
        flash('JSON must be a team object or list of team objects.', 'error')
        return redirect(url_for('admin_panel'))

    added = 0
    skipped = 0
    for t in teams_payload:
        if not isinstance(t, dict):
            skipped += 1
            continue
        name = (t.get('name') or '').strip()
        if not name:
            skipped += 1
            continue
        # Check duplicate by case-insensitive name
        if any(existing.name.lower() == name.lower() for existing in mmr_system.teams):
            skipped += 1
            continue
        # Build Team object
        try:
            mmr_val = int(t.get('mmr', BASE_MMR))
        except Exception:
            mmr_val = BASE_MMR
        new_team = Team(name=name, mmr=mmr_val)
        new_team.active = bool(t.get('active', True))
        # Matches/stats
        try:
            new_team.matches_played = int(t.get('matches_played', 0))
        except Exception:
            new_team.matches_played = 0
        try:
            new_team.wins = int(t.get('wins', 0))
        except Exception:
            new_team.wins = 0
        try:
            new_team.losses = int(t.get('losses', 0))
        except Exception:
            new_team.losses = 0
        # History
        hist = t.get('history')
        new_team.history = hist if isinstance(hist, list) else []
        # Provisional flag respects placement threshold
        prov = t.get('provisional')
        if prov is None:
            new_team.provisional = new_team.matches_played < mmr_system.placement_matches
        else:
            new_team.provisional = bool(prov)
        # Visuals and roster
        new_team.logo = t.get('logo') or ''
        new_team.hexcolor = t.get('hexcolor') or '#374151'
        roster = t.get('roster') or []
        if isinstance(roster, list):
            cleaned = []
            for p in roster:
                if not isinstance(p, dict):
                    continue
                n = (p.get('name') or '').strip()
                if not n:
                    continue
                cleaned.append({
                    'name': n,
                    'role': (p.get('role') or '').strip(),
                    'matches_played': int(p.get('matches_played') or 0)
                })
            new_team.roster = cleaned
        else:
            new_team.roster = []

        mmr_system.teams.append(new_team)
        added += 1

    if added:
        mmr_system.save_data()
        flash(f'Imported {added} team(s). Skipped {skipped}.', 'success')
    else:
        flash('No new teams were imported. Check for duplicates or invalid JSON format.', 'warning')

    return redirect(url_for('admin_panel'))

if __name__ == '__main__':
    # In production use gunicorn. This block is for local/dev usage.
    debug = os.getenv('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes')
    port = int(os.getenv('PORT', 5001))
    host = os.getenv('HOST', '0.0.0.0')
    app.run(debug=debug, host=host, port=port)
