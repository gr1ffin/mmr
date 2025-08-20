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
import re
from dotenv import load_dotenv
from functools import wraps

# Load .env for local/dev use (production uses systemd EnvironmentFile)
load_dotenv()
# Also load mmr.env if present (user-provided env file)
import os as _os
if _os.path.exists('mmr.env'):
    load_dotenv('mmr.env')

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

def _contrast_text_color(hex_str: Optional[str], light: str = '#ffffff', dark: str = '#000000') -> str:
    """Return a text color with good contrast for the given background hex color.
    Uses YIQ luma approximation.
    """
    try:
        h = (hex_str or '').strip().lstrip('#')
        if len(h) == 3:
            h = ''.join(ch * 2 for ch in h)
        if len(h) != 6:
            # fallback to dark text on light-ish default
            return light
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
        yiq = (r * 299 + g * 587 + b * 114) / 1000
        # Threshold 160 gives decent balance for saturated colors
        return dark if yiq > 160 else light
    except Exception:
        return light


@app.context_processor
def inject_helpers():
    # Expose helpers to Jinja templates
    return {"contrast_text_color": _contrast_text_color}

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
        logging.warning("Discord webhook URL is not set.")
        return
    payload = {
        'embeds': [embed]
    }
    try:
        logging.info(f"Sending embed to Discord webhook: {json.dumps(payload)}")
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
        if resp.status_code not in (200, 204):
            logging.warning(f"Discord webhook (embed) returned status {resp.status_code}: {resp.text}")
        else:
            logging.info("Embed sent successfully to Discord webhook.")
    except Exception as e:
        logging.warning(f"Failed to post Discord embed: {e}")

# Helper: parse #RRGGBB -> int for Discord embed color
def _parse_hex_color(s: Optional[str], default: int = 0x5865F2) -> int:
    try:
        if not s:
            return default
        h = s.strip().lstrip('#')
        if len(h) == 3:
            h = ''.join(ch * 2 for ch in h)
        return int(h, 16)
    except Exception:
        return default

# Helper: format and send a match result notification
def notify_discord_match_result(match: Match, score_a: int, score_b: int, set_scores: Optional[List[str]]) -> None:
    try:
        match_url = request.url_root.rstrip('/') + url_for('match_detail', match_id=match.match_id)
        sets_text = ', '.join(set_scores) if set_scores else 'N/A'

        # Resolve teams
        team_a_obj = next((t for t in mmr_system.teams if t.name == match.team_a), None)
        team_b_obj = next((t for t in mmr_system.teams if t.name == match.team_b), None)

        # Determine winner for styling
        winner = None
        if score_a > score_b:
            winner = team_a_obj
        elif score_b > score_a:
            winner = team_b_obj

        # Color & thumbnail based on winner
        if winner is not None:
            color = _parse_hex_color(getattr(winner, 'hexcolor', None), 0x57F287)
            thumbnail = {'url': getattr(winner, 'logo', '')} if getattr(winner, 'logo', '') else None
        else:
            color = 0x99AAB5  # tie/neutral
            thumbnail = None

        fields = [
            {'name': 'Set Scores', 'value': sets_text, 'inline': False},
            {'name': 'Details', 'value': f"[View Match]({match_url})", 'inline': False}
        ]

        # Include post-match MMR for teams with >= 2 matches played
        try:
            a_mp = int(getattr(team_a_obj, 'matches_played', 0) or 0)
            b_mp = int(getattr(team_b_obj, 'matches_played', 0) or 0)
        except Exception:
            a_mp = b_mp = 0

        title_parts = []
        if team_a_obj:
            title_a = team_a_obj.name
            if a_mp >= 2:
                title_a += f" ({team_a_obj.mmr})"
            title_parts.append(title_a)
        else:
            title_parts.append(match.team_a)

        if team_b_obj:
            title_b = team_b_obj.name
            if b_mp >= 2:
                title_b += f" ({team_b_obj.mmr})"
            title_parts.append(title_b)

        embed = {
            'title': f"{title_parts[0]} {score_a}-{score_b} {title_parts[1]}",
            'description': f"Match Result - Week {match.week}",
            'color': color,
            'fields': fields,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'footer': {'text': 'STARS'}
        }
        if thumbnail:
            embed['thumbnail'] = thumbnail

        _post_discord_embed(embed)
    except Exception as e:
        logging.warning(f"Failed to compose/send Discord notification: {e}")

