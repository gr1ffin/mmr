import json
import os
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import uuid
import logging

# New: SQLAlchemy for SQLite persistence
from sqlalchemy import create_engine, Column, Integer, String, Boolean, JSON, Text, Index, Float
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.exc import OperationalError

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# Constants
BASE_MMR = 1000
PLACEMENT_MATCHES = 3
K_FACTOR = 20
CHALLENGE_MULTIPLIER = 0.5
INACTIVITY_PENALTY = 10
MARGIN_BONUS = {(3, 0): 5, (3, 1): 3, (3, 2): 1}
POINT_DIFF_MULTIPLIER = 0.1

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEAMS_PATH = os.path.join(BASE_DIR, 'teams.json')
MATCHES_PATH = os.path.join(BASE_DIR, 'matches.json')
# Allow production to place DB on a persistent path via env
DB_PATH = os.getenv('MMR_DB_PATH', os.path.join(BASE_DIR, 'mmr.db'))
# Ensure the parent directory exists for custom DB paths
try:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
except Exception:
    pass
DB_URL = f"sqlite:///{DB_PATH}"

# ORM base
Base = declarative_base()

class TeamModel(Base):
    __tablename__ = 'teams'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False)
    mmr = Column(Integer, default=BASE_MMR)
    matches_played = Column(Integer, default=0)
    history = Column(JSON, default=list)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    active = Column(Boolean, default=True)
    provisional = Column(Boolean, default=True)
    roster = Column(JSON, default=list)
    logo = Column(Text, default="")
    hexcolor = Column(String, default="#374151")
    created_at = Column(String, default=lambda: datetime.utcnow().isoformat())
    updated_at = Column(String, default=lambda: datetime.utcnow().isoformat())
    __table_args__ = (
        Index('ix_teams_name', 'name'),
        Index('ix_teams_mmr', 'mmr'),
        Index('ix_teams_active', 'active'),
    )

class MatchModel(Base):
    __tablename__ = 'matches'
    match_id = Column(String, primary_key=True)
    team_a = Column(String, nullable=False)
    team_b = Column(String, nullable=False)
    week = Column(Integer, nullable=False)
    score = Column(JSON, nullable=True)  # [a, b]
    set_scores = Column(JSON, nullable=True)  # ["25:20", ...]
    completed = Column(Boolean, default=False)
    scheduled = Column(Boolean, default=False)  # New: for 3-stage system
    scheduled_time = Column(String, nullable=True)  # New: ISO timestamp when match is scheduled
    timestamp = Column(String, nullable=True)
    # New: MMR deltas for each team
    mmr_delta_a = Column(Integer, nullable=True)  # MMR change for team_a
    mmr_delta_b = Column(Integer, nullable=True)  # MMR change for team_b
    __table_args__ = (
        Index('ix_matches_week', 'week'),
        Index('ix_matches_team_a', 'team_a'),
        Index('ix_matches_team_b', 'team_b'),
        Index('ix_matches_pair', 'team_a', 'team_b'),
        Index('ix_matches_completed', 'completed'),
        Index('ix_matches_scheduled', 'scheduled'),
    )

