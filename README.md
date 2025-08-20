# MMR Competitive Ladder System

Production-ready Flask app with SQLite (SQLAlchemy), systemd, and Nginx. This README covers local development on macOS and full production operations on an Ubuntu VPS for gr1ffin.com, including how to start/stop the service and push/pull code on the server.

## Features

### 🏆 Core MMR System
- **Elo-based rating system** with customizable K-factor
- **Margin bonuses** for dominant victories (3-0, 3-1, 3-2)
- **Point differential bonuses** for comprehensive wins
- **Weekly match scheduling** with persistent data
- **Inactivity penalties** to maintain competitive integrity

### 🌐 Web Interface
- **Real-time leaderboard** with team rankings
- **Match management** - generate, view, and input results
- **Team profiles** with detailed match history
- **Responsive design** using Bootstrap 5
- **Interactive forms** for easy data entry

### 💾 Data Persistence
- **JSON storage** for teams and matches
- **Weekly data snapshots** for historical tracking
- **Automatic backups** between program runs
- **Export capabilities** for external analysis

## Quick Start

### 1. Install Dependencies
```bash
pip3 install Flask
```

### 2. Run the System
```bash
# First time setup (creates sample teams and matches)
python3 mmr_system.py

# Start web interface
python3 web_interface.py
```

### 3. Access Web Interface
Open your browser and go to: `http://localhost:5000`

## System Architecture

### Core Classes

#### Team Class
```python
class Team:
    name: str           # Team name
    mmr: int            # Current MMR rating (default: 1000)
    matches_played: int # Total matches completed
    active: bool        # Team status
    history: List[str]  # Match result log
```

#### Match Class
```python
class Match:
    match_id: str       # Unique identifier
    team_a: str         # First team name
    team_b: str         # Second team name
    week: int           # Week number
    score: Tuple[int, int]      # Games won (A, B)
    point_differential: Tuple[int, int]  # Total points (A, B)
    completed: bool     # Match status
    timestamp: str      # Completion time
```

### MMR Calculation Formula

The enhanced MMR system includes:

1. **Base Elo calculation**: `1 / (1 + 10^((opp_mmr - team_mmr) / 400))`
2. **Margin bonuses**: 
   - 3-0 victory: +5 MMR
   - 3-1 victory: +3 MMR  
   - 3-2 victory: +1 MMR
3. **Point differential bonus**: `(winner_points - loser_points) × 0.1`
4. **K-factor**: 20 (configurable)

## Web Interface Usage

### Dashboard (`/`)
- **Current standings** (top 5 teams)
- **Recent matches** overview
- **Quick actions** for common tasks
- **Week status** information

### Leaderboard (`/leaderboard`)
- **Complete team rankings** by MMR
- **Team statistics** and history
- **Interactive modals** for match details
- **Performance metrics**

### Matches (`/matches`)
- **Weekly match organization**
- **Match status tracking**
- **Result input forms**
- **Historical match data**

### Teams (`/teams`)
- **Individual team profiles**
- **Match history timelines**
- **Performance analytics**
- **Status management**

## Data Management

### File Structure
```
mmr/
├── mmr_system.py      # Core MMR logic
├── web_interface.py   # Flask web app
├── teams.json         # Team data storage
├── matches.json       # Match data storage
├── templates/         # HTML templates
└── requirements.txt   # Python dependencies
```

### Data Persistence
- **teams.json**: Stores all team information and MMR data
- **matches.json**: Stores match schedules and results
- **Automatic saving** after every operation
- **Data integrity** checks and error handling

## Configuration

### Constants (mmr_system.py)
```python
BASE_MMR = 1000              # Starting MMR for new teams
PLACEMENT_MATCHES = 3        # Matches before MMR stabilization
K_FACTOR = 20                # MMR change multiplier
CHALLENGE_MULTIPLIER = 0.5   # Challenge match impact
INACTIVITY_PENALTY = 10      # MMR penalty per inactive week
MARGIN_BONUS = {(3,0):5, (3,1):3, (3,2):1}  # Victory bonuses
POINT_DIFF_MULTIPLIER = 0.1 # Point differential bonus
```

## API Endpoints

### RESTful API
- `GET /api/teams` - Get all teams data
- `GET /api/matches` - Get all matches data
- `GET /match/<id>` - Get specific match details
- `GET /team/<name>` - Get specific team details

## Usage Examples

### Generate Weekly Matches
1. Navigate to "Generate Matches" in the web interface
2. Enter number of matches (e.g., 10)
3. System creates random team pairings for the current week

### Input Match Results
1. Go to "Matches" page
2. Click "Input Result" for a pending match
3. Enter games won and total points for both teams
4. Submit to update MMR and mark match complete

