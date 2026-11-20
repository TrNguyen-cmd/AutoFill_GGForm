"""AutoGGForm backend: Google Form scanner, weighted sampler, job runner, and HTTP server."""

from __future__ import annotations

import json
import math
import mimetypes
import os
import random
import re
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

NEXT_BUTTON_TEXT_RE = re.compile(r"^(tiếp|next|tiếp tục|continue)$", re.IGNORECASE)
SUBMIT_BUTTON_TEXT_RE = re.compile(r"^(gửi|submit|enviar)$", re.IGNORECASE)
FORBIDDEN_SCAN_TEXT_RE = re.compile(
    r"^(gửi|submit|enviar|quay lại|back|xóa hết câu trả lời|clear form)$",
    re.IGNORECASE,
)


def _clean_text(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _strip_required_star(title: str) -> tuple[str, bool]:
    cleaned = _clean_text(title)
    if cleaned.endswith("*"):
        return cleaned[:-1].strip(), True
    return cleaned, False


def allocate_quotas(option_percentages: list[float], total_samples: int) -> list[int | None]:
    """Allocate exact sample counts per option using Largest Remainder Method.

    Percentages may sum to <= 100.0; any remainder up to 100.0 becomes None (skip).
    """
    if total_samples < 1:
        raise ValueError("total_samples must be >= 1")
    if any(p < -1e-6 for p in option_percentages):
        raise ValueError("option_percentages cannot contain negative values")

    total_pct = sum(option_percentages)
    if total_pct > 100.0 + 1e-6:
        raise ValueError(f"Tổng tỷ lệ % ({total_pct:.2f}%) vượt quá 100%")

    if not option_percentages:
        return [None] * total_samples

    buckets: list[tuple[int | None, float]] = [
        (idx, (max(0.0, float(pct)) / 100.0) * total_samples)
        for idx, pct in enumerate(option_percentages)
    ]
    remainder_pct = max(0.0, 100.0 - total_pct)
    if remainder_pct > 1e-6:
        buckets.append((None, (remainder_pct / 100.0) * total_samples))

    floors: list[list[Any]] = []
    fracs: list[float] = []
    allocated = 0
    for key, exact in buckets:
        base = int(math.floor(exact + 1e-9))
        frac = max(0.0, exact - base)
        floors.append([key, base])
        fracs.append(frac)
        allocated += base

    missing = total_samples - allocated
    if missing > 0:
        for idx in sample_checkbox_options(fracs, missing, missing):
            floors[idx][1] += 1

    plan: list[int | None] = []
    for key, count in floors:
        plan.extend([key] * count)

    random.shuffle(plan)
    return plan[:total_samples]


def allocate_boolean_quota(probability_pct: float, total_samples: int) -> list[bool]:
    """Return a shuffled boolean list of length total_samples matching probability_pct."""
    if total_samples < 1:
        raise ValueError("total_samples must be >= 1")
    if probability_pct < -1e-6 or probability_pct > 100.0 + 1e-6:
        raise ValueError("probability_pct must be between 0 and 100")

    clamped = max(0.0, min(100.0, float(probability_pct)))
    exact = (clamped / 100.0) * total_samples
    base = int(math.floor(exact + 1e-9))
    frac = max(0.0, exact - base)
    true_count = base + (1 if random.random() < frac else 0)
    true_count = max(0, min(total_samples, true_count))
    flags = [True] * true_count + [False] * (total_samples - true_count)
    random.shuffle(flags)
    return flags


def sample_checkbox_options(
    option_weights: list[float],
    min_select: int,
    max_select: int,
) -> list[int]:
    """Pick a random count in [min_select, max_select] of distinct options using weights."""
    n = len(option_weights)
    if n == 0:
        return []

    lo = max(0, min(int(min_select), n))
    hi = max(lo, min(int(max_select), n))
    k = random.randint(lo, hi)
    if k == 0:
        return []

    positive_pool = [(idx, float(w)) for idx, w in enumerate(option_weights) if float(w) > 0.0]
    zero_pool = [idx for idx, w in enumerate(option_weights) if float(w) <= 0.0]

    selected: list[int] = []
    pool = list(positive_pool)
    while pool and len(selected) < k:
        total_w = sum(w for _, w in pool)
        pick = random.uniform(0.0, total_w)
        upto = 0.0
        chosen_pos = len(pool) - 1
        for pos, (_, w) in enumerate(pool):
            upto += w
            if upto >= pick:
                chosen_pos = pos
                break
        idx, _ = pool.pop(chosen_pos)
        selected.append(idx)

    if len(selected) < k and zero_pool:
        random.shuffle(zero_pool)
        needed = k - len(selected)
        selected.extend(zero_pool[:needed])

    return sorted(selected)


def _validate_question_config(q: dict[str, Any]) -> None:
    qtype = q.get("type", "text")
    title = q.get("title", "Câu hỏi")
    q_pct = float(q.get("question_pct", 100.0))
    if not math.isfinite(q_pct) or q_pct < -1e-6 or q_pct > 100.0 + 1e-6:
        raise ValueError(f"Tỷ lệ chọn câu hỏi '{title}' phải nằm trong khoảng 0% - 100%")

    if qtype in ("radio", "dropdown", "scale"):
        pcts = [float(x) for x in q.get("option_percentages", [])]
        if any((not math.isfinite(p)) or p < -1e-6 for p in pcts):
            raise ValueError(f"Tỷ lệ % đáp án ở '{title}' không hợp lệ")
        if sum(pcts) > 100.0 + 1e-6:
            raise ValueError(f"Tổng tỷ lệ % ở '{title}' vượt quá 100%")
        if q.get("required") and pcts and sum(pcts) <= 1e-6:
            raise ValueError(f"Câu bắt buộc '{title}' cần tổng tỷ lệ % đáp án lớn hơn 0%")
    elif qtype == "checkbox":
        pcts = [float(x) for x in q.get("option_percentages", [])]
        if any((not math.isfinite(p)) or p < -1e-6 or p > 100.0 + 1e-6 for p in pcts):
            raise ValueError(f"Tỷ lệ % đáp án ở '{title}' phải nằm trong khoảng 0% - 100%")
        opts_len = len(q.get("options", []))
        min_sel = int(q.get("min_select", 1))
        max_sel = int(q.get("max_select", max(1, opts_len)))
        if min_sel < 0 or max_sel < min_sel:
            raise ValueError(f"Khoảng số lượng ngẫu nhiên ở '{title}' không hợp lệ")
        if q.get("required") and q_pct > 0 and min_sel == 0:
            raise ValueError(f"Câu nhiều lựa chọn bắt buộc '{title}' cần chọn tối thiểu 1 đáp án")
    elif qtype == "grid_radio":
        rows = q.get("rows", [])
        row_pcts = q.get("row_percentages") or [q.get("option_percentages", []) for _ in rows]
        for r_idx, pcts in enumerate(row_pcts):
            vals = [float(x) for x in pcts]
            if any((not math.isfinite(p)) or p < -1e-6 for p in vals):
                raise ValueError(f"Tỷ lệ % dòng {r_idx + 1} ở '{title}' không hợp lệ")
            if sum(vals) > 100.0 + 1e-6:
                raise ValueError(f"Tổng tỷ lệ % dòng {r_idx + 1} ở '{title}' vượt quá 100%")
    elif qtype == "grid_checkbox":
        rows = q.get("rows", [])
        row_pcts = q.get("row_percentages") or [q.get("option_percentages", []) for _ in rows]
        for r_idx, pcts in enumerate(row_pcts):
            vals = [float(x) for x in pcts]
            if any((not math.isfinite(p)) or p < -1e-6 or p > 100.0 + 1e-6 for p in vals):
                raise ValueError(f"Tỷ lệ % dòng {r_idx + 1} ở '{title}' phải nằm trong khoảng 0% - 100%")
        min_sel = int(q.get("min_select", 1))
        max_sel = int(q.get("max_select", max(1, len(q.get("columns", [])))))
        if min_sel < 0 or max_sel < min_sel:
            raise ValueError(f"Khoảng số lượng ở '{title}' không hợp lệ")


def plan_checkbox_schedule(
    option_percentages: list[float],
    total_samples: int,
    min_select: int,
    max_select: int,
    answer_flags: list[bool],
) -> list[list[int]]:
    """Build per-sample checkbox selections where each option matches its independent 0-100% rate."""
    num_opts = len(option_percentages)
    schedule: list[list[int]] = [[] for _ in range(total_samples)]
    active_indices = [i for i, flag in enumerate(answer_flags) if flag]
    active_count = len(active_indices)
    if active_count == 0 or num_opts == 0:
        return schedule

    lo = max(0, min(int(min_select), num_opts))
    hi = max(lo, min(int(max_select), num_opts))
    decks = [allocate_boolean_quota(float(pct), active_count) for pct in option_percentages]

    for pos, sample_idx in enumerate(active_indices):
        picked = [j for j in range(num_opts) if decks[j][pos]]
        if len(picked) < lo:
            target_k = random.randint(lo, hi)
            need = target_k - len(picked)
            if need > 0:
                rem = [j for j in range(num_opts) if j not in picked]
                rem_w = [max(0.0, float(option_percentages[j])) for j in rem]
                picked.extend(rem[idx] for idx in sample_checkbox_options(rem_w, need, need))
        elif len(picked) > hi:
            target_k = random.randint(lo, hi)
            picked_w = [max(0.0, float(option_percentages[j])) for j in picked]
            picked = [picked[idx] for idx in sample_checkbox_options(picked_w, target_k, target_k)]
        schedule[sample_idx] = sorted(picked)

    return schedule


def build_submission_plan(questions: list[dict[str, Any]], total_samples: int) -> list[dict[str, Any]]:
    """Build exact per-sample answer schedules across all configured questions."""
    if total_samples < 1:
        raise ValueError("Số mẫu khảo sát mong muốn phải >= 1")

    for q in questions:
        _validate_question_config(q)

    # Pre-allocate per-question schedules so quota percentages hold exactly over total_samples
    per_question_schedules: dict[str, list[Any]] = {}

    for q in questions:
        qid = str(q["id"])
        qtype = q.get("type", "text")
        q_pct = float(q.get("question_pct", 100.0))
        answer_flags = allocate_boolean_quota(q_pct, total_samples)

        if qtype in ("radio", "dropdown", "scale"):
            pcts = [float(x) for x in q.get("option_percentages", [])]
            if not pcts:
                per_question_schedules[qid] = [None] * total_samples
                continue
            quotas = allocate_quotas(pcts, total_samples)
            schedule = [
                quotas[i] if answer_flags[i] else None
                for i in range(total_samples)
            ]
            per_question_schedules[qid] = schedule

        elif qtype == "checkbox":
            pcts = [float(x) for x in q.get("option_percentages", [])]
            opts_len = len(q.get("options", []))
            if not pcts and opts_len > 0:
                pcts = [50.0] * opts_len
            min_sel = int(q.get("min_select", 1))
            max_sel = int(q.get("max_select", max(1, opts_len)))
            per_question_schedules[qid] = plan_checkbox_schedule(
                pcts, total_samples, min_sel, max_sel, answer_flags
            )

        elif qtype == "grid_radio":
            rows = q.get("rows", [])
            row_pcts = q.get("row_percentages") or [q.get("option_percentages", []) for _ in rows]
            row_quotas = [allocate_quotas([float(x) for x in rp], total_samples) for rp in row_pcts]
            schedule = []
            for i in range(total_samples):
                if answer_flags[i]:
                    schedule.append([rq[i] for rq in row_quotas])
                else:
                    schedule.append([None for _ in rows])
            per_question_schedules[qid] = schedule

        elif qtype == "grid_checkbox":
            rows = q.get("rows", [])
            cols_len = len(q.get("columns", []))
            row_pcts = q.get("row_percentages") or [q.get("option_percentages", []) for _ in rows]
            min_sel = int(q.get("min_select", 1))
            max_sel = int(q.get("max_select", max(1, cols_len)))
            row_schedules = [
                plan_checkbox_schedule([float(x) for x in rp], total_samples, min_sel, max_sel, answer_flags)
                for rp in row_pcts
            ]
            per_question_schedules[qid] = [
                [rs[i] for rs in row_schedules]
                for i in range(total_samples)
            ]

        else:  # text
            pool = [line.strip() for line in q.get("text_values", []) if str(line).strip()]
            if not pool:
                pool = ["Mẫu khảo sát"] if q.get("required") else []
            schedule = [
                (random.choice(pool) if (answer_flags[i] and pool) else "")
                for i in range(total_samples)
            ]
            per_question_schedules[qid] = schedule

    samples: list[dict[str, Any]] = []
    for i in range(total_samples):
        answers: dict[str, Any] = {}
        for q in questions:
            qid = str(q["id"])
            answers[qid] = per_question_schedules[qid][i]
        samples.append({"sample_index": i + 1, "answers": answers})
    return samples


def _extract_choice_label(el: Any) -> str:
    """Extract human-readable option text from a Google Form radio/checkbox node."""
    # Prefer visible label text inside Google Forms option wrappers when data-value is __other_option__
    data_val = el.get_attribute("data-value") or el.get_attribute("data-answer-value")
    aria_label = el.get_attribute("aria-label")
    text_val = el.evaluate(
        """(node) => {
            const container = node.closest('.docssharedWizToggleLabeledContainer, .nWQGrd, .eBFwI, label') || node.parentElement;
            if (container) {
                const labelEl = container.querySelector('.aDTYNe, .YEVVod, .ulDsOb, span[dir="auto"]');
                if (labelEl && labelEl.innerText.trim()) return labelEl.innerText.trim();
            }
            return (node.innerText || '').trim();
        }"""
    )
    for candidate in (data_val, text_val, aria_label):
        cleaned = _clean_text(candidate)
        if cleaned and cleaned != "__other_option__":
            return cleaned
    return "Tùy chọn"


def _extract_item_header(item: Any) -> tuple[str, bool]:
    """Extract question title and required flag from a div[role='listitem']."""
    heading = item.query_selector('[role="heading"]')
    raw_title = heading.inner_text() if heading else ""
    title, star_req = _strip_required_star(raw_title)
    req_marker = item.query_selector(
        '[aria-label*="bắt buộc" i], [aria-label*="Required" i], span.vnumgf, [aria-required="true"]'
    )
    return title or "Câu hỏi chưa đặt tên", bool(star_req or req_marker)


def _guess_dummy_text(item: Any, title: str) -> str:
    inp = item.query_selector('input:not([type="hidden"]), textarea')
    if not inp:
        return "Mẫu thử"
    itype = (inp.get_attribute("type") or "text").lower()
    lower_title = title.lower()
    if itype == "email" or "email" in lower_title or "thư điện tử" in lower_title:
        return "test@example.com"
    if itype == "date":
        return "2025-01-01"
    if itype == "time":
        return "09:00"
    if itype == "url":
        return "https://example.com"
    if itype == "number" or any(k in lower_title for k in ("tuổi", "age", "số lượng", "năm sinh")):
        return "20"
    return "Mẫu thử"


def _click_safe(el: Any) -> None:
    el.scroll_into_view_if_needed()
    el.click()


def _find_navigation_button(page: Page, kind: str) -> Any | None:
    """Locate the visible Next ('Tiếp') or Submit ('Gửi') button.

    kind is 'next' or 'submit'. Never returns Submit when kind == 'next'.
    """
    if kind == "next":
        btn = page.query_selector('div[role="button"][jsname="OCpkoe"], button[jsname="OCpkoe"]')
        if btn and btn.is_visible():
            return btn
        for candidate in page.query_selector_all('div[role="button"], button'):
            if not candidate.is_visible():
                continue
            txt = _clean_text(candidate.inner_text())
            if FORBIDDEN_SCAN_TEXT_RE.match(txt):
                continue
            if NEXT_BUTTON_TEXT_RE.match(txt):
                return candidate
        return None

    if kind == "submit":
        btn = page.query_selector(
            'div[role="button"][jsname="M2UYVd"], button[jsname="M2UYVd"], button[type="submit"]'
        )
        if btn and btn.is_visible():
            return btn
        for candidate in page.query_selector_all('div[role="button"], button'):
            if not candidate.is_visible():
                continue
            txt = _clean_text(candidate.inner_text())
            if SUBMIT_BUTTON_TEXT_RE.match(txt):
                return candidate
        return None

    return None


def _read_dropdown_options(page: Page, listbox: Any) -> tuple[list[str], list[Any]]:
    """Open a Google Form listbox and return valid option labels and element handles."""
    _click_safe(listbox)
    page.wait_for_timeout(300)
    # Google Forms places active dropdown options either inside listbox or in a floating popup
    options = listbox.query_selector_all('[role="option"]')
    if len(options) <= 1:
        options = page.query_selector_all('.OA0qNb [role="option"], [role="listbox"] [role="option"]')

    valid_labels: list[str] = []
    valid_handles: list[Any] = []
    seen_labels: set[str] = set()
    for opt in options:
        if not opt.is_visible():
            continue
        data_val = opt.get_attribute("data-value")
        text = _clean_text(opt.inner_text())
        if data_val == "" or text.lower() in {"chọn", "choose", ""}:
            continue
        label = _clean_text(data_val or text)
        if label and label not in seen_labels:
            seen_labels.add(label)
            valid_labels.append(label)
            valid_handles.append(opt)
    return valid_labels, valid_handles


def _get_question_items(page: Page) -> list[Any]:
    """Return top-level visible question items, excluding nested option listitems."""
    items: list[Any] = []
    for el in page.query_selector_all('div[role="listitem"]'):
        if not el.is_visible():
            continue
        is_top_question = el.evaluate(
            """(node) => {
                if (node.parentElement && node.parentElement.closest('[role="listitem"]')) return false;
                return Boolean(node.querySelector('[role="heading"]'));
            }"""
        )
        if is_top_question:
            items.append(el)
    return items


def _detect_checkbox_bounds(item: Any, num_options: int) -> tuple[int, int]:
    """Infer checkbox min_select and max_select from Google Forms data-params or question text."""
    if num_options <= 0:
        return 0, 0

    raw_blobs = item.evaluate(
        """(node) => {
            const out = [node.innerText || ''];
            const dp = node.getAttribute('data-params');
            if (dp) out.push(dp);
            node.querySelectorAll('[data-params]').forEach((el) => {
                const v = el.getAttribute('data-params');
                if (v) out.push(v);
            });
            return out.join('\\n');
        }"""
    )

    min_sel = 1
    max_sel: int | None = None

    # Google Forms validation codes: [7,200,["N"]] = min, [7,201,["N"]] = max, [7,202,["N"]] = exact
    for m in re.finditer(r"\[\s*7\s*,\s*(200|201|202)\s*,\s*\[\s*['\"]?(\d+)['\"]?", raw_blobs):
        code, val = m.group(1), int(m.group(2))
        if code == "200":
            min_sel = max(min_sel, val)
        elif code == "201":
            max_sel = val
        elif code == "202":
            min_sel = val
            max_sel = val

    m_min = re.search(r"(?:tối thiểu|ít nhất|at least|minimum)\s+(\d+)", raw_blobs, re.IGNORECASE)
    if m_min:
        min_sel = max(min_sel, int(m_min.group(1)))
    m_max = re.search(r"(?:tối đa|nhiều nhất|at most|maximum)\s+(\d+)", raw_blobs, re.IGNORECASE)
    if m_max:
        parsed_max = int(m_max.group(1))
        max_sel = parsed_max if max_sel is None else min(max_sel, parsed_max)

    if max_sel is None:
        max_sel = min(2, num_options)

    min_sel = max(0, min(min_sel, num_options))
    max_sel = max(min_sel, min(max_sel, num_options))
    return min_sel, max_sel


def _parse_and_select_first_on_page(page: Page, page_index: int, start_order: int) -> list[dict[str, Any]]:
    """Scan all question items on current page and select the first option on each."""
    items = _get_question_items(page)
    parsed: list[dict[str, Any]] = []
    order = start_order

    for idx_on_page, item in enumerate(items):
        radiogroups = item.query_selector_all('[role="radiogroup"]')
        checkbox_groups = item.query_selector_all('[role="group"]')
        radios = item.query_selector_all('[role="radio"]')
        checkboxes = item.query_selector_all('[role="checkbox"]')
        listbox = item.query_selector('[role="listbox"]')
        text_inputs = item.query_selector_all('input:not([type="hidden"]), textarea')

        if not (radios or checkboxes or listbox or text_inputs):
            continue

        title, required = _extract_item_header(item)
        qid = f"p{page_index}_q{len(parsed) + 1}"

        # 1. Grid Radio
        if len(radiogroups) > 1:
            rows: list[str] = []
            columns: list[str] = []
            for r_idx, rg in enumerate(radiogroups):
                row_label = _clean_text(rg.get_attribute("aria-label")) or f"Dòng {r_idx + 1}"
                rows.append(row_label)
                row_radios = rg.query_selector_all('[role="radio"]')
                if r_idx == 0:
                    columns = [_extract_choice_label(r) for r in row_radios]
                if row_radios and not any(r.get_attribute("aria-checked") == "true" for r in row_radios):
                    _click_safe(row_radios[0])
            order += 1
            parsed.append({
                "id": qid,
                "order": order,
                "page": page_index,
                "page_item_index": idx_on_page,
                "title": title,
                "type": "grid_radio",
                "required": required,
                "rows": rows,
                "columns": columns,
                "options": columns,
            })
            continue

        # 2. Grid Checkbox
        if len(checkbox_groups) > 1 and checkboxes:
            rows = []
            columns = []
            for r_idx, cg in enumerate(checkbox_groups):
                row_cbs = cg.query_selector_all('[role="checkbox"]')
                if not row_cbs:
                    continue
                row_label = _clean_text(cg.get_attribute("aria-label")) or f"Dòng {r_idx + 1}"
                rows.append(row_label)
                if not columns:
                    columns = [_extract_choice_label(c) for c in row_cbs]
                if not any(c.get_attribute("aria-checked") == "true" for c in row_cbs):
                    _click_safe(row_cbs[0])
            if rows:
                order += 1
                parsed.append({
                    "id": qid,
                    "order": order,
                    "page": page_index,
                    "page_item_index": idx_on_page,
                    "title": title,
                    "type": "grid_checkbox",
                    "required": required,
                    "rows": rows,
                    "columns": columns,
                    "options": columns,
                })
                continue

        # 3. Single Choice (Radio or Linear Scale)
        if radios:
            options = [_extract_choice_label(r) for r in radios]
            if not any(r.get_attribute("aria-checked") == "true" for r in radios):
                _click_safe(radios[0])
                # If first radio is an "Other" option, also fill its adjacent text input
                other_inp = item.query_selector('input[type="text"]')
                if other_inp and other_inp.is_visible() and not other_inp.input_value():
                    other_inp.fill("Mẫu thử")
            is_scale = len(options) >= 3 and all(re.fullmatch(r"\d+", o) for o in options)
            order += 1
            parsed.append({
                "id": qid,
                "order": order,
                "page": page_index,
                "page_item_index": idx_on_page,
                "title": title,
                "type": "scale" if is_scale else "radio",
                "required": required,
                "options": options,
            })
            continue

        # 4. Multiple Choice (Checkbox)
        if checkboxes:
            options = [_extract_choice_label(c) for c in checkboxes]
            min_sel, max_sel = _detect_checkbox_bounds(item, len(options))
            if not any(c.get_attribute("aria-checked") == "true" for c in checkboxes):
                _click_safe(checkboxes[0])
                other_inp = item.query_selector('input[type="text"]')
                if other_inp and other_inp.is_visible() and not other_inp.input_value():
                    other_inp.fill("Mẫu thử")
            order += 1
            parsed.append({
                "id": qid,
                "order": order,
                "page": page_index,
                "page_item_index": idx_on_page,
                "title": title,
                "type": "checkbox",
                "required": required,
                "min_select": min_sel,
                "max_select": max_sel,
                "options": options,
            })
            continue

        # 5. Dropdown
        if listbox:
            options, handles = _read_dropdown_options(page, listbox)
            if handles:
                _click_safe(handles[0])
                page.wait_for_timeout(200)
            else:
                page.keyboard.press("Escape")
            order += 1
            parsed.append({
                "id": qid,
                "order": order,
                "page": page_index,
                "page_item_index": idx_on_page,
                "title": title,
                "type": "dropdown",
                "required": required,
                "options": options,
            })
            continue

        # 6. Text / Date / Time / Paragraph
        if text_inputs:
            dummy = _guess_dummy_text(item, title)
            for tinp in text_inputs:
                if tinp.is_visible() and not tinp.input_value():
                    tinp.fill(dummy)
            order += 1
            parsed.append({
                "id": qid,
                "order": order,
                "page": page_index,
                "page_item_index": idx_on_page,
                "title": title,
                "type": "text",
                "required": required,
                "options": [],
            })

    return parsed


def _advance_to_next_page(page: Page, next_btn: Any, page_idx: int) -> None:
    """Click Next and wait until either DOM content swaps or full document navigation completes."""
    prev_sig = page.evaluate(
        """() => {
            const hs = [...document.querySelectorAll('div[role="listitem"] [role="heading"]')];
            const vis = hs.filter(h => h.offsetParent !== null).map(h => h.innerText.trim());
            const bodyHead = (document.body ? document.body.innerText : '').slice(0, 300);
            return JSON.stringify({ vis, bodyHead });
        }"""
    )
    _click_safe(next_btn)
    try:
        page.wait_for_function(
            """(prev) => {
                const hs = [...document.querySelectorAll('div[role="listitem"] [role="heading"]')];
                const vis = hs.filter(h => h.offsetParent !== null).map(h => h.innerText.trim());
                const bodyHead = (document.body ? document.body.innerText : '').slice(0, 300);
                return JSON.stringify({ vis, bodyHead }) !== prev;
            }""",
            arg=prev_sig,
            timeout=15000,
        )
    except Exception as exc:
        if "Execution context was destroyed" not in str(exc) and "Target closed" not in str(exc):
            raise RuntimeError(
                f"Không thể chuyển tiếp từ trang {page_idx} (có thể còn câu hỏi bắt buộc hoặc ràng buộc chưa hợp lệ)."
            ) from exc

    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    page.wait_for_selector('div[role="listitem"], form', timeout=15000)
    page.wait_for_timeout(450)


def _ensure_not_google_login(page: Page) -> None:
    """Abort clearly if Google redirected to an authentication wall."""
    parsed = urlparse(page.url or "")
    if (parsed.hostname or "").lower() == "accounts.google.com":
        raise RuntimeError(
            "Google Form này yêu cầu đăng nhập tài khoản Google nên không thể quét/điền tự động."
        )


def scan_google_form(url: str, headless: bool = True) -> dict[str, Any]:
    """Open a Google Form, select first answers page-by-page to scan all questions, and NEVER click Submit."""
    # shortcut: conditional branching follows only the first-option path; forms with hidden branches require separate scans.
    all_questions: list[dict[str, Any]] = []
    form_title = ""
    page_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(locale="vi-VN")
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            _ensure_not_google_login(page)
            page.wait_for_selector('div[role="listitem"], form', timeout=15000)
            page.wait_for_timeout(500)

            title_el = page.query_selector('[role="heading"][aria-level="1"], .F9yp7e')
            form_title = _clean_text(title_el.inner_text() if title_el else page.title())

            max_pages = 30
            for page_idx in range(1, max_pages + 1):
                page_count = page_idx
                page_questions = _parse_and_select_first_on_page(page, page_idx, len(all_questions))
                all_questions.extend(page_questions)

                next_btn = _find_navigation_button(page, "next")
                if not next_btn:
                    # Final page reached (Submit button sits here); stop immediately without submitting.
                    break

                _advance_to_next_page(page, next_btn, page_idx)
        finally:
            context.close()
            browser.close()

    return {
        "form_title": form_title or "Google Form",
        "page_count": page_count,
        "questions": all_questions,
    }


def _maybe_fill_other_input(item: Any, choice_el: Any) -> bool:
    """Fill the 'Other' text field when selected; return True if Other option was filled/used."""
    attr_val = choice_el.get_attribute("data-value") or choice_el.get_attribute("data-answer-value") or ""
    label = _extract_choice_label(choice_el).lower().strip(": ")
    if attr_val != "__other_option__" and not (label.startswith("mục khác") or label == "other"):
        return False
    other_inp = item.query_selector('input[type="text"]')
    if other_inp and other_inp.is_visible() and not other_inp.input_value():
        other_inp.fill("Khác")
    return True


def _apply_answer_to_item(page: Page, item: Any, q: dict[str, Any], answer: Any) -> str:
    """Apply a planned answer to a single question item on the active Playwright page."""
    qtype = q.get("type", "text")

    if qtype in ("radio", "scale"):
        if answer is None:
            return "Bỏ qua"
        radios = item.query_selector_all('[role="radio"]')
        idx = int(answer)
        if 0 <= idx < len(radios):
            _click_safe(radios[idx])
            if _maybe_fill_other_input(item, radios[idx]):
                return "Khác"
            return q["options"][idx] if idx < len(q.get("options", [])) else f"#{idx + 1}"
        return "Không khớp"

    if qtype == "checkbox":
        indices = answer or []
        checkboxes = item.query_selector_all('[role="checkbox"]')
        picked_labels: list[str] = []
        for idx in indices:
            if 0 <= idx < len(checkboxes):
                if checkboxes[idx].get_attribute("aria-checked") != "true":
                    _click_safe(checkboxes[idx])
                if _maybe_fill_other_input(item, checkboxes[idx]):
                    picked_labels.append("Khác")
                else:
                    opts = q.get("options", [])
                    picked_labels.append(opts[idx] if idx < len(opts) else f"#{idx + 1}")
        return ", ".join(picked_labels) if picked_labels else "Bỏ qua"

    if qtype == "dropdown":
        if answer is None:
            return "Bỏ qua"
        listbox = item.query_selector('[role="listbox"]')
        if not listbox:
            return "Không khớp"
        labels, handles = _read_dropdown_options(page, listbox)
        idx = int(answer)
        if 0 <= idx < len(handles):
            _click_safe(handles[idx])
            page.wait_for_timeout(200)
            return labels[idx]
        page.keyboard.press("Escape")
        return "Không khớp"

    if qtype == "grid_radio":
        row_picks = answer or []
        radiogroups = item.query_selector_all('[role="radiogroup"]')
        for r_idx, col_idx in enumerate(row_picks):
            if col_idx is None or r_idx >= len(radiogroups):
                continue
            row_radios = radiogroups[r_idx].query_selector_all('[role="radio"]')
            if 0 <= int(col_idx) < len(row_radios):
                _click_safe(row_radios[int(col_idx)])
        return f"{len(row_picks)} dòng"

    if qtype == "grid_checkbox":
        row_picks = answer or []
        groups = [g for g in item.query_selector_all('[role="group"]') if g.query_selector('[role="checkbox"]')]
        for r_idx, col_indices in enumerate(row_picks):
            if r_idx >= len(groups):
                continue
            row_cbs = groups[r_idx].query_selector_all('[role="checkbox"]')
            for col_idx in col_indices:
                if 0 <= int(col_idx) < len(row_cbs):
                    if row_cbs[int(col_idx)].get_attribute("aria-checked") != "true":
                        _click_safe(row_cbs[int(col_idx)])
        return f"{len(row_picks)} dòng"

    # text
    val = str(answer or "")
    if val:
        for tinp in item.query_selector_all('input:not([type="hidden"]), textarea'):
            if tinp.is_visible():
                tinp.fill(val)
                break
        return val
    return "Để trống"


def fill_single_submission(
    url: str,
    questions: list[dict[str, Any]],
    sample_answers: dict[str, Any],
    headless: bool = True,
) -> dict[str, Any]:
    """Open the Google Form, fill one sample across all pages, and click Submit."""
    by_page: dict[int, list[dict[str, Any]]] = {}
    for q in questions:
        by_page.setdefault(int(q.get("page", 1)), []).append(q)

    summary_parts: list[str] = []
    answers_by_title: dict[str, str] = {q["title"]: "" for q in questions}
    used_qids: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(locale="vi-VN")
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            _ensure_not_google_login(page)
            page.wait_for_selector('div[role="listitem"], form', timeout=15000)
            page.wait_for_timeout(450)

            for page_idx in range(1, 31):
                items = _get_question_items(page)
                page_qs = by_page.get(page_idx, [])

                for item in items:
                    radios = item.query_selector_all('[role="radio"]')
                    checkboxes = item.query_selector_all('[role="checkbox"]')
                    listbox = item.query_selector('[role="listbox"]')
                    text_inputs = item.query_selector_all('input:not([type="hidden"]), textarea')
                    if not (radios or checkboxes or listbox or text_inputs):
                        continue

                    dom_types: set[str] = set()
                    if radios:
                        dom_types.update(("radio", "scale", "grid_radio"))
                    if checkboxes:
                        dom_types.update(("checkbox", "grid_checkbox"))
                    if listbox:
                        dom_types.add("dropdown")
                    if text_inputs and not (radios or checkboxes):
                        dom_types.add("text")

                    item_title, _ = _extract_item_header(item)
                    matched_q = next(
                        (q for q in page_qs if q["id"] not in used_qids and q["title"] == item_title),
                        None,
                    )
                    if not matched_q:
                        matched_q = next(
                            (q for q in questions if q["id"] not in used_qids and q["title"] == item_title),
                            None,
                        )
                    if not matched_q:
                        matched_q = next(
                            (q for q in page_qs if q["id"] not in used_qids and q.get("type") in dom_types),
                            None,
                        )
                    if not matched_q:
                        continue

                    used_qids.add(matched_q["id"])
                    ans_val = sample_answers.get(matched_q["id"])
                    chosen_text = _apply_answer_to_item(page, item, matched_q, ans_val)
                    if chosen_text == "Không khớp":
                        raise RuntimeError(f"Không khớp đáp án cho câu '{matched_q['title']}' ở trang {page_idx}")
                    answers_by_title[matched_q["title"]] = chosen_text
                    summary_parts.append(f"{matched_q['title'][:24]}: {chosen_text}")

                next_btn = _find_navigation_button(page, "next")
                if next_btn:
                    _advance_to_next_page(page, next_btn, page_idx)
                    continue

                submit_btn = _find_navigation_button(page, "submit")
                if not submit_btn:
                    raise RuntimeError(f"Không tìm thấy nút Gửi (Submit) ở trang {page_idx}")

                _click_safe(submit_btn)
                confirm_js = """() => {
                    if (location.href.includes('formResponse')) return true;
                    if (document.querySelector('[data-submitted="true"], .vHW8K, a[href*="viewform"]')) return true;
                    const txt = document.body ? document.body.innerText : '';
                    return /câu trả lời của bạn đã được ghi lại|your response has been recorded/i.test(txt);
                }"""
                confirmed = False
                try:
                    page.wait_for_function(confirm_js, timeout=15000)
                    confirmed = True
                except Exception as exc:
                    if "Execution context was destroyed" in str(exc):
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=15000)
                        except Exception:
                            pass
                        page.wait_for_timeout(300)
                        confirmed = bool(page.evaluate(confirm_js))
                if not confirmed:
                    raise RuntimeError(
                        f"Gửi form thất bại ở trang {page_idx}: không nhận được xác nhận từ Google Form."
                    )
                break
        finally:
            context.close()
            browser.close()

    return {
        "status": "success",
        "summary": " | ".join(summary_parts),
        "answers": answers_by_title,
    }


