"""Self-check and end-to-end tests for AutoGGForm."""

from __future__ import annotations

import json
import re
import sys
import threading
import urllib.error
import urllib.request
from collections import Counter
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app import (
    _ensure_not_google_login,
    allocate_boolean_quota,
    allocate_quotas,
    build_submission_plan,
    fill_single_submission,
    make_handler,
    sample_checkbox_options,
    scan_google_form,
)

MOCK_FORM_HTML = """<!DOCTYPE html>
<html lang="vi">
<head><meta charset="UTF-8"><title>Mock Google Form</title></head>
<body>
  <div role="heading" aria-level="1">Khảo sát thử nghiệm 2 trang</div>
  <form id="mock-form">
    <div id="page-1">
      <div role="listitem">
        <div role="heading" aria-level="3">Giới tính <span aria-label="Câu hỏi bắt buộc">*</span></div>
        <div role="radiogroup">
          <div role="radio" data-value="Nam" aria-checked="false" tabindex="0">Nam</div>
          <div role="radio" data-value="Nữ" aria-checked="false" tabindex="0">Nữ</div>
          <div role="radio" data-value="Khác" aria-checked="false" tabindex="0">Khác</div>
        </div>
      </div>

      <div role="listitem">
        <div role="heading" aria-level="3">Kênh sử dụng <span aria-label="Câu hỏi bắt buộc">*</span></div>
        <div role="list">
          <div role="listitem"><div role="checkbox" data-answer-value="Facebook" aria-checked="false" tabindex="0">Facebook</div></div>
          <div role="listitem"><div role="checkbox" data-answer-value="YouTube" aria-checked="false" tabindex="0">YouTube</div></div>
          <div role="listitem"><div role="checkbox" data-answer-value="TikTok" aria-checked="false" tabindex="0">TikTok</div></div>
        </div>
      </div>

      <div role="listitem">
        <div role="heading" aria-level="3">Thành phố</div>
        <div role="listbox" tabindex="0" aria-expanded="false">
          <span class="current-val">Chọn</span>
          <div class="dropdown-menu" style="display:none;">
            <div role="option" data-value="" aria-selected="true">Chọn</div>
            <div role="option" data-value="Hà Nội" aria-selected="false">Hà Nội</div>
            <div role="option" data-value="TP.HCM" aria-selected="false">TP.HCM</div>
          </div>
        </div>
      </div>

      <div role="button" jsname="OCpkoe" id="next-btn" tabindex="0">Tiếp</div>
    </div>

    <div id="page-2" style="display:none;">
      <div role="listitem">
        <div role="heading" aria-level="3">Mức độ hài lòng *</div>
        <div role="radiogroup">
          <div role="radio" data-value="1" aria-checked="false" tabindex="0">1</div>
          <div role="radio" data-value="2" aria-checked="false" tabindex="0">2</div>
          <div role="radio" data-value="3" aria-checked="false" tabindex="0">3</div>
          <div role="radio" data-value="4" aria-checked="false" tabindex="0">4</div>
          <div role="radio" data-value="5" aria-checked="false" tabindex="0">5</div>
        </div>
      </div>

      <div role="listitem">
        <div role="heading" aria-level="3">Góp ý thêm</div>
        <input type="text" id="comment-input" value="">
      </div>

      <div role="button" jsname="M2UYVd" id="submit-btn" tabindex="0">Gửi</div>
    </div>
  </form>

  <div id="confirmation" data-submitted="true" style="display:none;">
    Câu trả lời của bạn đã được ghi lại.
  </div>

  <script>
    document.querySelectorAll('[role="radio"]').forEach((r) => {
      r.addEventListener('click', () => {
        const group = r.closest('[role="radiogroup"]');
        group.querySelectorAll('[role="radio"]').forEach((o) => o.setAttribute('aria-checked', 'false'));
        r.setAttribute('aria-checked', 'true');
      });
    });

    document.querySelectorAll('[role="checkbox"]').forEach((c) => {
      c.addEventListener('click', () => {
        const cur = c.getAttribute('aria-checked') === 'true';
        c.setAttribute('aria-checked', cur ? 'false' : 'true');
      });
    });

    const lb = document.querySelector('[role="listbox"]');
    const menu = lb.querySelector('.dropdown-menu');
    lb.addEventListener('click', (e) => {
      const opt = e.target.closest('[role="option"]');
      if (opt) {
        lb.querySelector('.current-val').textContent = opt.textContent;
        menu.style.display = 'none';
        return;
      }
      menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
    });

    document.getElementById('next-btn').addEventListener('click', () => {
      const p1 = document.getElementById('page-1');
      const rChecked = p1.querySelector('[role="radio"][aria-checked="true"]');
      const cChecked = p1.querySelector('[role="checkbox"][aria-checked="true"]');
      if (!rChecked || !cChecked) return;
      p1.style.display = 'none';
      document.getElementById('page-2').style.display = 'block';
    });

    document.getElementById('submit-btn').addEventListener('click', async () => {
      const p2 = document.getElementById('page-2');
      const rChecked = p2.querySelector('[role="radio"][aria-checked="true"]');
      if (!rChecked) return;
      await fetch('/mock-submit', { method: 'POST', body: '{}' });
      document.getElementById('mock-form').style.display = 'none';
      document.getElementById('confirmation').style.display = 'block';
    });
  </script>
</body>
</html>
"""