### View Team Performance
1. Click on any team name in the leaderboard
2. View detailed match history
3. See MMR progression over time
4. Analyze performance trends

## Advanced Features

### Inactivity Management
- **Automatic penalties** for inactive teams
- **Configurable penalty rates** per week
- **Team status tracking** (Active/Inactive)

### Match Types
- **Regular season matches** (full MMR impact)
- **Challenge matches** (reduced K-factor)
- **Placement matches** (special handling)

### Data Export
- **JSON format** for external analysis
- **Historical snapshots** by week
- **Team performance** tracking over time

## Troubleshooting

### Common Issues
1. **Port already in use**: Change port in `web_interface.py`
2. **Data corruption**: Delete JSON files to reset
3. **Template errors**: Ensure Flask is properly installed

### Performance Tips
- **Large datasets**: Consider database migration for 100+ teams
- **Memory usage**: Monitor with large match histories
- **Backup strategy**: Regular JSON file backups

## Future Enhancements

### Planned Features
- **Database integration** (PostgreSQL/MySQL)
- **Real-time updates** with WebSockets
- **Advanced analytics** and charts
- **Team management** interface
- **Season management** and playoffs
- **API authentication** and rate limiting

### Customization Options
- **Multiple game types** support
- **Custom MMR formulas**
- **Tournament brackets**
- **Team seeding** algorithms

## Contributing

1. Fork the repository
2. Create feature branch
3. Implement changes with tests
4. Submit pull request

## License

This project is open source and available under the MIT License.

---

**Built with Python, Flask, and Bootstrap 5** 🚀

## Workspace layout (repo root)

```
discordbot.py
matches.json
mmr_config.json
mmr_system.py
mmr.db
mmr.db-shm
mmr.db-wal
players.json
README.md
requirements.txt
storage.py
teams.json
test_input.inp
web_interface.py
__pycache__/
deploy/
  mmr.env.example
  mmr.service
  nginx.gr1ffin.com
exports/
templates/
```

Key files
- web_interface.py: Flask app entry (exported as app for Gunicorn)
- mmr_system.py: MMR engine + SQLite persistence (SQLAlchemy)
- deploy/mmr.service: systemd unit for Gunicorn
- deploy/nginx.gr1ffin.com: Nginx server block template for gr1ffin.com
- deploy/mmr.env.example: Environment file template for production

## Configuration (env)
Create /etc/mmr.env on the server (copy from deploy/mmr.env.example and edit):
- SECRET_KEY: strong random hex (openssl rand -hex 32)
- ADMIN_PASSWORD: strong password for /admin_login
- MMR_DB_PATH: full path to SQLite DB (e.g., /var/lib/mmr/mmr.db)
- DISCORD_WEBHOOK_URL: optional
- SESSION_COOKIE_SECURE=true, SESSION_COOKIE_SAMESITE=Lax
- PREFERRED_URL_SCHEME=https
- FLASK_DEBUG=false

The app honors MMR_DB_PATH for the DB location and uses ProxyFix to work behind Nginx. Backups/exports are written under /opt/mmr by default.

## Local development (macOS)
- Create and activate a venv (macOS, Python 3.11+ recommended):
  - python3 -m venv .venv
  - source .venv/bin/activate
- Install deps:
  - pip install -U pip
  - pip install -r requirements.txt
- Run the dev server:
  - FLASK_DEBUG=1 HOST=0.0.0.0 PORT=5001 python web_interface.py
- Optional: use a custom DB path locally:
  - export MMR_DB_PATH="$PWD/mmr.db"

Visit http://localhost:5001 and login at /admin_login with ADMIN_PASSWORD.

## Production deployment (Ubuntu VPS)
Assumptions
- Code lives at /opt/mmr (repo root)
- Virtualenv at /opt/mmr/venv
- Database at /var/lib/mmr/mmr.db
- Env file at /etc/mmr.env
- Service name mmr, domain gr1ffin.com

One-time setup
- Create directories and permissions:
  - sudo mkdir -p /opt/mmr /opt/mmr/exports /opt/mmr/backups /var/lib/mmr
  - sudo chown -R griffin:www-data /opt/mmr /var/lib/mmr
  - sudo chmod 750 /opt/mmr && sudo chmod 770 /opt/mmr/exports /opt/mmr/backups /var/lib/mmr
- Create venv and install deps:
  - python3 -m venv /opt/mmr/venv
  - /opt/mmr/venv/bin/pip install -U pip
  - /opt/mmr/venv/bin/pip install -r /opt/mmr/requirements.txt
- Systemd unit:
  - sudo cp /opt/mmr/deploy/mmr.service /etc/systemd/system/mmr.service