class JobManager:
    """Thread-safe coordinator for scheduled Playwright form submissions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.reset_state()

    def reset_state(self) -> None:
        self.state: dict[str, Any] = {
            "status": "idle",
            "total_samples": 0,
            "completed": 0,
            "success_count": 0,
            "error_count": 0,
            "min_minutes": 0.0,
            "max_minutes": 0.0,
            "current_interval_seconds": 0,
            "countdown_seconds": 0,
            "paused": False,
            "stop_requested": False,
            "last_error": "",
            "logs": [],
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self.state,
                "logs": list(self.state["logs"]),
            }

    def start(
        self,
        url: str,
        questions: list[dict[str, Any]],
        total_samples: int,
        min_minutes: float,
        max_minutes: float,
        headless: bool = True,
    ) -> None:
        plan = build_submission_plan(questions, total_samples)
        with self._lock:
            if self.state["status"] in ("running", "waiting", "paused"):
                raise RuntimeError("Đang có tiến trình chạy, hãy dừng trước khi bắt đầu mới.")
            self._wake_event.clear()
            self.state = {
                "status": "running",
                "total_samples": total_samples,
                "completed": 0,
                "success_count": 0,
                "error_count": 0,
                "min_minutes": float(min_minutes),
                "max_minutes": float(max_minutes),
                "current_interval_seconds": 0,
                "countdown_seconds": 0,
                "paused": False,
                "stop_requested": False,
                "last_error": "",
                "logs": [],
            }
            self._thread = threading.Thread(
                target=self._worker,
                args=(url, questions, plan, float(min_minutes), float(max_minutes), headless),
                daemon=True,
            )
            self._thread.start()

    def toggle_pause(self) -> dict[str, Any]:
        with self._lock:
            if self.state["status"] in ("running", "waiting", "paused"):
                self.state["paused"] = not self.state["paused"]
                if self.state["paused"]:
                    self.state["status"] = "paused"
                else:
                    self.state["status"] = "waiting" if self.state["countdown_seconds"] > 0 else "running"
                self._wake_event.set()
            return {**self.state, "logs": list(self.state["logs"])}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self.state["status"] in ("running", "waiting", "paused"):
                self.state["stop_requested"] = True
                self.state["paused"] = False
                self._wake_event.set()
            return {**self.state, "logs": list(self.state["logs"])}

    def _append_log(
        self,
        sample_idx: int,
        status: str,
        detail: str,
        answers: dict[str, str] | None = None,
    ) -> None:
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "sample": sample_idx,
            "status": status,
            "detail": detail,
            "answers": answers or {},
        }
        with self._lock:
            self.state["logs"].insert(0, entry)
            self.state["logs"] = self.state["logs"][:100]

    def _wait_if_paused_or_stopped(self) -> bool:
        """Return True if stop was requested."""
        while True:
            with self._lock:
                if self.state["stop_requested"]:
                    return True
                if not self.state["paused"]:
                    return False
            self._wake_event.wait(timeout=0.3)
            self._wake_event.clear()

    def _worker(
        self,
        url: str,
        questions: list[dict[str, Any]],
        plan: list[dict[str, Any]],
        min_minutes: float,
        max_minutes: float,
        headless: bool,
    ) -> None:
        try:
            total = len(plan)
            for idx, sample in enumerate(plan, start=1):
                if self._wait_if_paused_or_stopped():
                    with self._lock:
                        self.state["status"] = "stopped"
                        self.state["countdown_seconds"] = 0
                    self._append_log(idx, "stopped", "Người dùng đã dừng tiến trình.")
                    return

                with self._lock:
                    self.state["status"] = "running"
                    self.state["countdown_seconds"] = 0

                try:
                    result = fill_single_submission(url, questions, sample["answers"], headless=headless)
                    with self._lock:
                        self.state["completed"] += 1
                        self.state["success_count"] += 1
                    self._append_log(
                        idx,
                        "success",
                        result.get("summary") or "Đã gửi thành công",
                        result.get("answers"),
                    )
                except Exception as exc:
                    with self._lock:
                        self.state["completed"] += 1
                        self.state["error_count"] += 1
                        self.state["last_error"] = str(exc)
                    self._append_log(idx, "error", str(exc))

                if idx < total:
                    delay_sec = random.uniform(min_minutes * 60.0, max_minutes * 60.0)
                    if delay_sec > 0.2:
                        total_wait = int(math.ceil(delay_sec))
                        with self._lock:
                            self.state["status"] = "waiting"
                            self.state["current_interval_seconds"] = total_wait
                            self.state["countdown_seconds"] = total_wait

                        remaining = delay_sec
                        while remaining > 0:
                            if self._wait_if_paused_or_stopped():
                                with self._lock:
                                    self.state["status"] = "stopped"
                                    self.state["countdown_seconds"] = 0
                                self._append_log(idx + 1, "stopped", "Đã dừng trong lúc chờ giãn cách.")
                                return
                            slice_start = time.monotonic()
                            self._wake_event.wait(timeout=min(0.5, remaining))
                            self._wake_event.clear()
                            elapsed = time.monotonic() - slice_start
                            remaining = max(0.0, remaining - elapsed)
                            with self._lock:
                                self.state["countdown_seconds"] = max(0, int(math.ceil(remaining)))

            with self._lock:
                self.state["status"] = "completed"
                self.state["countdown_seconds"] = 0
        except Exception as exc:
            with self._lock:
                self.state["status"] = "error"
                self.state["countdown_seconds"] = 0
                self.state["last_error"] = str(exc)
            self._append_log(0, "error", f"Lỗi worker: {exc}")


JOB_MANAGER = JobManager()


def make_handler() -> type[BaseHTTPRequestHandler]:
    class AppHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_cors_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def do_OPTIONS(self) -> None:
            self.send_response(HTTPStatus.NO_CONTENT)
            self._send_cors_headers()
            self.end_headers()

        def _send_json(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 5 * 1024 * 1024:
                raise ValueError("Kích thước payload không hợp lệ (tối đa 5MB)")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("JSON payload phải là object")
            return parsed

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/status":
                self._send_json(HTTPStatus.OK, JOB_MANAGER.snapshot())
                return

            rel_path = "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
            target = (STATIC_DIR / rel_path).resolve()
            if not target.is_relative_to(STATIC_DIR.resolve()) or not target.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return

            mime, _ = mimetypes.guess_type(str(target))
            data = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{mime or 'application/octet-stream'}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            try:
                body = self._read_json()
            except Exception:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "JSON payload không hợp lệ"})
                return

            if parsed.path == "/api/scan":
                url = str(body.get("url", "")).strip()
                headless = bool(body.get("headless", True))
                if not re.match(r"^https?://", url, re.IGNORECASE):
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Vui lòng nhập link bắt đầu bằng http:// hoặc https://"})
                    return
                try:
                    result = scan_google_form(url, headless=headless)
                    self._send_json(HTTPStatus.OK, result)
                except Exception as exc:
                    self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
                return

            if parsed.path == "/api/start":
                url = str(body.get("url", "")).strip()
                headless = bool(body.get("headless", True))
                questions = body.get("questions", [])
                try:
                    total_samples = int(body.get("total_samples", 1))
                    min_minutes = float(body.get("min_minutes", 0.0))
                    max_minutes = float(body.get("max_minutes", 0.0))
                except (TypeError, ValueError):
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Thông số số lượng mẫu hoặc thời gian không hợp lệ"})
                    return

                if not re.match(r"^https?://", url, re.IGNORECASE):
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Link Google Form không hợp lệ"})
                    return
                if total_samples < 1 or total_samples > 10000:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Số mẫu khảo sát phải từ 1 đến 10.000"})
                    return
                if (
                    not math.isfinite(min_minutes)
                    or not math.isfinite(max_minutes)
                    or min_minutes < 0
                    or max_minutes < min_minutes
                    or max_minutes > 1440
                ):
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Khoảng thời gian giãn cách (0 - 1440 phút) không hợp lệ"})
                    return
                if not isinstance(questions, list) or not questions:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Chưa có danh sách câu hỏi để điền"})
                    return

                try:
                    JOB_MANAGER.start(
                        url=url,
                        questions=questions,
                        total_samples=total_samples,
                        min_minutes=min_minutes,
                        max_minutes=max_minutes,
                        headless=headless,
                    )
                    self._send_json(HTTPStatus.OK, JOB_MANAGER.snapshot())
                except Exception as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return

            if parsed.path == "/api/pause":
                self._send_json(HTTPStatus.OK, JOB_MANAGER.toggle_pause())
                return

            if parsed.path == "/api/stop":
                self._send_json(HTTPStatus.OK, JOB_MANAGER.stop())
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Endpoint không tồn tại"})

    return AppHandler


def run_server(host: str = "127.0.0.1", port: int | None = None) -> None:
    chosen_port = port or int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer((host, chosen_port), make_handler())
    print(f"AutoGGForm server listening at http://{host}:{chosen_port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    run_server()
