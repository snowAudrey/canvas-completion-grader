# Canvas Completion Grading Automation

Automates **completion-based grading** for Canvas courses using the Canvas API.

This script checks student submissions and updates the Canvas Gradebook to reflect **Complete / Incomplete-style grading**, including courses that
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
- Designed for controlled, instructor-in-the-loop execution
- **Excludes non-submission assessments** (e.g., presentations) to
  preserve manual rubric-based grading

---

## Grading Logic

For each eligible assignment:

- If `submitted_at` exists → **Complete** (or full points)
- Otherwise → **Incomplete** (or 0 points)

Assignments are graded only after a configurable **grace period** and within a
rolling **time window**.

Assignments that do not expect student submissions are intentionally excluded
from automated grading.

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
```

---

## Scheduling
The script is designed to be run **manually** by the instructor. 

Although it is compatible with automated schedulers (e.g., GitHub Actions),
manual execution is preferred to reduce risk when courses include assessment types
that require instructor judgment (e.g., presentations).

This design choice ensures that automated grading does not overwrite manually
assigned grades for non-submission-based assessments.

---

## Design notes
This tool intentionally avoids AI-based grading.
All grading decisions are based solely on the presence or absence of a submission at the time the script runs. 

---

## Acknowledgments
Initial implementation developed with assistance from ChatGPT.



