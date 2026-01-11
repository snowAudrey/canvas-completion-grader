"""
Canvas Completion Grading Automation
- Marks Complete/Incomplete based on submission presence (submitted_at exists)
- Ignores lateness
- Grades assignments in a rolling window after a grace period
- Safe: DRY_RUN, idempotent updates, logs

Run (after loading env vars):
  python canvas_completion_grader.py
"""

from __future__ import annotations

import os
import sys
import time
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple
from datetime import datetime, timedelta

import requests
from dateutil import parser as dtparser
from zoneinfo import ZoneInfo


# ----------------------------
# Configuration
# ----------------------------

@dataclass(frozen=True)
class Config:
    canvas_base_url: str
    canvas_token: str
    course_id: str

    grace_days: int = 1
    window_days: int = 7
    assignment_group_id: Optional[str] = None

    dry_run: bool = True
    log_level: str = "INFO"

    timezone: str = "America/Denver"

    # If true, the script will ONLY act when local time is Thu 5:00pm (minute=0).
    # Useful for GitHub Actions hourly schedules.
    enforce_thursday_5pm: bool = False

    # If true, update only when assignment grading_type is complete_incomplete
    require_complete_incomplete: bool = True


def env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_config() -> Config:
    base_url = os.getenv("CANVAS_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("CANVAS_TOKEN", "").strip()
    course_id = os.getenv("COURSE_ID", "").strip()

    if not base_url or not token or not course_id:
        missing = [k for k in ["CANVAS_BASE_URL", "CANVAS_TOKEN", "COURSE_ID"] if not os.getenv(k)]
        raise SystemExit(
            f"Missing required environment variables: {', '.join(missing)}\n"
            f"Set them and try again. See .env.example."
        )

    return Config(
        canvas_base_url=base_url,
        canvas_token=token,
        course_id=course_id,
        grace_days=int(os.getenv("GRACE_DAYS", "1")),
        window_days=int(os.getenv("WINDOW_DAYS", "7")),
        assignment_group_id=os.getenv("ASSIGNMENT_GROUP_ID") or None,
        dry_run=env_bool("DRY_RUN", True),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        timezone=os.getenv("TIMEZONE", "America/Denver"),
        enforce_thursday_5pm=env_bool("ENFORCE_THURSDAY_5PM", False),
        require_complete_incomplete=env_bool("REQUIRE_COMPLETE_INCOMPLETE", True),
    )


# ----------------------------
# Logging
# ----------------------------

def setup_logging(level: str) -> None:
    # force=True ensures logging is configured even if something set handlers earlier
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
        force=True,
    )


# ----------------------------
# Canvas API Client
# ----------------------------