def test_quota_and_sampling() -> None:
    # 1. Exact quota distribution
    plan = allocate_quotas([60.0, 40.0], 20)
    counts = Counter(plan)
    assert counts[0] == 12 and counts[1] == 8, f"Unexpected counts: {counts}"

    # 2. Partial sum (< 100%) yields None remainder
    partial = allocate_quotas([50.0, 20.0], 10)
    p_counts = Counter(partial)
    assert p_counts[0] == 5 and p_counts[1] == 2 and p_counts[None] == 3, f"Unexpected partial: {p_counts}"

    # 3. Over 100% rejected
    try:
        allocate_quotas([60.0, 50.0], 10)
        raise AssertionError("Should reject sum > 100%")
    except ValueError:
        pass

    # 4. Boolean quota exactness
    flags = allocate_boolean_quota(70.0, 20)
    assert sum(1 for x in flags if x) == 14 and len(flags) == 20

    # 5. Checkbox random range [min_select, max_select]
    for _ in range(100):
        picked = sample_checkbox_options([70.0, 20.0, 10.0, 0.0], 2, 3)
        assert 2 <= len(picked) <= 3, f"Picked count out of range: {picked}"
        assert len(set(picked)) == len(picked), "Duplicate checkbox indices"

    # 6. Build submission plan combines question_pct and checkbox range
    questions = [
        {
            "id": "q1",
            "title": "Single",
            "type": "radio",
            "required": True,
            "question_pct": 100,
            "options": ["A", "B"],
            "option_percentages": [75, 25],
        },
        {
            "id": "q2",
            "title": "Multi",
            "type": "checkbox",
            "required": False,
            "question_pct": 60,
            "min_select": 1,
            "max_select": 2,
            "options": ["X", "Y", "Z"],
            "option_percentages": [50, 30, 20],
        },
    ]
    samples = build_submission_plan(questions, 20)
    assert len(samples) == 20
    q1_counts = Counter(s["answers"]["q1"] for s in samples)
    assert q1_counts[0] == 15 and q1_counts[1] == 5
    q2_answered = [s["answers"]["q2"] for s in samples if s["answers"]["q2"]]
    assert len(q2_answered) == 12
    assert all(1 <= len(arr) <= 2 for arr in q2_answered)

    # 7. Multiple-choice independent per-option % (sum > 100%, e.g., 80 + 70 + 50 = 200%)
    multi_q = [{
        "id": "q_multi",
        "title": "Independent multi",
        "type": "checkbox",
        "required": True,
        "question_pct": 100,
        "min_select": 1,
        "max_select": 3,
        "options": ["OptA", "OptB", "OptC"],
        "option_percentages": [80, 70, 50],
    }]
    multi_samples = build_submission_plan(multi_q, 100)
    assert all(1 <= len(s["answers"]["q_multi"]) <= 3 for s in multi_samples)
    freq = Counter(idx for s in multi_samples for idx in s["answers"]["q_multi"])
    assert 65 <= freq[0] <= 95, f"OptA freq unexpected: {freq}"
    assert 55 <= freq[1] <= 85, f"OptB freq unexpected: {freq}"
    assert 35 <= freq[2] <= 65, f"OptC freq unexpected: {freq}"
    print("OK: quota & sampling tests passed")