# New: simple key-value settings store
class SettingModel(Base):
    __tablename__ = 'settings'
    key = Column(String, primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(String, default=lambda: datetime.utcnow().isoformat())
    __table_args__ = (
        Index('ix_settings_key', 'key'),
    )

class MMRConfigModel(Base):
    __tablename__ = 'mmr_config'
    id = Column(Integer, primary_key=True, autoincrement=True)
    base_mmr = Column(Integer, default=BASE_MMR)
    placement_matches = Column(Integer, default=PLACEMENT_MATCHES)
    k_factor = Column(Integer, default=K_FACTOR)
    challenge_multiplier = Column(Float, default=CHALLENGE_MULTIPLIER)
    inactivity_penalty = Column(Integer, default=INACTIVITY_PENALTY)
    margin_bonus = Column(JSON, default={"3_0": 5, "3_1": 3, "3_2": 1})
    point_diff_multiplier = Column(Float, default=POINT_DIFF_MULTIPLIER)
    updated_at = Column(String, default=lambda: datetime.utcnow().isoformat())
    __table_args__ = (
        Index('ix_mmr_config_singleton', 'id'),
    )

class Team:
    def __init__(self, name: str, mmr: int = BASE_MMR, logo: Optional[str] = None, hexcolor: Optional[str] = None):
        self.name = name
        # Coerce mmr to int safely
        try:
            self.mmr = int(mmr)
        except (ValueError, TypeError):
            self.mmr = BASE_MMR
        self.matches_played = 0
        self.history = []
        self.wins = 0
        self.losses = 0
        self.active = True
        self.provisional = True  # Hidden MMR until placement matches completed
        self.roster: List[Dict] = []  # Optional roster entries: {name, role, matches_played}
        # New: team logo (URL or path)
        self.logo = logo or ""
        # New: team accent color (hex)
        self.hexcolor = (hexcolor or "#374151")  # default slate-700-ish

    def to_dict(self) -> Dict:
        return {
            'name': self.name,
            'mmr': self.mmr,
            'matches_played': self.matches_played,
            'history': self.history,
            'wins': self.wins,
            'losses': self.losses,
            'active': self.active,
            'provisional': self.provisional,
            'roster': self.roster,
            'logo': self.logo,
            'hexcolor': self.hexcolor,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'Team':
        team = cls(data['name'], data.get('mmr', BASE_MMR), data.get('logo'))
        team.matches_played = data.get('matches_played', 0)
        team.history = data.get('history', [])
        team.wins = data.get('wins', 0)
        team.losses = data.get('losses', 0)
        team.active = data.get('active', True)
        team.provisional = data.get('provisional', True)
        team.roster = data.get('roster', [])
        # Load hexcolor if present, else default
        team.hexcolor = data.get('hexcolor', "#374151")
        return team

class Match:
    def __init__(self, team_a: str, team_b: str, week: int, match_id: str = None):
        self.team_a = team_a
        self.team_b = team_b
        self.week = week
        self.match_id = match_id or str(uuid.uuid4())[:8]
        self.score = None
        self.set_scores = None
        self.completed = False
        self.scheduled = False  # New: for 3-stage system
        self.scheduled_time = None  # New: ISO timestamp when match is scheduled
        self.timestamp = None
        # New: MMR deltas for each team
        self.mmr_delta_a = None  # MMR change for team_a
        self.mmr_delta_b = None  # MMR change for team_b

    def get_status(self) -> str:
        """Return the match status: 'unscheduled', 'scheduled', or 'completed'"""
        if self.completed:
            return 'completed'
        elif self.scheduled:
            return 'scheduled'
        else:
            return 'unscheduled'

    def to_dict(self) -> Dict:
        return {
            'match_id': self.match_id,
            'team_a': self.team_a,
            'team_b': self.team_b,
            'week': self.week,
            'score': self.score,
            'set_scores': self.set_scores,
            'completed': self.completed,
            'scheduled': self.scheduled,
            'scheduled_time': self.scheduled_time,
            'timestamp': self.timestamp,
            'mmr_delta_a': self.mmr_delta_a,
            'mmr_delta_b': self.mmr_delta_b
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'Match':
        match = cls(data['team_a'], data['team_b'], data['week'], data['match_id'])
        match.score = data.get('score')
        match.set_scores = data.get('set_scores')
        match.completed = data.get('completed', False)
        match.scheduled = data.get('scheduled', False)
        match.scheduled_time = data.get('scheduled_time')
        match.timestamp = data.get('timestamp')
        match.mmr_delta_a = data.get('mmr_delta_a')
        match.mmr_delta_b = data.get('mmr_delta_b')
        return match

class MMRSystem:
    def __init__(self):
        self.teams: List[Team] = []
        self.matches: List[Match] = []
        self.current_week = 1
        # Instance-level tunables (default from constants)
        self.k_factor = K_FACTOR
        self.point_diff_multiplier = POINT_DIFF_MULTIPLIER
        self.margin_bonus = MARGIN_BONUS.copy()
        self.inactivity_penalty = INACTIVITY_PENALTY
        self.placement_matches = PLACEMENT_MATCHES  # Teams are provisional until they play this many matches
        # DB
        self.engine = create_engine(
            DB_URL,
            echo=False,
            future=True,
            connect_args={"timeout": 30},
            pool_pre_ping=True,
        )
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False, class_=Session)
        self.has_database = True
        self._setup_sqlite_pragmas()
        self._init_db()
        self._migrate_from_json_if_needed()
        self.load_data()

    def _setup_sqlite_pragmas(self):
        """For SQLite engines, set pragmas to minimize write locks and improve concurrency."""
        try:
            if not DB_URL.startswith("sqlite"):
                return
            from sqlalchemy import event

            def _on_connect(dbapi_conn, _):
                try:
                    cur = dbapi_conn.cursor()
                    # Prefer WAL for fewer writer locks and better durability on macOS
                    cur.execute("PRAGMA journal_mode=WAL;")
                    # Reasonable durability while keeping speed
                    cur.execute("PRAGMA synchronous=NORMAL;")
                    # Ensure long enough busy timeout for short-lived locks (milliseconds)
                    cur.execute("PRAGMA busy_timeout=30000;")
                    # Keep referential integrity if we add FKs later
                    cur.execute("PRAGMA foreign_keys=ON;")
                    cur.close()
                except Exception as e:
                    logging.warning(f"Failed to set SQLite pragmas: {e}")

            event.listen(self.engine, "connect", _on_connect)
        except Exception as e:
            logging.warning(f"Could not attach SQLite pragma listener: {e}")

    def _init_db(self):
        try:
            Base.metadata.create_all(self.engine)
            self._ensure_indexes()
            self._ensure_default_settings()
            # New: ensure mmr_config table is populated from json
            self._sync_mmr_config_from_json()
        except OperationalError as e:
            logging.error(f"Failed to initialize database: {e}")
            self.has_database = False

    def _ensure_default_settings(self):
        """Seed default settings keys if they don't already exist."""
        try:
            if self.get_setting("dashboard_header") is None:
                self.set_setting("dashboard_header", "")
        except Exception as e:
            logging.warning(f"Failed to seed default settings: {e}")

    def _ensure_indexes(self):
        """Create indexes if they don't exist (SQLite-safe)."""
        try:
            with self.engine.connect() as conn:
                conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_teams_name ON teams(name)")
                conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_teams_mmr ON teams(mmr)")
                conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_teams_active ON teams(active)")
                conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_matches_week ON matches(week)")
                conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_matches_team_a ON matches(team_a)")
                conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_matches_team_b ON matches(team_b)")
                conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_matches_pair ON matches(team_a, team_b)")
                conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_matches_completed ON matches(completed)")
                conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_settings_key ON settings(key)")
        except Exception as e:
            logging.warning(f"Index creation failed or not needed: {e}")

    def _migrate_from_json_if_needed(self):
        """If DB is empty and old JSON files exist, import them once."""
        if not self.has_database:
            return
        try:
            with self.SessionLocal() as db:
                has_teams = db.query(TeamModel).limit(1).first() is not None
                has_matches = db.query(MatchModel).limit(1).first() is not None
                if has_teams or has_matches:
                    return
        except Exception:
            return
        legacy_teams = []
        legacy_matches = []
        if os.path.exists(TEAMS_PATH):
            try:
                with open(TEAMS_PATH, 'r') as f:
                    legacy_teams = json.load(f)
            except Exception as e:
                logging.warning(f"Failed to read legacy teams.json: {e}")
        if os.path.exists(MATCHES_PATH):
            try:
                with open(MATCHES_PATH, 'r') as f:
                    legacy_matches = json.load(f)
            except Exception as e:
                logging.warning(f"Failed to read legacy matches.json: {e}")
        if not legacy_teams and not legacy_matches:
            return
        try:
            with self.SessionLocal() as db:
                for t in legacy_teams:
                    tm = TeamModel(
                        name=t.get('name'),
                        mmr=int(t.get('mmr', BASE_MMR) or BASE_MMR),
                        matches_played=int(t.get('matches_played', 0) or 0),
                        history=t.get('history') or [],
                        wins=int(t.get('wins', 0) or 0),
                        losses=int(t.get('losses', 0) or 0),
                        active=bool(t.get('active', True)),
                        provisional=bool(t.get('provisional', True)),
                        roster=t.get('roster') or [],
                        logo=t.get('logo') or "",
                        hexcolor=t.get('hexcolor') or "#374151",
                        created_at=datetime.utcnow().isoformat(),
                        updated_at=datetime.utcnow().isoformat(),
                    )
                    db.add(tm)
                for m in legacy_matches:
                    mm = MatchModel(
                        match_id=m.get('match_id') or str(uuid.uuid4())[:8],
                        team_a=m.get('team_a'),
                        team_b=m.get('team_b'),
                        week=int(m.get('week', 1) or 1),
                        score=m.get('score'),
                        set_scores=m.get('set_scores') or None,
                        completed=bool(m.get('completed', False)),
                        timestamp=m.get('timestamp'),
                    )
                    db.add(mm)
                db.commit()
                logging.info("Migrated legacy JSON data into SQLite database.")
        except Exception as e:
            logging.exception(f"Failed to migrate JSON to DB: {e}")

    def _sync_mmr_config_from_json(self):
        """Read mmr_config.json and upsert into mmr_config table (single row)."""
        if not self.has_database:
            return
        try:
            cfg_path = os.path.join(BASE_DIR, 'mmr_config.json')
            if os.path.exists(cfg_path):
                with open(cfg_path, 'r') as f:
                    cfg = json.load(f)
            else:
                cfg = {
                    "BASE_MMR": BASE_MMR,
                    "PLACEMENT_MATCHES": PLACEMENT_MATCHES,
                    "K_FACTOR": K_FACTOR,
                    "CHALLENGE_MULTIPLIER": CHALLENGE_MULTIPLIER,
                    "INACTIVITY_PENALTY": INACTIVITY_PENALTY,
                    "MARGIN_BONUS": {"3_0": 5, "3_1": 3, "3_2": 1},
                    "POINT_DIFF_MULTIPLIER": POINT_DIFF_MULTIPLIER,
                }
            with self.SessionLocal() as db:
                row = db.query(MMRConfigModel).order_by(MMRConfigModel.id.asc()).first()
                if not row:
                    row = MMRConfigModel()
                    db.add(row)
                # Assign values from cfg with fallbacks
                row.base_mmr = int(cfg.get('BASE_MMR', BASE_MMR) or BASE_MMR)
                row.placement_matches = int(cfg.get('PLACEMENT_MATCHES', PLACEMENT_MATCHES) or PLACEMENT_MATCHES)
                row.k_factor = int(cfg.get('K_FACTOR', K_FACTOR) or K_FACTOR)
                try:
                    row.challenge_multiplier = float(cfg.get('CHALLENGE_MULTIPLIER', CHALLENGE_MULTIPLIER) or CHALLENGE_MULTIPLIER)
                except Exception:
                    row.challenge_multiplier = CHALLENGE_MULTIPLIER
                row.inactivity_penalty = int(cfg.get('INACTIVITY_PENALTY', INACTIVITY_PENALTY) or INACTIVITY_PENALTY)
                mb = cfg.get('MARGIN_BONUS') or {"3_0": 5, "3_1": 3, "3_2": 1}
                # Ensure JSON-serializable dict with string keys
                row.margin_bonus = {
                    str(k): int(v) for k, v in mb.items()
                }
                try:
                    row.point_diff_multiplier = float(cfg.get('POINT_DIFF_MULTIPLIER', POINT_DIFF_MULTIPLIER) or POINT_DIFF_MULTIPLIER)
                except Exception:
                    row.point_diff_multiplier = POINT_DIFF_MULTIPLIER
                row.updated_at = datetime.utcnow().isoformat()
                db.commit()
        except Exception as e:
            logging.warning(f"Failed to sync mmr_config from JSON: {e}")

    def load_data(self):
        self.teams = self._load_teams()
        self.matches = self._load_matches()
        if self.matches:
            self.current_week = max(match.week for match in self.matches) + 1
        for t in self.teams:
            if t.matches_played >= self.placement_matches:
                t.provisional = False
            else:
                t.provisional = True

    def save_data(self):
        self._save_teams()
        self._save_matches()

    def _load_teams(self) -> List[Team]:
        teams: List[Team] = []
        if not self.has_database:
            return teams
        try:
            with self.SessionLocal() as db:
                for tm in db.query(TeamModel).all():
                    t = Team(tm.name, tm.mmr, tm.logo, tm.hexcolor)
                    t.matches_played = tm.matches_played
                    t.history = tm.history or []
                    t.wins = tm.wins
                    t.losses = tm.losses
                    t.active = tm.active
                    t.provisional = tm.provisional
                    t.roster = tm.roster or []
                    teams.append(t)
        except Exception as e:
            logging.exception(f"Failed loading teams from DB: {e}")
        return teams

    def _save_teams(self):
        if not self.has_database:
            return
        try:
            with self.SessionLocal() as db:
                existing = {tm.name: tm for tm in db.query(TeamModel).all()}
                seen = set()
                for t in self.teams:
                    seen.add(t.name)
                    tm = existing.get(t.name)
                    if not tm:
                        tm = TeamModel(name=t.name)
                        db.add(tm)
                    tm.mmr = int(t.mmr)
                    tm.matches_played = int(t.matches_played)
                    tm.history = list(t.history or [])
                    tm.wins = int(t.wins)
                    tm.losses = int(t.losses)
                    tm.active = bool(t.active)
                    tm.provisional = bool(t.provisional)
                    tm.roster = list(t.roster or [])
                    tm.logo = t.logo or ""
                    tm.hexcolor = t.hexcolor or "#374151"
                    tm.updated_at = datetime.utcnow().isoformat()
                for name, tm in existing.items():
                    if name not in seen:
                        db.delete(tm)
                db.commit()
        except Exception as e:
            logging.exception(f"Failed saving teams to DB: {e}")

    def _load_matches(self) -> List[Match]:
        matches: List[Match] = []
        if not self.has_database:
            return matches
        try:
            with self.SessionLocal() as db:
                for mm in db.query(MatchModel).all():
                    m = Match(mm.team_a, mm.team_b, mm.week, mm.match_id)
                    m.score = tuple(mm.score) if mm.score else None
                    m.set_scores = mm.set_scores or None
                    m.completed = bool(mm.completed)
                    m.scheduled = bool(getattr(mm, 'scheduled', False))
                    m.scheduled_time = getattr(mm, 'scheduled_time', None)
                    m.timestamp = mm.timestamp
                    m.mmr_delta_a = mm.mmr_delta_a
                    m.mmr_delta_b = mm.mmr_delta_b
                    matches.append(m)
        except Exception as e:
            logging.exception(f"Failed loading matches from DB: {e}")
        return matches

    def _save_matches(self):
        if not self.has_database:
            return
        try:
            with self.SessionLocal() as db:
                existing = {mm.match_id: mm for mm in db.query(MatchModel).all()}
                seen = set()
                for m in self.matches:
                    seen.add(m.match_id)
                    mm = existing.get(m.match_id)
                    if not mm:
                        mm = MatchModel(match_id=m.match_id)
                        db.add(mm)
                    mm.team_a = m.team_a
                    mm.team_b = m.team_b
                    mm.week = int(m.week)
                    mm.score = list(m.score) if m.score is not None else None
                    mm.set_scores = list(m.set_scores) if m.set_scores else None
                    mm.completed = bool(m.completed)
                    mm.scheduled = bool(m.scheduled)
                    mm.scheduled_time = m.scheduled_time
                    mm.timestamp = m.timestamp
                    mm.mmr_delta_a = m.mmr_delta_a
                    mm.mmr_delta_b = m.mmr_delta_b
                for mid, mm in existing.items():
                    if mid not in seen:
                        db.delete(mm)
                db.commit()
        except Exception as e:
            logging.exception(f"Failed saving matches to DB: {e}")

    def schedule_match(self, match_id: str, scheduled_time: str) -> bool:
        """Schedule a match by setting its scheduled flag and time."""
        match = next((m for m in self.matches if m.match_id == match_id), None)
        if not match:
            return False
        if match.completed:
            return False  # Can't schedule completed matches
        
        match.scheduled = True
        match.scheduled_time = scheduled_time
        self.save_data()
        return True

    def unschedule_match(self, match_id: str) -> bool:
        """Unschedule a match by removing its scheduled flag and time."""
        match = next((m for m in self.matches if m.match_id == match_id), None)
        if not match:
            return False
        if match.completed:
            return False  # Can't unschedule completed matches
        
        match.scheduled = False
        match.scheduled_time = None
        self.save_data()
        return True

    def update_settings(self, k_factor: Optional[int] = None, inactivity_penalty: Optional[int] = None, point_diff_multiplier: Optional[float] = None, margin_bonus: Optional[Dict[Tuple[int,int], int]] = None):
        if k_factor is not None:
            self.k_factor = int(k_factor)
        if inactivity_penalty is not None:
            self.inactivity_penalty = int(inactivity_penalty)
        if point_diff_multiplier is not None:
            self.point_diff_multiplier = float(point_diff_multiplier)
        if margin_bonus is not None:
            self.margin_bonus = dict(margin_bonus)
        logging.info(f"Settings updated: K={self.k_factor}, INACTIVITY={self.inactivity_penalty}, POINT_MULT={self.point_diff_multiplier}, MARGIN_BONUS={self.margin_bonus}")

    def update_mmr(self, winner: Team, loser: Team, score: Tuple[int, int], set_scores: Optional[List[str]] = None, points_winner: Optional[int] = None, points_loser: Optional[int] = None, record_history: bool = True) -> Tuple[int, int]:
        """
        Non-zero-sum MMR system where both teams can gain MMR in close matches.
        Returns (winner_gain, loser_change) where loser_change can be negative or positive.
        """
        # Calculate expected win probability for the winner
        expected_winner = 1 / (1 + 10 ** ((loser.mmr - winner.mmr) / 400))
        
        # Base MMR calculations
        winner_base_gain = self.k_factor * (1 - expected_winner)
        loser_base_change = self.k_factor * (0 - expected_winner)  # Usually negative
        
        # Set margin bonuses
        winner_sets = max(score)
        loser_sets = min(score)
        set_bonus = self.margin_bonus.get((winner_sets, loser_sets), 0)
        
        # Point differential calculations
        if points_winner is None or points_loser is None:
            if set_scores:
                try:
                    points_winner = sum(int(s.split(':')[0]) for s in set_scores)
                    points_loser = sum(int(s.split(':')[1]) for s in set_scores)
                except Exception:
                    points_winner = points_loser = 0
            else:
                points_winner = points_loser = 0
                
        point_diff = max(0, int(points_winner) - int(points_loser))
        point_diff_capped = min(point_diff, 75)
        point_factor = self.point_diff_multiplier * point_diff_capped
        
        # Final MMR changes with non-zero-sum adjustments
        winner_gain = int(round(winner_base_gain + point_factor + set_bonus))
        winner_gain = max(1, winner_gain)  # Winner always gains at least 1
        
        # Loser change calculation - less harsh in close matches
        closeness_factor = 1 - abs(expected_winner - 0.5) * 2  # 0 to 1, higher for closer matches
        loser_change = int(round(loser_base_change * (0.6 + 0.4 * closeness_factor) - point_factor * 0.5))
        
        # In very close matches (within 15 MMR), loser might gain small amount
        mmr_diff = abs(winner.mmr - loser.mmr)
        if mmr_diff <= 15 and winner_sets - loser_sets <= 1:  # Close MMR and close score
            loser_change = max(loser_change, int(winner_gain * 0.2))  # Loser gains 20% of winner's gain
        
        # Apply MMR changes
        winner.mmr = max(0, winner.mmr + winner_gain)
        loser.mmr = max(0, loser.mmr + loser_change)
        
        # Update match statistics
        winner.wins += 1
        loser.losses += 1
        winner.matches_played += 1
        loser.matches_played += 1
        
        # Update provisional status
        if winner.matches_played >= self.placement_matches:
            winner.provisional = False
        if loser.matches_played >= self.placement_matches:
            loser.provisional = False
            
        # Record history
        if record_history:
            winner_change_str = f"+{winner_gain}"
            loser_change_str = f"+{loser_change}" if loser_change >= 0 else str(loser_change)
            winner.history.append(f"Won {winner_sets}-{loser_sets} vs {loser.name} ({winner_change_str} MMR, +{point_diff} pts)")
            loser.history.append(f"Lost {loser_sets}-{winner_sets} vs {winner.name} ({loser_change_str} MMR, -{point_diff} pts)")
            
        return winner_gain, loser_change

    def record_match(self, team_a_name: str, team_b_name: str, score: Tuple[int, int], set_scores: Optional[List[str]] = None):
        team_a = next((t for t in self.teams if t.name == team_a_name), None)
        team_b = next((t for t in self.teams if t.name == team_b_name), None)
        if not team_a or not team_b:
            logging.error("One or both teams not found.")
            return
        winner, loser = (team_a, team_b) if score[0] > score[1] else (team_b, team_a)
        points_a = points_b = None
        if set_scores:
            try:
                totals_a = 0
                totals_b = 0
                for s in set_scores:
                    a_str, b_str = s.split(':')
                    totals_a += int(a_str)
                    totals_b += int(b_str)
                points_a, points_b = totals_a, totals_b
            except Exception:
                points_a = points_b = None
        if points_a is not None and points_b is not None:
            if winner is team_a:
                pw, pl = points_a, points_b
            else:
                pw, pl = points_b, points_a
        else:
            pw = pl = None
        winner_gain, loser_change = self.update_mmr(winner, loser, score, set_scores=set_scores, points_winner=pw, points_loser=pl)
        match = Match(team_a_name, team_b_name, self.current_week)
        match.score = score
        match.set_scores = set_scores
        match.completed = True
        match.timestamp = datetime.now().isoformat()
        # Store MMR deltas in the match record
        match.mmr_delta_a = winner_gain if winner is team_a else loser_change
        match.mmr_delta_b = winner_gain if winner is team_b else loser_change
        self.matches.append(match)
        self.save_data()

    def get_leaderboard(self) -> List[Team]:
        placed = [t for t in self.teams if t.matches_played >= self.placement_matches]
        return sorted(placed, key=lambda t: t.mmr, reverse=True) if placed else []

    def recalculate_all_mmr(self):
        for t in self.teams:
            t.mmr = BASE_MMR
            t.wins = 0
            t.losses = 0
            t.matches_played = 0
            t.provisional = True
        def match_sort_key(m: Match):
            return (m.week, m.timestamp or "")
        for m in sorted(self.matches, key=match_sort_key):
            if not m.completed or not m.score:
                continue
            team_a = next((t for t in self.teams if t.name == m.team_a), None)
            team_b = next((t for t in self.teams if t.name == m.team_b), None)
            if not team_a or not team_b:
                continue
            winner, loser = (team_a, team_b) if m.score[0] > m.score[1] else (team_b, team_a)
            points_a = points_b = None
            if m.set_scores:
                try:
                    ta = tb = 0
                    for s in m.set_scores:
                        a_str, b_str = s.split(':')
                        ta += int(a_str)
                        tb += int(b_str)
                    points_a, points_b = ta, tb
                except Exception:
                    points_a = points_b = None
            if points_a is not None and points_b is not None:
                if winner is team_a:
                    pw, pl = points_a, points_b
                else:
                    pw, pl = points_b, points_a
            else:
                pw = pl = None
            mmr_gain, mmr_change = self.update_mmr(winner, loser, tuple(m.score), set_scores=m.set_scores, points_winner=pw, points_loser=pl, record_history=False)
            # Store MMR deltas in the match record
            m.mmr_delta_a = mmr_gain if winner is team_a else mmr_change
            m.mmr_delta_b = mmr_gain if winner is team_b else mmr_change
        for t in self.teams:
            if t.matches_played >= self.placement_matches:
                t.provisional = False
        self.save_data()

    def generate_weekly_matches(self, matches_per_team: int = 1) -> List['Match']:
        import random
        teams = [t for t in self.teams if t.active]
        n = len(teams)
        if matches_per_team <= 0 or n < 2:
            logging.warning("No matches generated: invalid matches_per_team or not enough teams")
            return []
        total_degree = n * matches_per_team
        if total_degree % 2 != 0:
            logging.warning(f"Cannot generate schedule: n={n}, k={matches_per_team} leads to odd total degree {total_degree}")
            return []
        forbidden_pairs = set()
        for m in self.matches:
            pair = frozenset({m.team_a, m.team_b})
            forbidden_pairs.add(pair)
        names = [t.name for t in teams]
        name_to_team = {t.name: t for t in teams}
        counts = {name: 0 for name in names}
        chosen_pairs: set[frozenset] = set()
        target_pairs = total_degree // 2
        def available_opponents(a: str) -> List[str]:
            return [b for b in names
                    if b != a
                    and counts[b] < matches_per_team
                    and frozenset({a, b}) not in forbidden_pairs
                    and frozenset({a, b}) not in chosen_pairs]
        def select_team() -> Optional[str]:
            candidates = [name for name in names if counts[name] < matches_per_team]
            if not candidates:
                return None
            candidates.sort(key=lambda a: (matches_per_team - counts[a], len(available_opponents(a))))
            return candidates[0]
        def backtrack() -> bool:
            if len(chosen_pairs) == target_pairs:
                return True
            a = select_team()
            if a is None:
                return True
            ops = available_opponents(a)
            if not ops:
                return False
            random.shuffle(ops)
            for b in ops:
                if counts[a] >= matches_per_team or counts[b] >= matches_per_team:
                    continue
                pair = frozenset({a, b})
                chosen_pairs.add(pair)
                counts[a] += 1
                counts[b] += 1
                feasible = True
                if any(counts[name] > matches_per_team for name in names):
                    feasible = False
                if feasible:
                    for name in names:
                        if counts[name] < matches_per_team and not available_opponents(name):
                            feasible = False
                            break
                if feasible and backtrack():
                    return True
                chosen_pairs.remove(pair)
                counts[a] -= 1
                counts[b] -= 1
            return False
        if not backtrack():
            logging.warning("Failed to generate a complete, valid schedule with the given constraints.")
            return []
        created: List[Match] = []
        for pair in chosen_pairs:
            a, b = tuple(pair)
            created.append(Match(a, b, self.current_week))
        if created:
            self.matches.extend(created)
            self.save_data()
        return created

    def generate_weekly_matches_preview(self, matches_per_team: int = 1) -> List['Match']:
        """Generate a proposed set of matches for the current week with improved distribution."""
        import random
        teams = [t for t in self.teams if t.active]
        n = len(teams)
        if matches_per_team <= 0 or n < 2:
            return []

        total_degree = n * matches_per_team
        if total_degree % 2 != 0:
            logging.warning(f"Cannot generate complete schedule: n={n}, k={matches_per_team} leads to odd total degree {total_degree}")
            # For odd total degree, we'll generate as many matches as possible
            total_degree -= 1

        forbidden_pairs = {frozenset({m.team_a, m.team_b}) for m in self.matches}
        target_pairs = total_degree // 2

        # If all teams have very similar MMR (within 100 points), use round-robin style distribution
        mmr_values = [t.mmr for t in teams]
        mmr_range = max(mmr_values) - min(mmr_values) if mmr_values else 0
        
        if mmr_range <= 100 and n >= 4:
            # Use snake draft style pairing for even distribution
            return self._generate_snake_draft_matches(teams, matches_per_team, forbidden_pairs, target_pairs)
        else:
            # Use MMR-based pairing for skill-based matchmaking
            return self._generate_mmr_based_matches(teams, matches_per_team, forbidden_pairs, target_pairs)

    def _generate_snake_draft_matches(self, teams: List['Team'], matches_per_team: int, forbidden_pairs: set, target_pairs: int) -> List['Match']:
        """Generate matches using snake draft style for even distribution when MMRs are similar."""
        import random
        
        # Shuffle teams to randomize the starting order
        shuffled_teams = teams.copy()
        random.shuffle(shuffled_teams)
        
        names = [t.name for t in shuffled_teams]
        counts = {name: 0 for name in names}
        chosen_pairs: set[frozenset] = set()

        def available_opponents(a: str) -> List[str]:
            return [b for b in names
                    if b != a
                    and counts[b] < matches_per_team
                    and frozenset({a, b}) not in forbidden_pairs
                    and frozenset({a, b}) not in chosen_pairs]

        # Snake draft pairing: pair teams in alternating order
        for round_num in range(matches_per_team):
            round_teams = names.copy()
            if round_num % 2 == 1:  # Reverse order every other round
                round_teams.reverse()
            
            # Pair teams in groups
            for i in range(0, len(round_teams) - 1, 2):
                if len(chosen_pairs) >= target_pairs:
                    break
                    
                team_a = round_teams[i]
                team_b = round_teams[i + 1]
                
                # Check if this pairing is valid
                if (counts[team_a] < matches_per_team and 
                    counts[team_b] < matches_per_team and
                    frozenset({team_a, team_b}) not in forbidden_pairs and
                    frozenset({team_a, team_b}) not in chosen_pairs):
                    
                    chosen_pairs.add(frozenset({team_a, team_b}))
                    counts[team_a] += 1
                    counts[team_b] += 1

        # Fill remaining matches with available pairings
        while len(chosen_pairs) < target_pairs:
            # Find team with fewest matches
            min_matches = min(counts.values())
            candidates = [name for name, count in counts.items() if count == min_matches]
            
            if not candidates:
                break
                
            team_a = random.choice(candidates)
            opponents = available_opponents(team_a)
            
            if not opponents:
                # If no opponents available for this team, try next team
                continue
                
            team_b = random.choice(opponents)
            chosen_pairs.add(frozenset({team_a, team_b}))
            counts[team_a] += 1
            counts[team_b] += 1

        # Build Match objects
        created: List[Match] = []
        for pair in chosen_pairs:
            a, b = tuple(pair)
            created.append(Match(a, b, self.current_week))
        return created

    def _generate_mmr_based_matches(self, teams: List['Team'], matches_per_team: int, forbidden_pairs: set, target_pairs: int) -> List['Match']:
        """Generate matches prioritizing similar MMR teams."""
        # Sort teams by MMR for easier pairing
        teams.sort(key=lambda t: t.mmr)
        names = [t.name for t in teams]
        name_to_team = {t.name: t for t in teams}
        counts = {name: 0 for name in names}
        chosen_pairs: set[frozenset] = set()

        def available_opponents(a: str) -> List[str]:
            """Find available opponents for team `a`, prioritizing closest MMR."""
            team_a = name_to_team[a]
            potential_opponents = [
                (b, abs(team_a.mmr - name_to_team[b].mmr))
                for b in names
                if b != a
                and counts[b] < matches_per_team
                and frozenset({a, b}) not in forbidden_pairs
                and frozenset({a, b}) not in chosen_pairs
            ]
            # Sort by MMR difference (ascending)
            potential_opponents.sort(key=lambda x: x[1])
            return [b for b, _ in potential_opponents]

        def select_team() -> Optional[str]:
            """Select the next team to assign a match, prioritizing those with fewer matches."""
            candidates = [name for name in names if counts[name] < matches_per_team]
            if not candidates:
                return None
            candidates.sort(key=lambda a: (matches_per_team - counts[a], len(available_opponents(a))))
            return candidates[0]

        def backtrack() -> bool:
            if len(chosen_pairs) >= target_pairs:
                return True
            a = select_team()
            if a is None:
                return True
            ops = available_opponents(a)
            if not ops:
                return False
            for b in ops:
                if counts[a] >= matches_per_team or counts[b] >= matches_per_team:
                    continue
                pair = frozenset({a, b})
                chosen_pairs.add(pair)
                counts[a] += 1
                counts[b] += 1
                if backtrack():
                    return True
                chosen_pairs.remove(pair)
                counts[a] -= 1
                counts[b] -= 1
            return False

        if not backtrack():
            logging.warning("Failed to generate a complete, valid schedule with the given constraints.")

        # Build Match objects from chosen pairs
        created: List[Match] = []
        for pair in chosen_pairs:
            a, b = tuple(pair)
            created.append(Match(a, b, self.current_week))
        return created

    def get_db_status(self) -> Dict[str, Optional[str]]:
        info: Dict[str, Optional[str]] = {
            'backend': 'sqlite' if self.has_database else 'memory',
            'db_path': DB_PATH if os.path.exists(DB_PATH) else None,
            'teams_count': str(len(self.teams)),
            'matches_count': str(len(self.matches)),
            'db_size_bytes': None,
            'legacy_json_present': str(os.path.exists(TEAMS_PATH) or os.path.exists(MATCHES_PATH))
        }
        if info['db_path']:
            try:
                info['db_size_bytes'] = str(os.path.getsize(DB_PATH))
            except Exception:
                pass
        return info

    def remigrate_from_json(self) -> bool:
        if not self.has_database:
            return False
        try:
            if os.path.exists(DB_PATH):
                import shutil
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                os.makedirs('backups', exist_ok=True)
                shutil.copy2(DB_PATH, os.path.join('backups', f'mmr_backup_{ts}.db'))
        except Exception as e:
            logging.warning(f"Failed to backup DB before re-migrate: {e}")
        try:
            legacy_teams = []
            legacy_matches = []
            if os.path.exists(TEAMS_PATH):
                with open(TEAMS_PATH, 'r') as f:
                    legacy_teams = json.load(f)
            if os.path.exists(MATCHES_PATH):
                with open(MATCHES_PATH, 'r') as f:
                    legacy_matches = json.load(f)
            with self.SessionLocal() as db:
                db.query(MatchModel).delete()
                db.query(TeamModel).delete()
                for t in legacy_teams:
                    tm = TeamModel(
                        name=t.get('name'),
                        mmr=int(t.get('mmr', BASE_MMR) or BASE_MMR),
                        matches_played=int(t.get('matches_played', 0) or 0),
                        history=t.get('history') or [],
                        wins=int(t.get('wins', 0) or 0),
                        losses=int(t.get('losses', 0) or 0),
                        active=bool(t.get('active', True)),
                        provisional=bool(t.get('provisional', True)),
                        roster=t.get('roster') or [],
                        logo=t.get('logo') or "",
                        hexcolor=t.get('hexcolor') or "#374151",
                        created_at=datetime.utcnow().isoformat(),
                        updated_at=datetime.utcnow().isoformat(),
                    )
                    db.add(tm)
                for m in legacy_matches:
                    mm = MatchModel(
                        match_id=m.get('match_id') or str(uuid.uuid4())[:8],
                        team_a=m.get('team_a'),
                        team_b=m.get('team_b'),
                        week=int(m.get('week', 1) or 1),
                        score=m.get('score'),
                        set_scores=m.get('set_scores') or None,
                        completed=bool(m.get('completed', False)),
                        timestamp=m.get('timestamp'),
                    )
                    db.add(mm)
                db.commit()
            self.load_data()
            return True
        except Exception as e:
            logging.exception(f"Re-migrate from JSON failed: {e}")
            return False

    def get_setting(self, key: str) -> Optional[str]:
        if not self.has_database:
            return None
        try:
            with self.SessionLocal() as db:
                row = db.get(SettingModel, key)
                return row.value if row else None
        except Exception as e:
            logging.warning(f"get_setting failed for key={key}: {e}")
            return None

    def set_setting(self, key: str, value: Optional[str]) -> None:
        if not self.has_database:
            return
        try:
            with self.SessionLocal() as db:
                row = db.get(SettingModel, key)
                if not row:
                    row = SettingModel(key=key, value=value or "", updated_at=datetime.utcnow().isoformat())
                    db.add(row)
                else:
                    row.value = value or ""
                    row.updated_at = datetime.utcnow().isoformat()
                db.commit()
        except Exception as e:
            logging.warning(f"set_setting failed for key={key}: {e}")

    def get_mmr_config(self) -> Dict:
        """Return the current MMR configuration from DB as a dict compatible with templates."""
        # Defaults
        cfg: Dict = {
            "BASE_MMR": BASE_MMR,
            "PLACEMENT_MATCHES": PLACEMENT_MATCHES,
            "K_FACTOR": K_FACTOR,
            "CHALLENGE_MULTIPLIER": CHALLENGE_MULTIPLIER,
            "INACTIVITY_PENALTY": INACTIVITY_PENALTY,
            "MARGIN_BONUS": {"3_0": 5, "3_1": 3, "3_2": 1},
            "POINT_DIFF_MULTIPLIER": POINT_DIFF_MULTIPLIER,
        }
        if not self.has_database:
            return cfg
        try:
            with self.SessionLocal() as db:
                row = db.query(MMRConfigModel).order_by(MMRConfigModel.id.asc()).first()
                if not row:
                    return cfg
                mb = row.margin_bonus or {"3_0": 5, "3_1": 3, "3_2": 1}
                # Ensure margin bonus keys are strings like "3_0"
                mb_norm = {str(k): int(v) for k, v in mb.items()}
                return {
                    "BASE_MMR": int(row.base_mmr),
                    "PLACEMENT_MATCHES": int(row.placement_matches),
                    "K_FACTOR": int(row.k_factor),
                    "CHALLENGE_MULTIPLIER": float(row.challenge_multiplier),
                    "INACTIVITY_PENALTY": int(row.inactivity_penalty),
                    "MARGIN_BONUS": mb_norm,
                    "POINT_DIFF_MULTIPLIER": float(row.point_diff_multiplier),
                }
        except Exception as e:
            logging.warning(f"Failed to read MMR config from DB: {e}")
            return cfg

    def update_mmr_config_in_db(self,
                                base_mmr: int,
                                placement_matches: int,
                                k_factor: int,
                                challenge_multiplier: float,
                                inactivity_penalty: int,
                                margin_bonus: Dict[str, int],
                                point_diff_multiplier: float) -> None:
        """Upsert the mmr_config row in DB with provided values."""
        if not self.has_database:
            return
        try:
            with self.SessionLocal() as db:
                row = db.query(MMRConfigModel).order_by(MMRConfigModel.id.asc()).first()
                if not row:
                    row = MMRConfigModel()
                    db.add(row)
                row.base_mmr = int(base_mmr)
                row.placement_matches = int(placement_matches)
                row.k_factor = int(k_factor)
                try:
                    row.challenge_multiplier = float(challenge_multiplier)
                except Exception:
                    row.challenge_multiplier = CHALLENGE_MULTIPLIER
                row.inactivity_penalty = int(inactivity_penalty)
                # Ensure string keys for JSON storage
                row.margin_bonus = {str(k): int(v) for k, v in (margin_bonus or {}).items()}
                try:
                    row.point_diff_multiplier = float(point_diff_multiplier)
                except Exception:
                    row.point_diff_multiplier = POINT_DIFF_MULTIPLIER
                row.updated_at = datetime.utcnow().isoformat()
                db.commit()
        except Exception as e:
            logging.warning(f"Failed to update MMR config in DB: {e}")