class CanvasClient:
    def __init__(self, base_url: str, token: str, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })
        self.timeout = timeout

    def _request(self, method: str, path: str, *, params: dict | None = None, data: dict | None = None) -> requests.Response:
        url = f"{self.base_url}{path}"

        max_attempts = 8
        backoff = 2.0

        for attempt in range(1, max_attempts + 1):
            try:
                resp = self.session.request(
                    method,
                    url,
                    params=params,
                    data=data,
                    timeout=self.timeout,
                )
            except requests.RequestException as e:
                if attempt == max_attempts:
                    raise
                sleep_s = min(60, backoff ** attempt * 0.25)
                logging.warning(f"Request error ({e}); retrying in {sleep_s:.1f}s (attempt {attempt}/{max_attempts})")
                time.sleep(sleep_s)
                continue

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                sleep_s = int(retry_after) + 1 if (retry_after and retry_after.isdigit()) else min(60, int(backoff ** attempt))
                logging.warning(f"Rate limited (429). Sleeping {sleep_s}s then retrying (attempt {attempt}/{max_attempts})")
                time.sleep(sleep_s)
                continue

            if 500 <= resp.status_code <= 599:
                if attempt == max_attempts:
                    return resp
                sleep_s = min(60, backoff ** attempt * 0.25)
                logging.warning(f"Server error {resp.status_code}. Sleeping {sleep_s:.1f}s then retrying (attempt {attempt}/{max_attempts})")
                time.sleep(sleep_s)
                continue

            return resp

        return resp  # unreachable

    @staticmethod
    def _parse_link_header(link_header: str) -> Dict[str, str]:
        links: Dict[str, str] = {}
        if not link_header:
            return links
        parts = [p.strip() for p in link_header.split(",")]
        for part in parts:
            if ";" not in part:
                continue
            url_part, *param_parts = [x.strip() for x in part.split(";")]
            if not (url_part.startswith("<") and url_part.endswith(">")):
                continue
            url = url_part[1:-1]
            rel = None
            for prm in param_parts:
                if prm.startswith("rel="):
                    rel = prm.split("=", 1)[1].strip('"')
            if rel:
                links[rel] = url
        return links

    def _get_paginated(self, path: str, *, params: dict | None = None) -> Iterable[Dict[str, Any]]:
        resp = self._request("GET", path, params=params)
        if not resp.ok:
            raise RuntimeError(f"GET {path} failed: {resp.status_code} {resp.text}")

        data = resp.json()
        if isinstance(data, list):
            for item in data:
                yield item
        else:
            yield data

        link = resp.headers.get("Link", "")
        links = self._parse_link_header(link)
        next_url = links.get("next")

        while next_url:
            respN = self.session.get(next_url, timeout=self.timeout)
            if respN.status_code == 429:
                retry_after = respN.headers.get("Retry-After")
                sleep_s = int(retry_after) + 1 if (retry_after and retry_after.isdigit()) else 10
                logging.warning(f"Rate limited (429) on next page. Sleeping {sleep_s}s.")
                time.sleep(sleep_s)
                continue
            if not respN.ok:
                raise RuntimeError(f"GET next page failed: {respN.status_code} {respN.text}")

            dataN = respN.json()
            if isinstance(dataN, list):
                for item in dataN:
                    yield item
            else:
                yield dataN

            linkN = respN.headers.get("Link", "")
            linksN = self._parse_link_header(linkN)
            next_url = linksN.get("next")

    def list_assignments(self, course_id: str) -> List[Dict[str, Any]]:
        return list(self._get_paginated(
            f"/api/v1/courses/{course_id}/assignments",
            params={"per_page": 100},
        ))

    def list_submissions(self, course_id: str, assignment_id: int) -> List[Dict[str, Any]]:
        return list(self._get_paginated(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions",
            params={"per_page": 100, "include[]": ["user"]},
        ))

    def update_submission_grade(self, course_id: str, assignment_id: int, user_id: int, posted_grade: str) -> None:
        resp = self._request(
            "PUT",
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}",
            data={"submission[posted_grade]": posted_grade},
        )
        if not resp.ok:
            raise RuntimeError(
                f"PUT grade failed for assignment {assignment_id} user {user_id}: {resp.status_code} {resp.text}"
            )


# ----------------------------
# Business Logic
# ----------------------------

def parse_canvas_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    return dtparser.isoparse(dt_str)


def is_within_window(due_at: datetime, start: datetime, end: datetime) -> bool:
    return start <= due_at < end


def should_run_now(cfg: Config) -> bool:
    if not cfg.enforce_thursday_5pm:
        return True
    tz = ZoneInfo(cfg.timezone)
    now = datetime.now(tz)
    return (now.weekday() == 3) and (now.hour == 17) and (now.minute == 0)


def compute_due_window(cfg: Config) -> Tuple[datetime, datetime]:
    tz = ZoneInfo(cfg.timezone)
    now_local = datetime.now(tz)
    start_local = now_local - timedelta(days=(cfg.grace_days + cfg.window_days))
    end_local = now_local - timedelta(days=cfg.grace_days)
    return start_local, end_local


