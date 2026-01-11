# Canvas Completion Grading Automation

Automates **completion-based grading** for Canvas courses using the Canvas API.

This script checks student submissions on a weekly schedule and updates the Canvas
Gradebook to reflect **Complete / Incomplete-style grading**, including courses that
use **0/1 point assignments** for completion.

Late submissions are counted as complete.

---

## Features

- Automatically marks assignments as complete or incomplete
- Supports both:
  - Canvas `complete_incomplete` grading type
  - Point-based completion grading (e.g., 0 / 1)
- Ignores lateness (submission presence only)
- Safe by default with `DRY_RUN` mode
- Designed for weekly automation

---

## Grading Logic

For each eligible assignment:

- If `submitted_at` exists → **Complete** (or full points)
- Otherwise → **Incomplete** (or 0 points)

Assignments are graded only after a configurable **grace period** and within a
rolling **time window**.

---

## Configuration

Environment variables are required.  
See `.env.example` for all options.

Key settings:

- `COURSE_ID` – Canvas course ID
- `GRACE_DAYS` – Days to wait after due date
- `WINDOW_DAYS` – Lookback window for grading
- `DRY_RUN` – Preview changes without updating grades

⚠️ Never commit your real `.env` file or Canvas token.

---

## Usage

```bash
set -a
source .env
set +a

python canvas_completion_grader.py

---

## Scheduling
The script is designed to be run automatically on a fixed weekly schedule
(e.g., **Thursdays at 5:00 pm**).

---

## Design notes
This tool intentionally avoids AI-based grading.
All grading decisions are deterministic, auditable, and policy-driven, making the
system transparent and suitable for academic use.

---

## Acknowledgments
Initial implementation developed with assistance from ChatGPT.




