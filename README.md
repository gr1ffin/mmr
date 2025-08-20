# MMR Competitive Ladder System

A comprehensive Matchmaking Rating (MMR) system for competitive ladders with a modern web interface.

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