# Helper: announce weekly schedule to Discord webhook
def notify_discord_week_schedule(week: int, matches: List[Match]) -> None:
    try:
        if not matches:
            return
        matches_url = request.url_root.rstrip('/') + url_for('matches')
        # Chunk matches into multiple fields if long
        lines = [f"{m.team_a} vs {m.team_b}" for m in matches]
        fields = []
        chunk = []
        total_chars = 0
        for line in lines:
            # Discord field value soft limit ~1024 chars
            if total_chars + len(line) + 1 > 900:  # keep margin
                fields.append({'name': f'Matches ({len(chunk)})', 'value': '\n'.join(chunk), 'inline': False})
                chunk = []
                total_chars = 0
            chunk.append(line)
            total_chars += len(line) + 1
        if chunk:
            fields.append({'name': f'Matches ({len(chunk)})', 'value': '\n'.join(chunk), 'inline': False})
        embed = {
            'title': f"Weekly Schedule - Week {week}",
            'description': f"Scheduled matches for Week {week}.\n[View Matches]({matches_url})",
            'color': 0x5865F2,  # blurple
            'fields': fields,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'footer': {'text': 'MMR System'}
        }
        _post_discord_embed(embed)
    except Exception as e:
        logging.warning(f"Failed to send weekly schedule webhook: {e}")

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
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash('Admin access required. Please log in.', 'error')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
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
    # Ensure provisional flags reflect matches played vs placement threshold
    changed = False
    for t in teams_sorted:
        desired = t.matches_played < mmr_system.placement_matches
        if getattr(t, 'provisional', desired) != desired:
            t.provisional = desired
            changed = True
    if changed:
        mmr_system.save_data()
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

    # Compute per-match MMR change for this team from history entries
    def mmr_delta_for_match(m: Match) -> Optional[int]:
        if not m.completed or not m.score:
            return None
        opp = m.team_b if m.team_a == team.name else m.team_a
        a_sets, b_sets = m.score
        if m.team_a == team.name:
            my_sets, opp_sets = a_sets, b_sets
        else:
            my_sets, opp_sets = b_sets, a_sets
        won = my_sets > opp_sets
        phrase = f"Won {my_sets}-{opp_sets} vs {opp}" if won else f"Lost {my_sets}-{opp_sets} vs {opp}"
        for entry in reversed(team.history or []):
            if phrase in entry:
                mobj = re.search(r"([+-]\\d+)\\s*MMR", entry)
                if mobj:
                    try:
                        return int(mobj.group(1))
                    except Exception:
                        return None
        return None

    mmr_changes: Dict[str, Optional[int]] = {m.match_id: mmr_delta_for_match(m) for m in team_matches}

    return render_template('team_detail.html', team=team, matches=team_matches, display_mmr=display_mmr, win_rate=win_rate, avg_points=avg_points, rank=rank, placement_matches=mmr_system.placement_matches, mmr_changes=mmr_changes)

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
    return render_template('manage_teams.html', teams=teams, base_mmr=BASE_MMR)

@app.route('/generate_matches', methods=['GET', 'POST'])
@admin_required
def generate_matches():
    """Deprecated: redirect to Manage Matches which now handles preview/publish."""
    return redirect(url_for('manage_matches'))