- Nginx site:
  - sudo cp /opt/mmr/deploy/nginx.gr1ffin.com /etc/nginx/sites-available/gr1ffin.com
  - sudo ln -sf /etc/nginx/sites-available/gr1ffin.com /etc/nginx/sites-enabled/gr1ffin.com
  - sudo rm -f /etc/nginx/sites-enabled/default
  - sudo nginx -t && sudo systemctl reload nginx
- HTTPS (Let’s Encrypt):
  - sudo certbot --nginx -d gr1ffin.com -d www.gr1ffin.com --redirect -m you@example.com -n --agree-tos

Start/Stop/Status (server)
- Start: sudo systemctl start mmr
- Stop: sudo systemctl stop mmr
- Restart: sudo systemctl restart mmr
- Enable on boot: sudo systemctl enable mmr
- Disable on boot: sudo systemctl disable mmr
- Status: systemctl status mmr --no-pager
- Logs (if journal isn’t persistent, use syslog):
  - sudo journalctl -u mmr -e --no-pager
  - sudo tail -n 200 /var/log/syslog | grep -i gunicorn

Upstream (Gunicorn) quick test
- curl -I http://127.0.0.1:8000

Nginx quick tests
- curl -I http://gr1ffin.com
- curl -I https://gr1ffin.com

## Deploying code from GitHub (on the server)
Repo location: /opt/mmr
Branch strategy: LIVE is the deployed branch

Pull latest LIVE and restart
- cd /opt/mmr
- git fetch --all --tags
- git switch LIVE
- git pull --ff-only
- /opt/mmr/venv/bin/pip install -r requirements.txt
- sudo systemctl restart mmr

If local changes block pull
- git stash --include-untracked
- git pull --ff-only
- git stash pop (resolve as needed)

Force match remote (discard local changes)
- git reset --hard origin/LIVE

## Pushing changes from the server to GitHub
Prepare SSH for GitHub (one time)
- ssh-keygen -t ed25519 -C "deploy@gr1ffin.com" -f ~/.ssh/github_ed25519
- Add ~/.ssh/github_ed25519.pub to your GitHub account (SSH keys) or as a deploy key with write access
- Create ~/.ssh/config with:
  - Host github.com
  -   HostName github.com
  -   User git
  -   IdentityFile ~/.ssh/github_ed25519
  -   IdentitiesOnly yes
- Test: ssh -T git@github.com (should say auth succeeded, no shell access)

Ensure remote and branch
- cd /opt/mmr
- git remote set-url origin git@github.com:gr1ffin/mmr.git
- git switch -c LIVE 2>/dev/null || git switch LIVE

Commit and push
- git add -A
- git commit -m "Server changes"
- git push -u origin LIVE

Note: Do not commit /etc/mmr.env. The repo’s .gitignore only ignores .env files; everything else (including mmr.db) is tracked.

## Database operations
Live DB path: set by MMR_DB_PATH (e.g., /var/lib/mmr/mmr.db).

Copy live DB into the repo (to commit or inspect)
- Safest with a brief stop:
  - sudo systemctl stop mmr
  - cp /var/lib/mmr/mmr.db /opt/mmr/mmr.db
  - sudo chown griffin:www-data /opt/mmr/mmr.db
  - sudo systemctl start mmr
- Or hot backup (no stop):
  - sqlite3 /var/lib/mmr/mmr.db ".backup '/opt/mmr/mmr.db'"

Replace live DB with repo DB (overwrite server data)
- sudo systemctl stop mmr
- cp /opt/mmr/mmr.db /var/lib/mmr/mmr.db
- sudo chown griffin:www-data /var/lib/mmr/mmr.db
- sudo chmod 660 /var/lib/mmr/mmr.db
- sudo systemctl start mmr

Integrity check (SQLite)
- sqlite3 /var/lib/mmr/mmr.db "PRAGMA integrity_check;"

## Troubleshooting
- 502/Bad Gateway: check Gunicorn service and upstream port 8000; systemctl status mmr; curl 127.0.0.1:8000
- Certbot unauthorized: ensure DNS A for gr1ffin.com and www points to your Droplet IP; retry certbot
- Journal missing: enable persistent journaling or use syslog tail
- Permissions: ensure /opt/mmr and /var/lib/mmr are writable by the service user (griffin or www-data) and that /etc/mmr.env is readable (640)

## Security notes
- Keep ADMIN_PASSWORD and SECRET_KEY only in /etc/mmr.env
- Keep SESSION_COOKIE_SECURE=true in production (requires HTTPS)
- Do not run Gunicorn as root; the provided service runs as griffin with group www-data

## License
MIT
