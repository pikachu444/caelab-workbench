# Git Branch and Review Guard Rule

## 1. Direct Commit & Push on `main` Forbidden
- All development, bug fixes, test adjustments, and refactoring MUST be done in dedicated feature/fix branches (e.g. `fix/...`, `feat/...`, `docs/...`).
- Never run `git commit` directly on `main`. (Git pre-commit hook will block it).
- Never run `git push origin main`. (Git pre-push hook will block it).

## 2. Reviewer Review Mandate
- Before merging any branch or declaring completion, the changes MUST be formally audited by the designated reviewers (`chief-reviewer`, `cae-user-expert` on Gemini Pro).
- If any reviewer issues defects or B/C/D critique, the critique MUST be resolved or properly answered before proceeding.
- Merging to `main` must always be done via Pull Request (`gh pr create` -> review -> `gh pr merge`).