@app.route('/manage_matches')
@admin_required
def manage_matches():
    """Manage existing matches and generate new ones via preview/publish."""
    matches = mmr_system.matches
    active_teams = [t.name for t in mmr_system.teams if t.active]
    return render_template('manage_matches.html', matches=matches, active_teams=active_teams, current_week=mmr_system.current_week)

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
        # Backward compatible: prefer new fields, fallback to legacy
        display_names = request.form.getlist('player_display_names[]') or request.form.getlist('player_names[]')
        roblox_usernames = request.form.getlist('player_roblox_usernames[]') or []
        discord_ids = request.form.getlist('player_discord_ids[]') or []
        player_roles = request.form.getlist('player_roles[]') or []

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
        # Build roster using new schema
        roster = []
        max_len = len(display_names)
        for i in range(max_len):
            dn = (display_names[i] if i < len(display_names) else '') or ''
            rb = (roblox_usernames[i] if i < len(roblox_usernames) else '') or ''
            did = (discord_ids[i] if i < len(discord_ids) else '') or ''
            rl = (player_roles[i] if i < len(player_roles) else '') or ''
            dn = dn.strip()
            rb = rb.strip()
            did = did.strip()
            rl = rl.strip()
            if dn:
                roster.append({
                    'display_name': dn,
                    'name': dn,  # legacy alias
                    'roblox_username': rb,
                    'discord_id': did,
                    'role': rl,
                    'matches_played': 0
                })
        new_team.roster = roster

        mmr_system.teams.append(new_team)
        mmr_system.save_data()

        flash(f'Team {team_name} created successfully!', 'success')
        return redirect(url_for('manage_teams'))

    return render_template('create_team.html')

@app.route('/match/<match_id>')
def match_detail(match_id):
    """Show detailed information about a specific match"""
    match = next((m for m in mmr_system.matches if m.match_id == match_id), None)
    if not match:
        flash('Match not found!', 'error')
        return redirect(url_for('matches'))
    # Resolve team objects
    team_a = next((t for t in mmr_system.teams if t.name == match.team_a), None)
    team_b = next((t for t in mmr_system.teams if t.name == match.team_b), None)

    def extract_mmr_delta(team: Team, opponent_name: str, score: Optional[Tuple[int,int]]) -> Optional[int]:
        if not team or not score:
            return None
        a_sets, b_sets = score
        # Determine expected history phrase for Win/Loss against opponent
        won = (team.name == match.team_a and a_sets > b_sets) or (team.name == match.team_b and b_sets > a_sets)
        if team.name == match.team_a:
            my_sets, opp_sets = a_sets, b_sets
        else:
            my_sets, opp_sets = b_sets, a_sets
        phrase = f"Won {my_sets}-{opp_sets} vs {opponent_name}" if won else f"Lost {my_sets}-{opp_sets} vs {opponent_name}"
        for entry in reversed(team.history or []):
            if phrase in entry:
                m = re.search(r"([+-]\\d+)\\s*MMR", entry)
                if m:
                    try:
                        return int(m.group(1))
                    except Exception:
                        return None
        return None

    mmr_a_delta = extract_mmr_delta(team_a, match.team_b, match.score) if match.completed else None
    mmr_b_delta = extract_mmr_delta(team_b, match.team_a, match.score) if match.completed else None

    return render_template('match_detail.html', match=match, team_a=team_a, team_b=team_b, mmr_a_delta=mmr_a_delta, mmr_b_delta=mmr_b_delta)

