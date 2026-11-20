(() => {
  "use strict";

  const API_BASE = window.location.protocol === "file:" ? "http://127.0.0.1:8080" : "";

  const state = {
    formTitle: "",
    pageCount: 0,
    questions: [],
    pollTimer: null,
    pollInFlight: false,
    scanning: false,
    jobStatus: "idle",
  };

  // DOM Elements
  const scanForm = document.getElementById("scan-form");
  const urlInput = document.getElementById("form-url");
  const urlError = document.getElementById("form-url-error");
  const headlessInput = document.getElementById("headless-mode");
  const btnScan = document.getElementById("btn-scan");
  const btnDemo = document.getElementById("btn-demo");
  const scanAlert = document.getElementById("scan-alert");
  const scanAlertText = document.getElementById("scan-alert-text");
  const scanMetaPill = document.getElementById("scan-meta-pill");

  const questionsContainer = document.getElementById("questions-container");
  const questionsCountPill = document.getElementById("questions-count-pill");
  const btnEqualizeAll = document.getElementById("btn-equalize-all");

  const runForm = document.getElementById("run-form");
  const totalSamplesInput = document.getElementById("total-samples");
  const totalSamplesError = document.getElementById("total-samples-error");
  const minMinutesInput = document.getElementById("min-minutes");
  const maxMinutesInput = document.getElementById("max-minutes");
  const intervalError = document.getElementById("interval-error");
  const btnStart = document.getElementById("btn-start");
  const btnPause = document.getElementById("btn-pause");
  const btnStop = document.getElementById("btn-stop");
  const runAlert = document.getElementById("run-alert");
  const runAlertText = document.getElementById("run-alert-text");

  const topStatusPill = document.getElementById("top-status-pill");
  const topStatusText = document.getElementById("top-status-text");
  const kpiProgress = document.getElementById("kpi-progress");
  const kpiCounts = document.getElementById("kpi-counts");
  const kpiCountdown = document.getElementById("kpi-countdown");
  const kpiInterval = document.getElementById("kpi-interval");
  const kpiRate = document.getElementById("kpi-rate");
  const kpiState = document.getElementById("kpi-state");
  const jobProgress = document.getElementById("job-progress");
  const progressPctPill = document.getElementById("progress-pct-pill");
  const progressLabelText = document.getElementById("progress-label-text");
  const liveAnnouncement = document.getElementById("live-announcement");
  const logsTbody = document.getElementById("logs-tbody");
  const errorSidebarBox = document.getElementById("error-sidebar-box");
  const errorSidebarText = document.getElementById("error-sidebar-text");

  const TYPE_LABELS = {
    radio: "Trắc nghiệm (1 đáp án)",
    scale: "Thang điểm tuyến tính",
    dropdown: "Danh sách thả xuống",
    checkbox: "Nhiều lựa chọn (Checkbox)",
    grid_radio: "Lưới trắc nghiệm",
    grid_checkbox: "Lưới hộp kiểm",
    text: "Câu hỏi tự luận / Nhập liệu",
  };

  function round1(val) {
    return Math.round(val * 10) / 10;
  }

  function makeEvenDistribution(count) {
    if (count <= 0) return [];
    const base = Math.floor((1000 / count)) / 10;
    const arr = Array.from({ length: count }, () => base);
    const diff = round1(100 - arr.reduce((a, b) => a + b, 0));
    arr[0] = round1(arr[0] + diff);
    return arr;
  }

  function makeCheckboxDefaults(options) {
    return options.map((opt) => {
      const lower = String(opt || "").toLowerCase().trim();
      if (lower.startsWith("mục khác") || lower === "other" || lower.startsWith("other:")) {
        return 0;
      }
      return 50;
    });
  }

  function normalizeQuestionDefaults(rawQ) {
    const q = { ...rawQ };
    const opts = Array.isArray(q.options) ? q.options : [];
    const rows = Array.isArray(q.rows) ? q.rows : [];
    const cols = Array.isArray(q.columns) ? q.columns : opts;

    q.question_pct = q.question_pct !== undefined ? Number(q.question_pct) : (q.type === "text" && !q.required ? 50 : 100);

    if (q.type === "radio" || q.type === "dropdown" || q.type === "scale") {
      q.option_percentages = Array.isArray(q.option_percentages) && q.option_percentages.length === opts.length
        ? q.option_percentages.map(Number)
        : makeEvenDistribution(opts.length);
    } else if (q.type === "checkbox") {
      q.min_select = q.min_select !== undefined ? Number(q.min_select) : (opts.length > 0 ? 1 : 0);
      q.max_select = q.max_select !== undefined ? Number(q.max_select) : Math.min(opts.length, 2);
      q.option_percentages = Array.isArray(q.option_percentages) && q.option_percentages.length === opts.length
        ? q.option_percentages.map(Number)
        : makeCheckboxDefaults(opts);
    } else if (q.type === "grid_radio" || q.type === "grid_checkbox") {
      q.rows = rows;
      q.columns = cols;
      q.row_percentages = Array.isArray(q.row_percentages) && q.row_percentages.length === rows.length
        ? q.row_percentages.map((rp) => rp.map(Number))
        : rows.map(() => (q.type === "grid_checkbox" ? makeCheckboxDefaults(cols) : makeEvenDistribution(cols.length)));
      if (q.type === "grid_checkbox") {
        q.min_select = q.min_select !== undefined ? Number(q.min_select) : (cols.length > 0 ? 1 : 0);
        q.max_select = q.max_select !== undefined ? Number(q.max_select) : cols.length;
      }
    } else {
      q.text_values = Array.isArray(q.text_values) && q.text_values.length
        ? q.text_values
        : ["Khá hài lòng", "Rất tốt", "Đáp ứng đủ nhu cầu"];
    }
    return q;
  }

  function showAlert(box, textEl, msg) {
    if (!msg) {
      box.hidden = true;
      textEl.textContent = "";
      return;
    }
    textEl.textContent = msg;
    box.hidden = false;
  }

  function formatMMSS(totalSeconds) {
    const sec = Math.max(0, Math.floor(Number(totalSeconds) || 0));
    const m = String(Math.floor(sec / 60)).padStart(2, "0");
    const s = String(sec % 60).padStart(2, "0");
    return `${m}:${s}`;
  }

  function createEl(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  }

  function updateSumBadge(badgeEl, arr, isMultiSelect = false, getBounds = null) {
    const sum = round1(arr.reduce((a, b) => a + (Number(b) || 0), 0));
    if (isMultiSelect) {
      const rawAvg = round1(sum / 100);
      const bounds = typeof getBounds === "function" ? getBounds() : null;
      if (bounds && bounds.max >= bounds.min && (rawAvg > bounds.max || rawAvg < bounds.min)) {
        const clampedAvg = round1(Math.max(bounds.min, Math.min(bounds.max, rawAvg)));
        badgeEl.textContent = `Tỷ lệ từng đáp án (0% – 100%) · Giới hạn ${bounds.min}–${bounds.max} đáp án (TB ~${clampedAvg})`;
      } else {
        badgeEl.textContent = `Tỷ lệ chọn từng đáp án (0% – 100%) · TB ~${rawAvg} đáp án/người`;
      }
      badgeEl.className = "pill pill-cyan";
      return;
    }
    badgeEl.textContent = `Tổng: ${sum}% / 100%`;
    badgeEl.className = "pill";
    if (sum > 100) {
      badgeEl.classList.add("pill-alert");
    } else if (Math.abs(sum - 100) < 0.05) {
      badgeEl.classList.add("pill-live");
    } else if (sum > 0) {
      badgeEl.classList.add("pill-cyan");
    } else {
      badgeEl.classList.add("pill-warn");
    }
  }

  function renderOptionWeightList(container, labels, percentages, idPrefix, onUpdated, isMultiSelect = false, getBounds = null) {
    const sumPill = createEl("span", "pill");
    const refreshBadge = () => updateSumBadge(sumPill, percentages, isMultiSelect, getBounds);
    refreshBadge();

    const toolbar = createEl("div", "question-toolbar");
    const leftGroup = createEl("div", "toolbar-group");
    leftGroup.appendChild(sumPill);

    const rightGroup = createEl("div", "toolbar-group");
    const rowControls = [];

    const setAllValues = (fn) => {
      labels.forEach((lblText, idx) => {
        const val = fn(lblText, idx);
        percentages[idx] = val;
        rowControls[idx].slider.value = String(val);
        rowControls[idx].numInput.value = String(val);
      });
      refreshBadge();
      onUpdated();
    };

    if (isMultiSelect) {
      const btnHalf = createEl("button", "btn btn-secondary btn-sm", "Đặt tất cả 50%");
      btnHalf.type = "button";
      const btnFull = createEl("button", "btn btn-secondary btn-sm", "Đặt tất cả 100%");
      btnFull.type = "button";
      const btnZero = createEl("button", "btn btn-secondary btn-sm", "Đặt 0%");
      btnZero.type = "button";
      btnHalf.addEventListener("click", () => setAllValues(() => 50));
      btnFull.addEventListener("click", () => setAllValues(() => 100));
      btnZero.addEventListener("click", () => setAllValues(() => 0));
      rightGroup.append(btnHalf, btnFull, btnZero);
    } else {
      const btnEqual = createEl("button", "btn btn-secondary btn-sm", "Chia đều 100%");
      btnEqual.type = "button";
      const btnZero = createEl("button", "btn btn-secondary btn-sm", "Đặt 0%");
      btnZero.type = "button";
      btnEqual.addEventListener("click", () => {
        const even = makeEvenDistribution(labels.length);
        setAllValues((_, idx) => even[idx]);
      });
      btnZero.addEventListener("click", () => setAllValues(() => 0));
      rightGroup.append(btnEqual, btnZero);
    }

    toolbar.append(leftGroup, rightGroup);
    container.appendChild(toolbar);

    labels.forEach((labelText, optIdx) => {
      const row = createEl("div", "option-row");

      const lbl = createEl("label", "option-label", labelText);
      const inputId = `${idPrefix}_opt_${optIdx}`;
      lbl.htmlFor = inputId;

      const slider = document.createElement("input");
      slider.type = "range";
      slider.className = "range-slider";
      slider.min = "0";
      slider.max = "100";
      slider.step = "0.1";
      slider.value = String(percentages[optIdx] || 0);
      slider.setAttribute("aria-label", `Thanh trượt tỷ lệ ${labelText}`);

      const pctWrap = createEl("div", "pct-input-wrap");
      const numInput = document.createElement("input");
      numInput.type = "number";
      numInput.id = inputId;
      numInput.className = "input-mini";
      numInput.min = "0";
      numInput.max = "100";
      numInput.step = "0.1";
      numInput.value = String(percentages[optIdx] || 0);
      const pctUnit = createEl("span", "field-hint", "%");
      pctWrap.append(numInput, pctUnit);

      const applyValue = (rawVal) => {
        const parsed = Number.isFinite(Number(rawVal)) ? Number(rawVal) : 0;
        let maxAllowed = 100;
        if (!isMultiSelect) {
          const otherSum = percentages.reduce((acc, v, idx) => (idx === optIdx ? acc : acc + (Number(v) || 0)), 0);
          maxAllowed = Math.max(0, round1(100 - otherSum));
        }
        const clamped = round1(Math.max(0, Math.min(maxAllowed, parsed)));
        percentages[optIdx] = clamped;
        slider.value = String(clamped);
        numInput.value = String(clamped);
        refreshBadge();
        onUpdated();
      };

      slider.addEventListener("input", () => applyValue(slider.value));
      numInput.addEventListener("input", () => {
        if (numInput.value === "") return;
        applyValue(numInput.value);
      });
      numInput.addEventListener("blur", () => {
        applyValue(numInput.value === "" ? 0 : numInput.value);
      });

      rowControls.push({ slider, numInput });
      row.append(lbl, slider, pctWrap);
      container.appendChild(row);
    });
    return refreshBadge;
  }

  function renderQuestions() {
    questionsContainer.replaceChildren();
    questionsCountPill.textContent = `${state.questions.length} câu hỏi`;
    btnEqualizeAll.disabled = state.questions.length === 0;

    if (state.questions.length === 0) {
      const empty = createEl(
        "div",
        "questions-empty",
        "Nhập link Google Form và nhấn Quét câu hỏi để hiển thị danh sách câu hỏi và cấu hình tỷ lệ phần trăm (tối đa 100%)."
      );
      questionsContainer.appendChild(empty);
      return;
    }

    const list = createEl("div", "questions-list");

    state.questions.forEach((q, qIdx) => {
      const card = createEl("article", "question-card");

      const head = createEl("div", "question-head");
      const titleWrap = document.createElement("div");
      const titleEl = createEl("h3", "question-title", `Câu ${qIdx + 1}: ${q.title}`);
      titleWrap.appendChild(titleEl);

      const meta = createEl("div", "question-meta");
      meta.appendChild(createEl("span", "pill", `Trang ${q.page || 1}`));
      meta.appendChild(createEl("span", "pill pill-cyan", TYPE_LABELS[q.type] || q.type));
      if (q.required) {
        meta.appendChild(createEl("span", "pill pill-alert", "Bắt buộc"));
      }
      head.append(titleWrap, meta);
      card.appendChild(head);

      const badgeRefiners = [];

      // Question selection % + random range for multi-select (checkbox) or optional questions
      if (q.type === "checkbox" || q.type === "grid_checkbox" || q.type === "text" || !q.required) {
        const qToolbar = createEl("div", "question-toolbar");
        const qGroup = createEl("div", "toolbar-group");

        const qPctLabel = createEl("label", "inline-number-field");
        const qPctId = `q_${q.id}_pct`;
        qPctLabel.htmlFor = qPctId;
        qPctLabel.appendChild(document.createTextNode("% Chọn câu hỏi này:"));
        const qPctInput = document.createElement("input");
        qPctInput.type = "number";
        qPctInput.id = qPctId;
        qPctInput.className = "input-mini";
        qPctInput.min = "0";
        qPctInput.max = "100";
        qPctInput.step = "1";
        qPctInput.value = String(q.question_pct ?? 100);
        qPctInput.addEventListener("input", () => {
          qPctInput.setCustomValidity("");
        });
        qPctInput.addEventListener("blur", () => {
          const val = Math.max(0, Math.min(100, Number(qPctInput.value) || 0));
          q.question_pct = round1(val);
          qPctInput.value = String(q.question_pct);
        });
        qPctLabel.append(qPctInput, document.createTextNode("%"));
        qGroup.appendChild(qPctLabel);

        if (q.type === "checkbox" || q.type === "grid_checkbox") {
          const maxPossible = q.type === "checkbox" ? q.options.length : q.columns.length;
          const minId = `q_${q.id}_min`;
          const maxId = `q_${q.id}_max`;

          const minLabel = createEl("label", "inline-number-field");
          minLabel.htmlFor = minId;
          minLabel.appendChild(document.createTextNode("Số lượng ngẫu nhiên từ:"));
          const minInp = document.createElement("input");
          minInp.type = "number";
          minInp.id = minId;
          minInp.className = "input-mini";
          minInp.min = q.required ? "1" : "0";
          minInp.max = String(maxPossible);
          minInp.step = "1";
          minInp.value = String(q.min_select);

          const maxLabel = createEl("label", "inline-number-field");
          maxLabel.htmlFor = maxId;
          maxLabel.appendChild(document.createTextNode("đến:"));
          const maxInp = document.createElement("input");
          maxInp.type = "number";
          maxInp.id = maxId;
          maxInp.className = "input-mini";
          maxInp.min = q.required ? "1" : "0";
          maxInp.max = String(maxPossible);
          maxInp.step = "1";
          maxInp.value = String(q.max_select);

          const syncRange = () => {
            const floorMin = q.required ? 1 : 0;
            let lo = Math.max(floorMin, Math.min(maxPossible, Math.round(Number(minInp.value) || 0)));
            let hi = Math.max(floorMin, Math.min(maxPossible, Math.round(Number(maxInp.value) || lo)));
            if (hi < lo) hi = lo;
            q.min_select = lo;
            q.max_select = hi;
            minInp.value = String(lo);
            maxInp.value = String(hi);
            badgeRefiners.forEach((fn) => fn());
          };

          minInp.addEventListener("blur", syncRange);
          maxInp.addEventListener("blur", syncRange);
          minInp.addEventListener("input", () => {
            minInp.setCustomValidity("");
            const val = Number(minInp.value);
            if (Number.isFinite(val)) {
              q.min_select = val;
              badgeRefiners.forEach((fn) => fn());
            }
          });
          maxInp.addEventListener("input", () => {
            maxInp.setCustomValidity("");
            const val = Number(maxInp.value);
            if (Number.isFinite(val)) {
              q.max_select = val;
              badgeRefiners.forEach((fn) => fn());
            }
          });

          minLabel.appendChild(minInp);
          maxLabel.append(maxInp, document.createTextNode(`(tối đa ${maxPossible})`));
          qGroup.append(minLabel, maxLabel);
        }

        qToolbar.appendChild(qGroup);
        card.appendChild(qToolbar);
      }

      const getBounds = () => ({ min: Number(q.min_select ?? 0), max: Number(q.max_select ?? 0) });

      // Per-option percentages
      if (q.type === "radio" || q.type === "dropdown" || q.type === "scale" || q.type === "checkbox") {
        const refiner = renderOptionWeightList(
          card,
          q.options,
          q.option_percentages,
          `q_${q.id}`,
          () => {},
          q.type === "checkbox",
          q.type === "checkbox" ? getBounds : null
        );
        badgeRefiners.push(refiner);
      } else if (q.type === "grid_radio" || q.type === "grid_checkbox") {
        q.rows.forEach((rowLabel, rIdx) => {
          const rowBlock = createEl("div", "grid-row-block");
          const rowHeading = createEl("div", "grid-row-heading", `Hàng ${rIdx + 1}: ${rowLabel}`);
          rowBlock.appendChild(rowHeading);
          const refiner = renderOptionWeightList(
            rowBlock,
            q.columns,
            q.row_percentages[rIdx],
            `q_${q.id}_r${rIdx}`,
            () => {},
            q.type === "grid_checkbox",
            q.type === "grid_checkbox" ? getBounds : null
          );
          badgeRefiners.push(refiner);
          card.appendChild(rowBlock);
        });
      } else {
        const textField = createEl("div", "field");
        const textId = `q_${q.id}_text`;
        const lbl = createEl("label", "field-label", "Danh sách câu trả lời ngẫu nhiên (mỗi dòng 1 câu trả lời):");
        lbl.htmlFor = textId;
        const ta = document.createElement("textarea");
        ta.id = textId;
        ta.className = "textarea-control";
        ta.value = (q.text_values || []).join("\n");
        ta.addEventListener("input", () => {
          q.text_values = ta.value.split("\n").map((s) => s.trim()).filter(Boolean);
        });
        textField.append(lbl, ta);
        card.appendChild(textField);
      }

      list.appendChild(card);
    });

    questionsContainer.appendChild(list);
  }

  // Validation Helpers (validate on blur/submit, clear on input)
  function validateUrlField() {
    const val = urlInput.value.trim();
    if (!val) {
      urlInput.setCustomValidity("Vui lòng nhập đường dẫn Google Form.");
      urlError.textContent = "Vui lòng nhập đường dẫn Google Form.";
      return false;
    }
    if (!/^https?:\/\//i.test(val)) {
      urlInput.setCustomValidity("Link phải bắt đầu bằng http:// hoặc https://");
      urlError.textContent = "Link phải bắt đầu bằng http:// hoặc https://";
      return false;
    }
    urlInput.setCustomValidity("");
    urlError.textContent = "";
    return true;
  }

  function validateRunFields() {
    let valid = true;
    const samples = Number(totalSamplesInput.value);
    if (!Number.isInteger(samples) || samples < 1 || samples > 10000) {
      totalSamplesInput.setCustomValidity("Số mẫu khảo sát phải là số nguyên từ 1 đến 10.000.");
      totalSamplesError.textContent = "Số mẫu khảo sát phải là số nguyên từ 1 đến 10.000.";
      valid = false;
    } else {
      totalSamplesInput.setCustomValidity("");
      totalSamplesError.textContent = "";
    }

    const minM = Number(minMinutesInput.value);
    const maxM = Number(maxMinutesInput.value);
    if (Number.isNaN(minM) || Number.isNaN(maxM) || minM < 0 || maxM < 0) {
      minMinutesInput.setCustomValidity("Số phút giãn cách không được âm.");
      intervalError.textContent = "Số phút giãn cách không được âm.";
      valid = false;
    } else if (maxM < minM) {
      maxMinutesInput.setCustomValidity("Thời gian tối đa phải lớn hơn hoặc bằng thời gian tối thiểu.");
      intervalError.textContent = "Thời gian tối đa phải lớn hơn hoặc bằng thời gian tối thiểu.";
      valid = false;
    } else {
      minMinutesInput.setCustomValidity("");
      maxMinutesInput.setCustomValidity("");
      intervalError.textContent = "";
    }
    return valid;
  }

  urlInput.addEventListener("input", () => {
    urlInput.setCustomValidity("");
    urlError.textContent = "";
  });
  urlInput.addEventListener("blur", () => {
    if (urlInput.value.trim()) validateUrlField();
  });

  totalSamplesInput.addEventListener("input", () => {
    totalSamplesInput.setCustomValidity("");
    totalSamplesError.textContent = "";
  });
  totalSamplesInput.addEventListener("blur", validateRunFields);

  [minMinutesInput, maxMinutesInput].forEach((inp) => {
    inp.addEventListener("input", () => {
      minMinutesInput.setCustomValidity("");
      maxMinutesInput.setCustomValidity("");
      intervalError.textContent = "";
    });
    inp.addEventListener("blur", validateRunFields);
  });

  // Equalize all questions to 100% (50% per option for multi-select checkboxes)
  btnEqualizeAll.addEventListener("click", () => {
    state.questions = state.questions.map((q) => {
      const nextQ = { ...q };
      if (nextQ.options && nextQ.options.length) {
        if (nextQ.type === "radio" || nextQ.type === "dropdown" || nextQ.type === "scale") {
          nextQ.option_percentages = makeEvenDistribution(nextQ.options.length);
        } else if (nextQ.type === "checkbox") {
          nextQ.option_percentages = makeCheckboxDefaults(nextQ.options);
        }
      }
      if (nextQ.rows && nextQ.columns) {
        if (nextQ.type === "grid_radio") {
          nextQ.row_percentages = nextQ.rows.map(() => makeEvenDistribution(nextQ.columns.length));
        } else if (nextQ.type === "grid_checkbox") {
          nextQ.row_percentages = nextQ.rows.map(() => makeCheckboxDefaults(nextQ.columns));
        }
      }
      return nextQ;
    });
    renderQuestions();
  });

  // Demo Form Loader (helps verify configuration UX without waiting on network)
  btnDemo.addEventListener("click", () => {
    showAlert(scanAlert, scanAlertText, "");
    if (!urlInput.value.trim()) {
      urlInput.value = "https://docs.google.com/forms/d/e/demo-survey/viewform";
      urlInput.setCustomValidity("");
      urlError.textContent = "";
    }
    state.formTitle = "Khảo sát trải nghiệm sản phẩm (Mẫu thử nghiệm)";
    state.pageCount = 2;
    state.questions = [
      {
        id: "p1_q1",
        order: 1,
        page: 1,
        title: "Nhóm tuổi của bạn",
        type: "radio",
        required: true,
        options: ["Dưới 18 tuổi", "18 - 24 tuổi", "25 - 34 tuổi", "Trên 35 tuổi"],
        option_percentages: [10, 45, 35, 10],
      },
      {
        id: "p1_q2",
        order: 2,
        page: 1,
        title: "Bạn biết đến sản phẩm qua những kênh nào?",
        type: "checkbox",
        required: false,
        question_pct: 80,
        min_select: 1,
        max_select: 3,
        options: ["Facebook", "TikTok", "YouTube", "Bạn bè giới thiệu"],
        option_percentages: [70, 60, 40, 20],
      },
      {
        id: "p2_q1",
        order: 3,
        page: 2,
        title: "Mức độ hài lòng chung (1 - 5)",
        type: "scale",
        required: true,
        options: ["1", "2", "3", "4", "5"],
        option_percentages: [5, 5, 15, 45, 30],
      },
    ].map(normalizeQuestionDefaults);
    scanMetaPill.textContent = `${state.formTitle} · ${state.pageCount} trang`;
    scanMetaPill.className = "pill pill-live";
    renderQuestions();
    liveAnnouncement.textContent = `Đã nạp ${state.questions.length} câu hỏi mẫu để cấu hình.`;
  });

  // Scan Google Form
  scanForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    showAlert(scanAlert, scanAlertText, "");
    if (!validateUrlField()) {
      urlInput.focus();
      return;
    }

    if (!["running", "waiting", "paused"].includes(state.jobStatus)) {
      applyStatusSnapshot({
        status: "idle",
        total_samples: 0,
        completed: 0,
        success_count: 0,
        error_count: 0,
        min_minutes: Number(minMinutesInput.value) || 0,
        max_minutes: Number(maxMinutesInput.value) || 0,
        countdown_seconds: 0,
        paused: false,
        last_error: "",
        logs: [],
      });
    }

    state.scanning = true;
    btnScan.disabled = true;
    btnDemo.disabled = true;
    scanMetaPill.textContent = "Đang quét câu hỏi...";
    scanMetaPill.className = "pill pill-warn";
    jobProgress.removeAttribute("value"); // Indeterminate while scanning
    liveAnnouncement.textContent = "Đang mở form và chọn đáp án đầu qua các trang (không ấn gửi)...";

    try {
      const resp = await fetch(`${API_BASE}/api/scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: urlInput.value.trim(),
          headless: headlessInput.checked,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.error || "Không thể quét Google Form.");
      }

      state.formTitle = data.form_title || "Google Form";
      state.pageCount = data.page_count || 1;
      state.questions = (data.questions || []).map(normalizeQuestionDefaults);

      scanMetaPill.textContent = `${state.formTitle} · ${state.pageCount} trang`;
      scanMetaPill.className = "pill pill-live";
      renderQuestions();
      liveAnnouncement.textContent = `Quét xong ${state.questions.length} câu hỏi trên ${state.pageCount} trang (đã dừng trước nút Gửi).`;
    } catch (err) {
      scanMetaPill.textContent = "Quét thất bại";
      scanMetaPill.className = "pill pill-alert";
      showAlert(scanAlert, scanAlertText, err.message);
      liveAnnouncement.textContent = `Lỗi khi quét form: ${err.message}`;
    } finally {
      state.scanning = false;
      btnScan.disabled = false;
      btnDemo.disabled = false;
      jobProgress.value = 0;
    }
  });

  // Start Automated Filling Job
  runForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    showAlert(runAlert, runAlertText, "");

    if (!validateUrlField()) {
      urlInput.focus();
      return;
    }
    if (!validateRunFields()) {
      const firstInvalid = runForm.querySelector(":invalid");
      if (firstInvalid) firstInvalid.focus();
      return;
    }
    if (state.questions.length === 0) {
      showAlert(runAlert, runAlertText, "Bạn cần quét câu hỏi từ Google Form trước khi bắt đầu điền.");
      urlInput.focus();
      return;
    }

    // Validate option sums <= 100% and required questions
    for (const q of state.questions) {
      if (q.type === "radio" || q.type === "dropdown" || q.type === "scale") {
        const sum = (q.option_percentages || []).reduce((a, b) => a + (Number(b) || 0), 0);
        if (sum > 100.01) {
          showAlert(runAlert, runAlertText, `Câu "${q.title}" có tổng tỷ lệ vượt quá 100%.`);
          return;
        }
        if (q.required && sum <= 0) {
          showAlert(runAlert, runAlertText, `Câu bắt buộc "${q.title}" cần tổng tỷ lệ % lớn hơn 0%.`);
          return;
        }
      } else if (q.type === "checkbox" || q.type === "grid_checkbox") {
        const minSel = Number(q.min_select);
        const maxSel = Number(q.max_select);
        if (!Number.isInteger(minSel) || !Number.isInteger(maxSel) || minSel < 0 || maxSel < minSel) {
          showAlert(runAlert, runAlertText, `Khoảng số lượng ngẫu nhiên của câu "${q.title}" không hợp lệ (Từ <= Đến).`);
          return;
        }
        const pctLists = q.type === "checkbox" ? [q.option_percentages || []] : (q.row_percentages || []);
        for (const list of pctLists) {
          if (list.some((v) => !Number.isFinite(Number(v)) || Number(v) < 0 || Number(v) > 100)) {
            showAlert(runAlert, runAlertText, `Tỷ lệ % đáp án của câu "${q.title}" phải trong khoảng 0% - 100%.`);
            return;
          }
        }
      }
    }

    const payload = {
      url: urlInput.value.trim(),
      headless: headlessInput.checked,
      total_samples: Number(totalSamplesInput.value),
      min_minutes: Number(minMinutesInput.value),
      max_minutes: Number(maxMinutesInput.value),
      questions: state.questions,
    };

    // Synchronously reset telemetry and lock Start button before async POST to prevent stale status or double-submit
    btnStart.disabled = true;
    applyStatusSnapshot({
      status: "running",
      total_samples: payload.total_samples,
      completed: 0,
      success_count: 0,
      error_count: 0,
      min_minutes: payload.min_minutes,
      max_minutes: payload.max_minutes,
      countdown_seconds: 0,
      paused: false,
      last_error: "",
      logs: [],
    });

    try {
      const resp = await fetch(`${API_BASE}/api/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.error || "Không thể khởi chạy tiến trình.");
      }
      applyStatusSnapshot(data);
      startPolling();
    } catch (err) {
      applyStatusSnapshot({
        status: "error",
        total_samples: payload.total_samples,
        completed: 0,
        success_count: 0,
        error_count: 0,
        min_minutes: payload.min_minutes,
        max_minutes: payload.max_minutes,
        countdown_seconds: 0,
        paused: false,
        last_error: err.message,
        logs: [],
      });
      showAlert(runAlert, runAlertText, err.message);
    }
  });

  btnPause.addEventListener("click", async () => {
    btnPause.disabled = true;
    try {
      const resp = await fetch(`${API_BASE}/api/pause`, { method: "POST" });
      if (resp.ok) {
        applyStatusSnapshot(await resp.json());
      }
    } catch (err) {
      showAlert(runAlert, runAlertText, `Không thể tạm dừng/tiếp tục: ${err.message}`);
    } finally {
      const isActive = state.jobStatus === "running" || state.jobStatus === "waiting" || state.jobStatus === "paused";
      btnPause.disabled = !isActive;
    }
  });

  btnStop.addEventListener("click", async () => {
    btnStop.disabled = true;
    try {
      const resp = await fetch(`${API_BASE}/api/stop`, { method: "POST" });
      if (resp.ok) {
        applyStatusSnapshot(await resp.json());
      }
    } catch (err) {
      showAlert(runAlert, runAlertText, `Không thể dừng tiến trình: ${err.message}`);
    } finally {
      const isActive = state.jobStatus === "running" || state.jobStatus === "waiting" || state.jobStatus === "paused";
      btnStop.disabled = !isActive;
    }
  });

  function applyStatusSnapshot(data) {
    state.jobStatus = data.status || "idle";
    const total = data.total_samples || 0;
    const completed = data.completed || 0;
    const pct = total > 0 ? Math.round((completed / total) * 100) : 0;

    kpiProgress.textContent = `${completed} / ${total}`;
    kpiCounts.textContent = `Thành công: ${data.success_count || 0} · Lỗi: ${data.error_count || 0}`;
    kpiCountdown.textContent = formatMMSS(data.countdown_seconds || 0);
    kpiInterval.textContent = `Khoảng nghỉ: ${data.min_minutes ?? minMinutesInput.value} – ${data.max_minutes ?? maxMinutesInput.value} phút`;
    kpiRate.textContent = `${pct}%`;

    if (!state.scanning) {
      jobProgress.max = 100;
      jobProgress.value = pct;
    }
    progressPctPill.textContent = `${pct}%`;
    progressLabelText.textContent = `${completed} / ${total} mẫu`;

    const statusMap = {
      idle: { text: "Sẵn sàng", cls: "pill pill-cyan", desc: "Đang chờ cấu hình" },
      running: { text: "Đang điền form", cls: "pill pill-live", desc: "Đang tự động điền và gửi mẫu" },
      waiting: { text: "Đang chờ giãn cách", cls: "pill pill-warn", desc: `Chờ ${formatMMSS(data.countdown_seconds)} tới mẫu tiếp theo` },
      paused: { text: "Đang tạm dừng", cls: "pill pill-warn", desc: "Đã tạm dừng bởi người dùng" },
      completed: { text: "Hoàn thành", cls: "pill pill-live", desc: `Hoàn tất ${completed}/${total} mẫu` },
      stopped: { text: "Đã dừng", cls: "pill pill-alert", desc: `Đã dừng tại ${completed}/${total} mẫu` },
      error: { text: "Gặp lỗi", cls: "pill pill-alert", desc: data.last_error || "Có lỗi xảy ra" },
    };

    const st = statusMap[data.status] || statusMap.idle;
    topStatusPill.className = st.cls;
    topStatusText.textContent = st.text;
    kpiState.textContent = st.desc;

    const isActive = data.status === "running" || data.status === "waiting" || data.status === "paused";
    btnStart.disabled = isActive;
    btnPause.disabled = !isActive;
    btnStop.disabled = !isActive;
    btnPause.textContent = data.paused ? "Tiếp tục" : "Tạm dừng";

    if (data.last_error) {
      errorSidebarBox.hidden = false;
      errorSidebarText.textContent = `Lỗi gần nhất: ${data.last_error}`;
    } else {
      errorSidebarBox.hidden = true;
    }

    if (data.status === "completed") {
      liveAnnouncement.textContent = `Đã hoàn thành toàn bộ ${total} mẫu khảo sát.`;
    } else if (data.status === "stopped") {
      liveAnnouncement.textContent = `Tiến trình đã dừng ở ${completed}/${total} mẫu.`;
    } else if (isActive) {
      liveAnnouncement.textContent = `${st.text}: ${completed}/${total} mẫu.`;
    }

    renderLogs(data.logs || []);

    if (!isActive && state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function renderLogs(logs) {
    logsTbody.replaceChildren();
    if (!logs.length) {
      const tr = document.createElement("tr");
      const td = createEl("td", "", "Chưa có dữ liệu thực thi.");
      td.colSpan = 4;
      td.style.textAlign = "center";
      td.style.color = "var(--color-text-secondary)";
      tr.appendChild(td);
      logsTbody.appendChild(tr);
      return;
    }

    logs.forEach((entry) => {
      const tr = document.createElement("tr");
      const tdTime = createEl("td", "mono", entry.time || "");
      const tdSample = createEl("td", "mono", `#${entry.sample}`);
      const tdStatus = document.createElement("td");
      const pill = createEl(
        "span",
        entry.status === "success" ? "pill pill-live" : entry.status === "stopped" ? "pill pill-warn" : "pill pill-alert",
        entry.status === "success" ? "Thành công" : entry.status === "stopped" ? "Đã dừng" : "Lỗi"
      );
      tdStatus.appendChild(pill);
      const tdDetail = createEl("td", "", entry.detail || "");
      tr.append(tdTime, tdSample, tdStatus, tdDetail);
      logsTbody.appendChild(tr);
    });
  }

  function startPolling() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollInFlight = false;
    state.pollTimer = setInterval(async () => {
      if (state.pollInFlight) return;
      state.pollInFlight = true;
      try {
        const resp = await fetch(`${API_BASE}/api/status`);
        if (resp.ok) {
          applyStatusSnapshot(await resp.json());
        }
      } catch {
        // Ignore transient poll errors
      } finally {
        state.pollInFlight = false;
      }
    }, 1000);
  }

  // Check status on initial load in case a job is already running
  fetch(`${API_BASE}/api/status`)
    .then((r) => (r.ok ? r.json() : null))
    .then((data) => {
      if (data) {
        applyStatusSnapshot(data);
        if (data.status === "running" || data.status === "waiting" || data.status === "paused") {
          startPolling();
        }
      }
    })
    .catch(() => {});
})();
