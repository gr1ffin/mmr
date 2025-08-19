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
        # Instance-level tunables (default from constants)
        self.k_factor = K_FACTOR
        self.point_diff_multiplier = POINT_DIFF_MULTIPLIER
        self.margin_bonus = MARGIN_BONUS.copy()
        self.inactivity_penalty = INACTIVITY_PENALTY
        self.placement_matches = 2  # Teams are provisional until they play this many matches
        self.load_data()

    def load_data(self):
        self.teams = self._load_teams()
        self.matches = self._load_matches()
        # Set current week and adjust provisional status based on placement threshold
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

    def update_mmr(self, winner: Team, loser: Team, score: Tuple[int, int], set_scores: Optional[List[str]] = None, points_winner: Optional[int] = None, points_loser: Optional[int] = None, record_history: bool = True) -> int:
        """Update MMR using an Elo-like core plus margins:
        - Core: Elo expectation by rating diff (bigger gains for upsets).
        - Margin: Primary weight from total point differential (capped at 75).
        - Set bonus: +5 for 3-0, +3 for 3-1, +1 for 3-2 (for the winner).
        Returns the MMR change applied to the winner (positive integer).
        """
        # Elo expectation (rating diff focus)
        expected_winner = 1 / (1 + 10 ** ((loser.mmr - winner.mmr) / 400))
        core_gain = self.k_factor * (1 - expected_winner)

        # Compute set bonus based on winner's set score
        winner_sets = max(score)
        loser_sets = min(score)
        set_bonus = self.margin_bonus.get((winner_sets, loser_sets), 0)

        # Compute point differential primarily from provided totals
        if points_winner is None or points_loser is None:
            # Try to compute from set_scores if available, assuming set_scores are formatted as "A:B"
            if set_scores:
                try:
                    # In absence of orientation, this will be computed externally where possible.
                    # Here we fall back to no additional points if orientation is unknown.
                    points_winner = 0
                    points_loser = 0
                except Exception:
                    points_winner = 0
                    points_loser = 0
            else:
                points_winner = 0
                points_loser = 0
        point_diff = max(0, int(points_winner) - int(points_loser))
        # Cap at 75 as per hypothetical max (3x 25-0)
        point_diff_capped = min(point_diff, 75)
        point_factor = self.point_diff_multiplier * point_diff_capped

        # Total gain for winner (ensure at least 1)
        mmr_gain = int(round(core_gain + point_factor + set_bonus))
        mmr_gain = max(1, mmr_gain)

        # Apply changes symmetrically
        winner.mmr = max(0, winner.mmr + mmr_gain)
        loser.mmr = max(0, loser.mmr - mmr_gain)

        # Record W/L
        winner.wins += 1
        loser.losses += 1
        winner.matches_played += 1
        loser.matches_played += 1
        # Flip provisional off when threshold reached
        if winner.matches_played >= self.placement_matches:
            winner.provisional = False
        if loser.matches_played >= self.placement_matches:
            loser.provisional = False

        # History entries
        if record_history:
            winner.history.append(f"Won {winner_sets}-{loser_sets} vs {loser.name} (+{mmr_gain} MMR, +{point_diff} pts)")
            loser.history.append(f"Lost {loser_sets}-{winner_sets} vs {winner.name} (-{mmr_gain} MMR, -{point_diff} pts)")

        return mmr_gain

    def record_match(self, team_a_name: str, team_b_name: str, score: Tuple[int, int], set_scores: Optional[List[str]] = None):
        team_a = next((t for t in self.teams if t.name == team_a_name), None)
        team_b = next((t for t in self.teams if t.name == team_b_name), None)
        if not team_a or not team_b:
            logging.error("One or both teams not found.")
            return

        winner, loser = (team_a, team_b) if score[0] > score[1] else (team_b, team_a)

        # Compute total points for team_a and team_b if set_scores provided
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

        # Map to winner/loser oriented totals
        if points_a is not None and points_b is not None:
            if winner is team_a:
                pw, pl = points_a, points_b
            else:
                pw, pl = points_b, points_a
        else:
            pw = pl = None

        self.update_mmr(winner, loser, score, set_scores=set_scores, points_winner=pw, points_loser=pl)

        match = Match(team_a_name, team_b_name, self.current_week)
        match.score = score
        match.set_scores = set_scores
        match.completed = True
        match.timestamp = datetime.now().isoformat()
        self.matches.append(match)

        self.save_data()

    def get_leaderboard(self) -> List[Team]:
        """Return a sorted list of non-provisional teams by MMR in descending order."""
        placed = [t for t in self.teams if t.matches_played >= self.placement_matches]
        return sorted(placed, key=lambda t: t.mmr, reverse=True) if placed else []

    def recalculate_all_mmr(self):
        """Recalculate MMR, wins/losses, and matches_played from matches history for all teams."""
        # Reset stats
        for t in self.teams:
            t.mmr = BASE_MMR
            t.wins = 0
            t.losses = 0
            t.matches_played = 0
            t.provisional = True
        # Reapply all completed matches in chronological order
        def match_sort_key(m: Match):
            return (m.week, m.timestamp or "")
        for m in sorted(self.matches, key=match_sort_key):
            if not m.completed or not m.score:
                continue
            team_a = next((t for t in self.teams if t.name == m.team_a), None)
            team_b = next((t for t in self.teams if t.name == m.team_b), None)
            if not team_a or not team_b:
                continue
            # Determine winner/loser
            winner, loser = (team_a, team_b) if m.score[0] > m.score[1] else (team_b, team_a)
            # Compute total points
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
            self.update_mmr(winner, loser, tuple(m.score), set_scores=m.set_scores, points_winner=pw, points_loser=pl, record_history=False)
        # After replaying history, set provisional flags by threshold
        for t in self.teams:
            if t.matches_played >= self.placement_matches:
                t.provisional = False
        self.save_data()

    def generate_weekly_matches(self, matches_per_team: int = 1) -> List['Match']:
        """Generate matches for the current week ensuring:
        - Each active team gets exactly matches_per_team matches (if feasible).
        - No team is paired with an opponent they've already played before (historically).
        - The schedule is computed fully first; only then are matches saved.
        Returns the list of created Match objects. Returns [] if no complete schedule is possible.
        """
        import random
        teams = [t for t in self.teams if t.active]
        n = len(teams)
        if matches_per_team <= 0 or n < 2:
            logging.warning("No matches generated: invalid matches_per_team or not enough teams")
            return []
        # Feasibility check: total degree must be even
        total_degree = n * matches_per_team
        if total_degree % 2 != 0:
            logging.warning(f"Cannot generate schedule: n={n}, k={matches_per_team} leads to odd total degree {total_degree}")
            return []

        # Build forbidden pairs from all historical matches (avoid rematches)
        forbidden_pairs = set()
        for m in self.matches:
            pair = frozenset({m.team_a, m.team_b})
            forbidden_pairs.add(pair)

        names = [t.name for t in teams]
        name_to_team = {t.name: t for t in teams}
        counts = {name: 0 for name in names}
        chosen_pairs: set[frozenset] = set()
        target_pairs = total_degree // 2

        # Precompute candidate adjacency for quick filtering
        def available_opponents(a: str) -> List[str]:
            return [b for b in names
                    if b != a
                    and counts[b] < matches_per_team
                    and frozenset({a, b}) not in forbidden_pairs
                    and frozenset({a, b}) not in chosen_pairs]

        # Heuristic: choose the team with the least available opponents (fail-fast)
        def select_team() -> Optional[str]:
            candidates = [name for name in names if counts[name] < matches_per_team]
            if not candidates:
                return None
            # Sort by remaining needed then by branching factor
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
                # Choose
                chosen_pairs.add(pair)
                counts[a] += 1
                counts[b] += 1
                # Early prune: ensure no team exceeds capacity and remaining can still reach targets
                feasible = True
                if any(counts[name] > matches_per_team for name in names):
                    feasible = False
                # Also, if any team has zero available opponents left but still needs matches, prune
                if feasible:
                    for name in names:
                        if counts[name] < matches_per_team and not available_opponents(name):
                            feasible = False
                            break
                if feasible and backtrack():
                    return True
                # Undo
                chosen_pairs.remove(pair)
                counts[a] -= 1
                counts[b] -= 1
            return False

        if not backtrack():
            logging.warning("Failed to generate a complete, valid schedule with the given constraints.")
            return []

        # Convert chosen_pairs to Match objects for current week
        created: List[Match] = []
        for pair in chosen_pairs:
            a, b = tuple(pair)
            created.append(Match(a, b, self.current_week))

        # Persist in one go
        if created:
            self.matches.extend(created)
            self.save_data()
        return created