@app.route('/input_result/<match_id>', methods=['GET', 'POST'])
@admin_required
def input_result(match_id):
    """Input or update match result"""
    match = next((m for m in mmr_system.matches if m.match_id == match_id), None)
    if not match:
        flash('Match not found!', 'error')
        return redirect(url_for('matches'))

    # Resolve team objects for template visuals
    team_a_obj = next((t for t in mmr_system.teams if t.name == match.team_a), None)
    team_b_obj = next((t for t in mmr_system.teams if t.name == match.team_b), None)

    if request.method == 'POST':
        try:
            score_a = int(request.form.get('score_a', 0))
            score_b = int(request.form.get('score_b', 0))
        except ValueError:
            flash('Invalid set win totals provided.', 'error')
            return render_template('input_result.html', match=match, team_a=team_a_obj, team_b=team_b_obj)

        # Basic validation for sets won (best of 5)
        if not (0 <= score_a <= 3 and 0 <= score_b <= 3 and max(score_a, score_b) == 3 and 3 <= (score_a + score_b) <= 5):
            flash('Sets won must be between 0 and 3 with one team reaching 3 (best of 5).', 'error')
            return render_template('input_result.html', match=match, team_a=team_a_obj, team_b=team_b_obj)

        # Collect and validate set scores from form set_1_a/set_1_b ... set_5_a/set_5_b
        set_scores: List[str] = []
        totals_a = 0
        totals_b = 0
        for i in range(1, 5 + 1):
            a = request.form.get(f'set_{i}_a')
            b = request.form.get(f'set_{i}_b')
            if a is None and b is None:
                continue
            if a == '' and b == '':
                continue
            try:
                ai = int(a) if a not in (None, '') else None
                bi = int(b) if b not in (None, '') else None
            except Exception:
                flash(f'Invalid score in Set {i}. Please enter numbers only.', 'error')
                return render_template('input_result.html', match=match, team_a=team_a_obj, team_b=team_b_obj)

            if ai is None or bi is None:
                flash(f'Set {i} is incomplete. Provide both scores or leave both blank.', 'error')
                return render_template('input_result.html', match=match, team_a=team_a_obj, team_b=team_b_obj)

            # Allow any non-negative integer values (no upper cap)
            if ai < 0 or bi < 0:
                flash(f'Set {i} scores must be non-negative integers.', 'error')
                return render_template('input_result.html', match=match, team_a=team_a_obj, team_b=team_b_obj)

            set_scores.append(f"{ai}:{bi}")
            totals_a += ai
            totals_b += bi

        # Persist match result first
        match.score = (score_a, score_b)
        match.set_scores = set_scores
        match.completed = True
        match.timestamp = datetime.now().isoformat()

        # Recalculate from full history to avoid double-counting on edits
        mmr_system.recalculate_all_mmr()

        # Notify Discord webhook about the result (best-effort)
        notify_discord_match_result(match, score_a, score_b, set_scores)

        flash('Match result updated successfully.', 'success')
        return redirect(url_for('matches'))

    # GET
    return render_template('input_result.html', match=match, team_a=team_a_obj, team_b=team_b_obj)

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
    # Recalculate MMR to reflect the deletion
    try:
        mmr_system.recalculate_all_mmr()
    except Exception:
        logging.exception('Failed to recalculate after match deletion')
    flash('Match deleted successfully.', 'success')
    # Prefer navigating back to where the deletion was initiated
    return redirect(request.referrer or url_for('manage_matches'))

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

@app.route('/match_setup')
@admin_required
def match_setup():
    """Legacy endpoint now redirects to Manage Matches UI."""
    return redirect(url_for('manage_matches'))

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
    # New fields, with backward-compatible fallback
    display_name = (request.form.get('display_name') or request.form.get('player_name') or '').strip()
    role = (request.form.get('role') or '').strip()
    roblox_username = (request.form.get('roblox_username') or '').strip()
    discord_id = (request.form.get('discord_id') or '').strip()

    if not display_name:
        flash('Display name is required.', 'error')
        return redirect(url_for('manage_teams'))
    if team.roster is None:
        team.roster = []

    # Avoid duplicates: prefer discord_id uniqueness when provided, else by display name
    if discord_id and any((p or {}).get('discord_id') == discord_id for p in team.roster):
        flash('A player with this Discord ID already exists on the roster.', 'error')
        return redirect(url_for('manage_teams'))
    if any(((p or {}).get('display_name') or (p or {}).get('name', '')).lower() == display_name.lower() for p in team.roster):
        flash('Player already in roster.', 'error')
        return redirect(url_for('manage_teams'))

    team.roster.append({
        'display_name': display_name,
        'name': display_name,  # legacy alias
        'roblox_username': roblox_username,
        'discord_id': discord_id,
        'role': role,
        'matches_played': 0
    })
    mmr_system.save_data()
    flash('Player added.', 'success')
    return redirect(url_for('manage_teams'))