def test_playwright_scan_and_fill() -> None:
    state = {"submitted_count": 0}

    class MockFormHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_GET(self):
            data = MOCK_FORM_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            state["submitted_count"] += 1
            body = b'{"ok":true}'
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), MockFormHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:
        url = f"http://127.0.0.1:{port}/viewform"
        scanned = scan_google_form(url, headless=True)

        assert scanned["page_count"] == 2, f"Expected 2 pages, got {scanned}"
        assert len(scanned["questions"]) == 5, f"Expected 5 questions, got {scanned['questions']}"
        assert state["submitted_count"] == 0, "Scanner must NEVER click Submit!"

        types = [q["type"] for q in scanned["questions"]]
        assert types == ["radio", "checkbox", "dropdown", "scale", "text"], f"Unexpected types: {types}"

        # Now configure and execute 1 submission
        configured = [
            {**scanned["questions"][0], "option_percentages": [0, 100, 0]},
            {**scanned["questions"][1], "question_pct": 100, "min_select": 2, "max_select": 2, "option_percentages": [50, 50, 0]},
            {**scanned["questions"][2], "option_percentages": [0, 100]},
            {**scanned["questions"][3], "option_percentages": [0, 0, 0, 100, 0]},
            {**scanned["questions"][4], "question_pct": 100, "text_values": ["Tuyệt vời"]},
        ]
        plan = build_submission_plan(configured, 1)
        res = fill_single_submission(url, configured, plan[0]["answers"], headless=True)
        assert res["status"] == "success"
        assert state["submitted_count"] == 1, f"Expected 1 submission, got {state['submitted_count']}"
        print("OK: Playwright multi-page scan (no submit) & fill (submit) passed")
    finally:
        server.shutdown()
        server.server_close()


def test_http_api_and_tokens() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler())
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:
        base = f"http://127.0.0.1:{port}"
        html = urllib.request.urlopen(base + "/").read().decode("utf-8")
        assert "AutoGGForm Control" in html

        status_json = json.loads(urllib.request.urlopen(base + "/api/status").read().decode("utf-8"))
        assert status_json["status"] == "idle"

        # Invalid start payload (sum > 100%)
        bad_payload = json.dumps({
            "url": "https://docs.google.com/forms/d/e/test/viewform",
            "total_samples": 5,
            "min_minutes": 1,
            "max_minutes": 2,
            "questions": [{
                "id": "q1",
                "title": "Q1",
                "type": "radio",
                "required": True,
                "options": ["A", "B"],
                "option_percentages": [80, 30],
            }],
        }).encode("utf-8")
        req = urllib.request.Request(
            base + "/api/start",
            data=bad_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req)
            raise AssertionError("Should return 400 when option sum > 100%")
        except urllib.error.HTTPError as err:
            assert err.code == 400

        # Risk checks: path traversal, non-dict JSON, NaN/Infinity, max_minutes > 1440, Google login detection
        for bad_path in ("/../app.py", "/..%2fapp.py"):
            try:
                urllib.request.urlopen(base + bad_path)
                raise AssertionError(f"Should reject path traversal: {bad_path}")
            except urllib.error.HTTPError as err:
                assert err.code == 404

        for raw_body in (b"[]", b"{bad json", b'{"url":"https://docs.google.com/forms/d/e/test/viewform","total_samples":1,"min_minutes":NaN,"max_minutes":1,"questions":[]}', b'{"url":"https://docs.google.com/forms/d/e/test/viewform","total_samples":1,"min_minutes":0,"max_minutes":2000,"questions":[{"id":"q1","title":"Q","type":"radio","required":true,"options":["A"],"option_percentages":[100]}]}'):
            bad_req = urllib.request.Request(
                base + "/api/start",
                data=raw_body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(bad_req)
                raise AssertionError(f"Should reject invalid payload: {raw_body}")
            except urllib.error.HTTPError as err:
                assert err.code == 400

        class _FakePage:
            def __init__(self, url: str) -> None:
                self.url = url

        try:
            _ensure_not_google_login(_FakePage("https://accounts.google.com/v3/signin/identifier"))
            raise AssertionError("Should reject accounts.google.com redirect")
        except RuntimeError:
            pass

        # CSS token check: no raw hex colors outside :root
        css_text = (ROOT_DIR / "static" / "styles.css").read_text(encoding="utf-8")
        root_end = css_text.find("*,")
        assert root_end > 0
        after_root = css_text[root_end:]
        raw_hex = re.findall(r"#[0-9a-fA-F]{3,8}\b", after_root)
        assert not raw_hex, f"Raw hex found outside :root tokens: {raw_hex}"
        print("OK: HTTP API validation & CSS token compliance passed")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    test_quota_and_sampling()
    test_playwright_scan_and_fill()
    test_http_api_and_tokens()
    print("ALL SELF-CHECKS PASSED")
