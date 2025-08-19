import json
import os
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import uuid
import logging

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

class Team:
    def __init__(self, name: str, mmr: int = BASE_MMR):
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

    def to_dict(self) -> Dict:
        return {
            'name': self.name,
            'mmr': self.mmr,
            'matches_played': self.matches_played,
            'history': self.history,
            'wins': self.wins,
            'losses': self.losses,
            'active': self.active
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'Team':
        team = cls(data['name'], data.get('mmr', BASE_MMR))
        team.matches_played = data.get('matches_played', 0)
        team.history = data.get('history', [])
        team.wins = data.get('wins', 0)
        team.losses = data.get('losses', 0)
        team.active = data.get('active', True)
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
        self.timestamp = None

    def to_dict(self) -> Dict:
        return {
            'match_id': self.match_id,
            'team_a': self.team_a,
            'team_b': self.team_b,
            'week': self.week,
            'score': self.score,
            'set_scores': self.set_scores,
            'completed': self.completed,
            'timestamp': self.timestamp
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'Match':
        match = cls(data['team_a'], data['team_b'], data['week'], data['match_id'])
        match.score = data.get('score')
        match.set_scores = data.get('set_scores')
        match.completed = data.get('completed', False)
        match.timestamp = data.get('timestamp')
        return match

class MMRSystem:
    def __init__(self):
        self.teams: List[Team] = []
        self.matches: List[Match] = []
        self.current_week = 1
        self.load_data()

    def load_data(self):
        self.teams = self._load_teams()
        self.matches = self._load_matches()
        if self.matches:
            self.current_week = max(match.week for match in self.matches) + 1

    def save_data(self):
        self._save_teams()
        self._save_matches()

    def _load_teams(self) -> List[Team]:
        if not os.path.exists(TEAMS_PATH):
            return []
        with open(TEAMS_PATH, 'r') as f:
            return [Team.from_dict(data) for data in json.load(f)]

    def _save_teams(self):
        with open(TEAMS_PATH, 'w') as f:
            json.dump([team.to_dict() for team in self.teams], f, indent=2)

    def _load_matches(self) -> List[Match]:
        if not os.path.exists(MATCHES_PATH):
            return []
        with open(MATCHES_PATH, 'r') as f:
            return [Match.from_dict(data) for data in json.load(f)]

    def _save_matches(self):
        with open(MATCHES_PATH, 'w') as f:
            json.dump([match.to_dict() for match in self.matches], f, indent=2)

    def update_mmr(self, winner: Team, loser: Team, score: Tuple[int, int], set_scores: List[str]):
        winner_expected = 1 / (1 + 10 ** ((loser.mmr - winner.mmr) / 400))
        loser_expected = 1 - winner_expected

        winner_change = K_FACTOR * (1 - winner_expected)
        loser_change = K_FACTOR * (0 - loser_expected)

        winner.mmr = max(0, int(winner.mmr + winner_change))
        loser.mmr = max(0, int(loser.mmr + loser_change))

        winner.matches_played += 1
        loser.matches_played += 1

        winner.history.append(f"Won {score[0]}-{score[1]} vs {loser.name}")
        loser.history.append(f"Lost {score[1]}-{score[0]} vs {winner.name}")

    def record_match(self, team_a_name: str, team_b_name: str, score: Tuple[int, int], set_scores: List[str]):
        team_a = next((t for t in self.teams if t.name == team_a_name), None)
        team_b = next((t for t in self.teams if t.name == team_b_name), None)

        if not team_a or not team_b:
            logging.error("One or both teams not found.")
            return

        winner, loser = (team_a, team_b) if score[0] > score[1] else (team_b, team_a)
        self.update_mmr(winner, loser, score, set_scores)

        match = Match(team_a_name, team_b_name, self.current_week)
        match.score = score
        match.set_scores = set_scores
        match.completed = True
        match.timestamp = datetime.now().isoformat()
        self.matches.append(match)

        self.save_data()

    def get_leaderboard(self) -> List[Team]:
        """Return a sorted list of teams by MMR in descending order."""
        return sorted(self.teams, key=lambda t: t.mmr, reverse=True) if self.teams else []

    def generate_weekly_matches(self, matches_per_team: int = 1) -> List['Match']:
        """Generate matches for the current week so each team gets matches_per_team matches if possible."""
        import random
        teams = self.teams[:]
        if len(teams) < 2:
            logging.warning("Not enough teams to generate matches")
            return []

        counts = {t.name: 0 for t in teams}
        matches_created: List[Match] = []

        # Attempt to generate pairings until all counts reach the target or no progress
        while any(count < matches_per_team for count in counts.values()):
            random.shuffle(teams)
            added_this_round = 0
            for i in range(0, len(teams) - 1, 2):
                a = teams[i]
                b = teams[i + 1]
                if counts[a.name] < matches_per_team and counts[b.name] < matches_per_team:
                    m = Match(a.name, b.name, self.current_week)
                    matches_created.append(m)
                    counts[a.name] += 1
                    counts[b.name] += 1
                    added_this_round += 1
            if added_this_round == 0:
                # Cannot satisfy further constraints; break to avoid infinite loop
                break

        if matches_created:
            self.matches.extend(matches_created)
            self.save_data()
        return matches_created