@app.route('/admin/edit_player/<team_name>', methods=['POST'])
@admin_required
def edit_player(team_name):
    """Edit an existing player on a team's roster.
    Identifies the player using original_name and optional original_discord_id.
    Allows updating: display_name, discord_id, roblox_username, role.
    """
    team = next((t for t in mmr_system.teams if t.name == team_name), None)
    if not team:
        flash('Team not found.', 'error')
        return redirect(url_for('manage_teams'))

    original_name = (request.form.get('original_name') or '').strip()
    original_discord_id = (request.form.get('original_discord_id') or '').strip()

    new_display_name = (request.form.get('display_name') or '').strip()
    new_discord_id = (request.form.get('discord_id') or '').strip()
    new_roblox_username = (request.form.get('roblox_username') or '').strip()
    new_role = (request.form.get('role') or '').strip()

    if not new_display_name:
        flash('Display name is required.', 'error')
        return redirect(url_for('manage_teams'))

    roster = team.roster or []
    # Find the player: prefer matching both name/display_name and discord_id if provided
    idx = None
    for i, p in enumerate(roster):
        name_val = (p or {}).get('display_name') or (p or {}).get('name') or ''
        did_val = (p or {}).get('discord_id') or ''
        if original_discord_id:
            if name_val == original_name and did_val == original_discord_id:
                idx = i
                break
        else:
            if name_val == original_name:
                idx = i
                break

    if idx is None:
        flash('Player not found on roster.', 'error')
        return redirect(url_for('manage_teams'))

    # Duplicate checks (ignore self):
    for j, p in enumerate(roster):
        if j == idx:
            continue
        other_name = ((p or {}).get('display_name') or (p or {}).get('name') or '').strip().lower()
        if other_name and other_name == new_display_name.lower():
            flash('Another player already has this display name on the roster.', 'error')
            return redirect(url_for('manage_teams'))
        if new_discord_id and (p or {}).get('discord_id') == new_discord_id:
            flash('Another player already uses this Discord ID on the roster.', 'error')
            return redirect(url_for('manage_teams'))

    # Update fields
    roster[idx]['display_name'] = new_display_name
    roster[idx]['name'] = new_display_name  # legacy alias for compatibility
    roster[idx]['discord_id'] = new_discord_id
    roster[idx]['roblox_username'] = new_roblox_username
    roster[idx]['role'] = new_role

    team.roster = roster
    mmr_system.save_data()
    flash('Player updated.', 'success')
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
    """Import a new team from pasted JSON in admin panel modal.
    Accepts:
      - A single team object
      - An array of team objects
      - An object with a "teams" array
    Player objects support flexible keys and are normalized to:
      display_name, name (legacy alias), roblox_username, role, matches_played
    """
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

    def _first_nonempty(d: dict, keys: list[str]) -> str:
        for k in keys:
            v = d.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ''

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
                # Normalize player fields
                dn = _first_nonempty(p, ['display_name', 'name', 'displayName'])
                if not dn:
                    continue
                rb = _first_nonempty(p, ['roblox_username', 'roblox', 'roblox_id', 'robloxId', 'robloxUsername'])
                rl = _first_nonempty(p, ['role', 'position'])
                try:
                    mp = int(p.get('matches_played') or 0)
                except Exception:
                    mp = 0
                cleaned.append({
                    'display_name': dn,
                    'name': dn,  # legacy alias
                    'roblox_username': rb,
                    'role': rl,
                    'matches_played': mp
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

# Removed duplicate '/match_setup' route that conflicted with the legacy redirect.

@app.route('/admin/match_setup/preview', methods=['POST'])
@admin_required
def admin_match_setup_preview():
    """Return a non-persisted preview of matchups for the current week."""
    # Accept JSON or form-encoded
    payload = request.get_json(silent=True) or request.form
    try:
        matches_per_team = int(payload.get('matches_per_team', 1))
    except Exception:
        return jsonify({
            'success': False,
            'message': 'Invalid matches_per_team'
        }), 400
    preview = mmr_system.generate_weekly_matches_preview(matches_per_team)
    # Return simple pairs for UI
    return jsonify({
        'success': True,
        'week': mmr_system.current_week,
        'matches': [{'team_a': m.team_a, 'team_b': m.team_b} for m in preview]
    })

@app.route('/admin/match_setup/commit', methods=['POST'])
@admin_required
def admin_match_setup_commit():
    """Persist curated matches for the current week."""
    data = request.get_json(silent=True)
    if not data or 'matches' not in data or not isinstance(data['matches'], list):
        return jsonify({'success': False, 'message': 'Request must include a matches array.'}), 400

    active_names = {t.name for t in mmr_system.teams if t.active}
    created = 0
    seen_pairs = set()  # prevent duplicates within payload (unordered)
    # Build forbidden pairs from all historical matches
    forbidden_pairs = {frozenset({m.team_a, m.team_b}) for m in mmr_system.matches}

    new_matches: List[Match] = []
    errors: List[str] = []

    for idx, item in enumerate(data['matches'], start=1):
        try:
            a = (item.get('team_a') or '').strip()
            b = (item.get('team_b') or '').strip()
        except Exception:
            errors.append(f'Row {idx}: invalid payload object')
            continue
        # Basic validation
        if not a or not b:
            errors.append(f'Row {idx}: team names required')
            continue
        if a == b:
            errors.append(f'Row {idx}: a team cannot play itself ({a})')
            continue
        if a not in active_names:
            errors.append(f'Row {idx}: unknown or inactive team A: {a}')
            continue
        if b not in active_names:
            errors.append(f'Row {idx}: unknown or inactive team B: {b}')
            continue
        pair = frozenset({a, b})
        if pair in seen_pairs:
            errors.append(f'Row {idx}: duplicate pair in submission: {a} vs {b}')
            continue
        if pair in forbidden_pairs:
            errors.append(f'Row {idx}: pair has already played before: {a} vs {b}')
            continue
        seen_pairs.add(pair)
        new_matches.append(Match(a, b, mmr_system.current_week))

    if errors:
        return jsonify({'success': False, 'message': 'Validation failed', 'errors': errors}), 400

    if not new_matches:
        return jsonify({'success': False, 'message': 'No valid matches to create.'}), 400

    # Persist
    mmr_system.matches.extend(new_matches)
    mmr_system.save_data()
    created = len(new_matches)

    # Announce schedule via Discord webhook (best-effort)
    try:
        notify_discord_week_schedule(mmr_system.current_week, new_matches)
    except Exception:
        logging.warning('Weekly schedule webhook failed unexpectedly.')

    return jsonify({'success': True, 'created': created, 'week': mmr_system.current_week})

@app.route('/admin/update_roster/<team_name>', methods=['POST'])
@admin_required
def update_roster(team_name):
    """Bulk update a team's roster from a single form submission.
    Accepts parallel arrays: original_names[], display_names[], roblox_usernames[], roles[].
    Rows with empty original_name and non-empty display_name are treated as additions.
    Players missing from submission are removed.
    """
    team = next((t for t in mmr_system.teams if t.name == team_name), None)
    if not team:
        flash('Team not found.', 'error')
        return redirect(url_for('manage_teams'))

    # Gather form arrays
    original_names = [ (s or '').strip() for s in request.form.getlist('original_names[]') ]
    display_names = [ (s or '').strip() for s in request.form.getlist('display_names[]') ]
    roblox_usernames = [ (s or '').strip() for s in request.form.getlist('roblox_usernames[]') ]
    roles = [ (s or '').strip() for s in request.form.getlist('roles[]') ]

    n = max(len(original_names), len(display_names), len(roblox_usernames), len(roles))
    # Normalize lengths
    while len(original_names) < n: original_names.append('')
    while len(display_names) < n: display_names.append('')
    while len(roblox_usernames) < n: roblox_usernames.append('')
    while len(roles) < n: roles.append('')

    # Map existing players by their current display/name
    existing_by_name = {}
    for p in (team.roster or []):
        key = (p or {}).get('display_name') or (p or {}).get('name') or ''
        if key:
            existing_by_name[key] = p

    new_roster = []
    seen = set()

    for i in range(n):
        orig = original_names[i]
        dn = display_names[i]
        rb = roblox_usernames[i]
        rl = roles[i]

        # Skip completely empty rows
        if not orig and not dn and not rb and not rl:
            continue

        if not dn:
            flash('Display name cannot be blank for roster rows.', 'error')
            return redirect(url_for('manage_teams'))

        key_lower = dn.lower()
        if key_lower in seen:
            flash('Duplicate display names in submission.', 'error')
            return redirect(url_for('manage_teams'))
        seen.add(key_lower)

        base = existing_by_name.get(orig, {}) if orig else {}
        new_entry = {
            'display_name': dn,
            'name': dn,  # legacy alias
            'roblox_username': rb,
            'role': rl,
            'matches_played': int((base or {}).get('matches_played') or 0),
            'discord_id': (base or {}).get('discord_id') or ''
        }
        new_roster.append(new_entry)

    # Apply and persist
    team.roster = new_roster
    mmr_system.save_data()

    flash('Roster updated.', 'success')
    return redirect(url_for('manage_teams'))

@app.route('/admin/notify_leaderboard', methods=['POST'])
@admin_required
def notify_leaderboard():
    """Send current leaderboard to Discord webhook."""
    try:
        lb = mmr_system.get_leaderboard()
        if not lb:
            flash('No teams to announce.', 'warning')
            return redirect(url_for('admin_panel'))
        leaderboard_url = request.url_root.rstrip('/') + url_for('leaderboard')
        # Build top 5 lines
        top = lb[:5]
        lines = [f"{i+1}. {t.name} — {t.mmr} MMR" for i, t in enumerate(top)]
        # Style using #1 team color & logo
        top_team = lb[0]
        color = _parse_hex_color(getattr(top_team, 'hexcolor', None), 0x5865F2)
        thumbnail = getattr(top_team, 'logo', '')

        embed = {
            'title': f"Leaderboard - Week {mmr_system.current_week}",
            'description': '\n'.join(lines) + f"\n\n[View Full Leaderboard]({leaderboard_url})",
            'color': color,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'footer': {'text': 'MMR System'}
        }
        if thumbnail:
            embed['thumbnail'] = {'url': thumbnail}

        _post_discord_embed(embed)
        flash('Leaderboard webhook sent.', 'success')
    except Exception as e:
        logging.exception('Failed to send leaderboard webhook')
        flash(f'Failed to send leaderboard webhook: {e}', 'error')
    return redirect(url_for('admin_panel'))

@app.route('/admin/update_branding/<team_name>', methods=['POST'])
@admin_required
def update_branding(team_name):
    """Update a team's branding: hex color and logo URL."""
    team = next((t for t in mmr_system.teams if t.name == team_name), None)
    if not team:
        flash('Team not found.', 'error')
        return redirect(url_for('manage_teams'))

    hex_in = (request.form.get('hexcolor') or '').strip()
    logo_in = (request.form.get('logo') or '').strip()

    # Normalize hex color to #RRGGBB
    import re as _re
    hex_out = '#374151'
    if hex_in:
        m = _re.fullmatch(r'#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})', hex_in)
        if m:
            h = m.group(1)
            if len(h) == 3:
                h = ''.join(ch * 2 for ch in h)
            hex_out = '#' + h.lower()
        else:
            flash('Invalid hex color. Using default.', 'warning')
    else:
        hex_out = getattr(team, 'hexcolor', '#374151') or '#374151'

    # Basic logo URL validation (optional)
    if logo_in and not (logo_in.startswith('http://') or logo_in.startswith('https://')):
        flash('Logo must be an http(s) URL.', 'error')
        return redirect(url_for('manage_teams'))

    team.hexcolor = hex_out
    team.logo = logo_in
    mmr_system.save_data()
    flash('Branding updated.', 'success')
    return redirect(url_for('manage_teams'))

if __name__ == '__main__':
    # In production use gunicorn. This block is for local/dev usage.
    debug = os.getenv('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes')
    port = int(os.getenv('PORT', 5001))
    host = os.getenv('HOST', '0.0.0.0')
    app.run(debug=debug, host=host, port=port)
