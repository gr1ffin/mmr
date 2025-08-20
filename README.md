# MMR Ops Runbook (gr1ffin.com)

This README is the operational guide only. It covers:
- Start/stop/status of the website service on the server
- Pulling updates from GitHub onto the server
- Pushing changes from the server to GitHub
- Git ignore policy so everything except local env files is tracked

## Service control (server)
- Start: sudo systemctl start mmr
- Stop: sudo systemctl stop mmr
- Restart: sudo systemctl restart mmr
- Enable on boot: sudo systemctl enable mmr
- Status: systemctl status mmr --no-pager
- Upstream quick test: curl -I http://127.0.0.1:8000

## Pull latest code from GitHub (server)
Use the LIVE branch as the deployed branch.
- cd /opt/mmr
- git fetch --all --tags
- git switch LIVE
- git pull --ff-only
- /opt/mmr/venv/bin/pip install -r requirements.txt
- sudo systemctl restart mmr

If local changes block pull:
- git stash --include-untracked
- git pull --ff-only
- git stash pop (resolve if needed)

Force match remote (discard local changes):
- git reset --hard origin/LIVE

## Push changes from the server to GitHub
One-time SSH setup:
- ssh-keygen -t ed25519 -C "deploy@gr1ffin.com" -f ~/.ssh/github_ed25519
- Add ~/.ssh/github_ed25519.pub to your GitHub account (SSH keys) or as a repo deploy key with write access
- Create ~/.ssh/config with:
  Host github.com
    HostName github.com
    User git
    IdentityFile ~/.ssh/github_ed25519
    IdentitiesOnly yes
- Test: ssh -T git@github.com (should say you’re authenticated)

Ensure remote/branch, commit, and push:
- cd /opt/mmr
- git remote set-url origin git@github.com:gr1ffin/mmr.git
- git switch -c LIVE 2>/dev/null || git switch LIVE
- git add -A
- git commit -m "Server changes"
- git push -u origin LIVE

## Git ignore policy (track everything except .env)
Keep only local env files ignored so ALL other files (including DBs) are tracked.

.gitignore contents should be exactly:

.env
.env.*

Reindex the repo so previously ignored files are added:
- cd /opt/mmr (or your local repo root)
- git rm -r --cached .
- git add -A
- git commit -m "Track all files; keep only .env ignored"
- git push origin LIVE

Check why a file is ignored (if something still won’t add):
- git check-ignore -v <path>

Check global excludes:
- git config --global core.excludesfile (then review that file)

Clear assume-unchanged if set:
- git ls-files -v | grep '^h'
- git update-index --no-assume-unchanged <path>