def normalize_ci_grade(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    v = str(value).strip().lower()
    if v in {"complete", "incomplete"}:
        return v
    return None


def main() -> int:
    cfg = load_config()
    setup_logging(cfg.log_level)

    if not should_run_now(cfg):
        tz = ZoneInfo(cfg.timezone)
        now = datetime.now(tz)
        logging.info(f"ENFORCE_THURSDAY_5PM is on. Now is {now.isoformat()} — not Thu 5:00pm. Exiting.")
        return 0

    start_dt, end_dt = compute_due_window(cfg)
    logging.info(f"Due-date grading window (local {cfg.timezone}): [{start_dt.isoformat()} , {end_dt.isoformat()})")
    logging.info(f"DRY_RUN={cfg.dry_run} | COURSE_ID={cfg.course_id} | ASSIGNMENT_GROUP_ID={cfg.assignment_group_id or 'None'}")

    client = CanvasClient(cfg.canvas_base_url, cfg.canvas_token)

    assignments = client.list_assignments(cfg.course_id)
    logging.info(f"Fetched {len(assignments)} assignments from course.")

    eligible: List[Dict[str, Any]] = []
    for a in assignments:
        due_at = parse_canvas_datetime(a.get("due_at"))
        if due_at is None:
            continue

        if cfg.assignment_group_id and str(a.get("assignment_group_id")) != str(cfg.assignment_group_id):
            continue

        if cfg.require_complete_incomplete and a.get("grading_type") != "complete_incomplete":
            continue

        if is_within_window(due_at, start_dt, end_dt):
            eligible.append(a)

    eligible.sort(key=lambda x: x.get("due_at") or "")
    logging.info(f"Eligible assignments in window: {len(eligible)}")

    total_updates = 0
    total_skips_same = 0
    total_errors = 0

    for a in eligible:
        assignment_id = int(a["id"])
        name = a.get("name", f"(assignment {assignment_id})")
        logging.info(f"Processing assignment {assignment_id}: {name} | due_at={a.get('due_at')}")

        try:
            submissions = client.list_submissions(cfg.course_id, assignment_id)
        except Exception as e:
            total_errors += 1
            logging.exception(f"Failed to fetch submissions for assignment {assignment_id}: {e}")
            continue

        updates_for_assignment = 0
        skips_for_assignment = 0

        for s in submissions:
            user_id = s.get("user_id")
            if user_id is None:
                continue

            submitted_at = parse_canvas_datetime(s.get("submitted_at"))
            #desired = "complete" if submitted_at is not None else "incomplete"
            
            # Decide what to post based on assignment grading_type
            grading_type = a.get("grading_type")
            points_possible = a.get("points_possible")

            if grading_type == "complete_incomplete":
                desired = "complete" if submitted_at is not None else "incomplete"
            else:
                # For point-based completion (e.g., 0/1), post numeric points:
                # Complete -> full points, Incomplete -> 0
                full_points = int(points_possible) if points_possible is not None else 1
                desired = str(full_points) if submitted_at is not None else "0"


            current = normalize_ci_grade(s.get("posted_grade")) or normalize_ci_grade(s.get("grade"))
            if current == desired:
                skips_for_assignment += 1
                continue

            if cfg.dry_run:
                updates_for_assignment += 1
                logging.info(f"[DRY_RUN] Would set user {user_id} -> {desired} (submitted_at={s.get('submitted_at')})")
                continue

            try:
                client.update_submission_grade(cfg.course_id, assignment_id, int(user_id), desired)
                updates_for_assignment += 1
            except Exception as e:
                total_errors += 1
                logging.exception(f"Failed to update grade for assignment {assignment_id} user {user_id}: {e}")

        total_updates += updates_for_assignment
        total_skips_same += skips_for_assignment
        logging.info(
            f"Done assignment {assignment_id}: updates={updates_for_assignment}, unchanged_skips={skips_for_assignment}, submissions={len(submissions)}"
        )

    logging.info("----- Summary -----")
    logging.info(f"Assignments processed: {len(eligible)}")
    logging.info(f"Total updates: {total_updates} {'(DRY_RUN)' if cfg.dry_run else ''}")
    logging.info(f"Total unchanged skips: {total_skips_same}")
    logging.info(f"Total errors: {total_errors}")

    if (not cfg.dry_run) and total_errors > 0:
        return 2
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)

