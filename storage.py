import json
import os
import uuid
import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import create_engine, Column, Integer, String, Boolean, JSON as SA_JSON, Text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.exc import OperationalError

# Domain imports happen at runtime to avoid circulars
# from mmr_system import Team, Match

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEAMS_PATH = os.path.join(BASE_DIR, 'teams.json')
MATCHES_PATH = os.path.join(BASE_DIR, 'matches.json')
DB_PATH = os.path.join(BASE_DIR, 'mmr.db')
DB_URL = f"sqlite:///{DB_PATH}"

# -------------------- Storage Abstraction --------------------
class BaseStorage:
    def load_teams(self) -> List["Team"]:
        raise NotImplementedError

    def save_teams(self, teams: List["Team"]) -> None:
        raise NotImplementedError

    def load_matches(self) -> List["Match"]:
        raise NotImplementedError

    def save_matches(self, matches: List["Match"]) -> None:
        raise NotImplementedError

# -------------------- JSON Backend --------------------
class JsonStorage(BaseStorage):
    def __init__(self, teams_path: Optional[str] = None, matches_path: Optional[str] = None):
        self.teams_path = teams_path or TEAMS_PATH
        self.matches_path = matches_path or MATCHES_PATH

    def load_teams(self) -> List["Team"]:
        from mmr_system import Team
        if not os.path.exists(self.teams_path):
            return []
        with open(self.teams_path, 'r') as f:
            return [Team.from_dict(data) for data in json.load(f)]

    def save_teams(self, teams: List["Team"]) -> None:
        self._atomic_write(self.teams_path, json.dumps([t.to_dict() for t in teams], indent=2))

    def load_matches(self) -> List["Match"]:
        from mmr_system import Match
        if not os.path.exists(self.matches_path):
            return []
        with open(self.matches_path, 'r') as f:
            return [Match.from_dict(data) for data in json.load(f)]

    def save_matches(self, matches: List["Match"]) -> None:
        self._atomic_write(self.matches_path, json.dumps([m.to_dict() for m in matches], indent=2))

    @staticmethod
    def _atomic_write(path: str, content: str) -> None:
        tmp = f"{path}.tmp"
        with open(tmp, 'w') as f:
            f.write(content)
        os.replace(tmp, path)

# -------------------- SQLite Backend --------------------
Base = declarative_base()

class TeamModel(Base):
    __tablename__ = 'teams'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False)
    mmr = Column(Integer, default=1000)
    matches_played = Column(Integer, default=0)
    history = Column(SA_JSON, default=list)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    active = Column(Boolean, default=True)
    provisional = Column(Boolean, default=True)
    roster = Column(SA_JSON, default=list)
    logo = Column(Text, default="")
    hexcolor = Column(String, default="#374151")
    created_at = Column(String, default=lambda: datetime.utcnow().isoformat())
    updated_at = Column(String, default=lambda: datetime.utcnow().isoformat())

class MatchModel(Base):
    __tablename__ = 'matches'
    match_id = Column(String, primary_key=True)
    team_a = Column(String, nullable=False)
    team_b = Column(String, nullable=False)
    week = Column(Integer, nullable=False)
    score = Column(SA_JSON, nullable=True)
    set_scores = Column(SA_JSON, nullable=True)
    completed = Column(Boolean, default=False)
    timestamp = Column(String, nullable=True)

class SqliteStorage(BaseStorage):
    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or DB_URL
        self.engine = create_engine(self.db_url, echo=False, future=True)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False, class_=Session)
        self._init_db()
        self._migrate_from_json_if_needed()

    def _init_db(self):
        try:
            Base.metadata.create_all(self.engine)
        except OperationalError as e:
            logging.error(f"Failed to initialize database: {e}")
            raise

    def _migrate_from_json_if_needed(self):
        # Only on empty DB
        try:
            with self.SessionLocal() as db:
                has_any = db.query(TeamModel).first() is not None or db.query(MatchModel).first() is not None
                if has_any:
                    return
        except Exception:
            return
        if not (os.path.exists(TEAMS_PATH) or os.path.exists(MATCHES_PATH)):
            return
        legacy_teams = []
        legacy_matches = []
        try:
            if os.path.exists(TEAMS_PATH):
                with open(TEAMS_PATH, 'r') as f:
                    legacy_teams = json.load(f)
        except Exception as e:
            logging.warning(f"Failed to read legacy teams.json: {e}")
        try:
            if os.path.exists(MATCHES_PATH):
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
                        mmr=int(t.get('mmr', 1000) or 1000),
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

    def load_teams(self) -> List["Team"]:
        from mmr_system import Team
        out: List[Team] = []
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
                out.append(t)
        return out

    def save_teams(self, teams: List["Team"]) -> None:
        with self.SessionLocal() as db:
            existing = {tm.name: tm for tm in db.query(TeamModel).all()}
            seen = set()
            for t in teams:
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
            # delete rows not in memory snapshot
            for name, tm in existing.items():
                if name not in seen:
                    db.delete(tm)
            db.commit()

    def load_matches(self) -> List["Match"]:
        from mmr_system import Match
        out: List[Match] = []
        with self.SessionLocal() as db:
            for mm in db.query(MatchModel).all():
                m = Match(mm.team_a, mm.team_b, mm.week, mm.match_id)
                m.score = tuple(mm.score) if mm.score else None
                m.set_scores = mm.set_scores or None
                m.completed = bool(mm.completed)
                m.timestamp = mm.timestamp
                out.append(m)
        return out

    def save_matches(self, matches: List["Match"]) -> None:
        with self.SessionLocal() as db:
            existing = {mm.match_id: mm for mm in db.query(MatchModel).all()}
            seen = set()
            for m in matches:
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
                mm.timestamp = m.timestamp
            for mid, mm in existing.items():
                if mid not in seen:
                    db.delete(mm)
            db.commit()
