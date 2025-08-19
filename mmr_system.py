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

class Team:
    def __init__(self, name: str, mmr: int = BASE_MMR):
        self.name = name
        self.mmr = mmr
        self.matches_played = 0
        self.history = []
        self.wins = 0
        self.losses = 0

    def to_dict(self) -> Dict:
        return {
            'name': self.name,
            'mmr': self.mmr,
            'matches_played': self.matches_played,
            'history': self.history,
            'wins': self.wins,
            'losses': self.losses
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'Team':
        team = cls(data['name'], data['mmr'])
        team.matches_played = data['matches_played']
        team.history = data['history']
        team.wins = data['wins']
        team.losses = data['losses']
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
        if not os.path.exists('teams.json'):
            return []
        with open('teams.json', 'r') as f:
            return [Team.from_dict(data) for data in json.load(f)]

    def _save_teams(self):
        with open('teams.json', 'w') as f:
            json.dump([team.to_dict() for team in self.teams], f, indent=2)

    def _load_matches(self) -> List[Match]:
        if not os.path.exists('matches.json'):
            return []
        with open('matches.json', 'r') as f:
            return [Match.from_dict(data) for data in json.load(f)]

    def _save_matches(self):
        with open('matches.json', 'w') as f:
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
        return sorted(self.teams, key=lambda t: t.mmr, reverse=True)