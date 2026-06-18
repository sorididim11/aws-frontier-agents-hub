(function () {
  "use strict";

  const STORAGE_SESSION = "expert_session_id";
  const STORAGE_OPEN = "expert_panel_open";
  const STORAGE_HISTORY = "expert_chat_history";
  const STORAGE_TAB = "expert_active_tab";
  const STORAGE_PROVIDER = "expert_provider";
  const STORAGE_WIDTH = "expert_panel_width";
  const STORAGE_SETTINGS = "expert_settings";
  const MAX_HISTORY = 100;
  const DEFAULT_WIDTH = 380;
  const MIN_WIDTH = 280;
  const MAX_WIDTH_RATIO = 0.5;

  const DEFAULT_SETTINGS = {
    fontSize: "M",
    theme: "dark",
    markdown: true,
    codeHighlight: true,
    showToolCalls: true,
    autoScroll: true,
    historyCount: 100,
  };

  const FONT_SIZES = { S: "0.78rem", M: "0.88rem", L: "1.0rem" };

  const PROVIDER_ICONS = {
    claude: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>',
    kiro: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/><line x1="14" y1="4" x2="10" y2="20"/></svg>',
  };

  const EXPERT_ACTIONS = {
    app: {
      label: "App",
      actions: [
        { label: "앱 상태 점검", prompt: "overview_app의 현재 상태를 점검해줘. Flask 라우트, worker, DDB 연결 확인." },
        { label: "API 응답 검증", prompt: "현재 space에 대해 주요 API 엔드포인트 응답을 확인해줘." },
        { label: "에러 로그 분석", prompt: "최근 에러나 경고가 있는지 앱 로그를 분석해줘." },
        { label: "데이터 정합성", prompt: "현재 페이지에 표시된 데이터가 DDB와 일치하는지 확인해줘." },
      ]
    },
    space: {
      label: "Space",
      actions: [
        { label: "Space 추가", prompt: "space 추가", isWizard: true },
        { label: "데이터소스 목록", prompt: "현재 space에 연결된 데이터소스(GitLab, Splunk, GitHub) 목록과 상태를 확인해줘." },
        { label: "데이터소스 추가", prompt: "새 데이터소스를 등록하고 싶어. 사용 가능한 서비스와 등록 방법을 알려줘." },
        { label: "데이터소스 검증", prompt: "등록된 데이터소스가 정상 연결되어 있는지 검증해줘. Private Connection 상태 포함." },
      ]
    },
    topology: {
      label: "Topology",
      actions: [
        { label: "토폴로지 분석", prompt: "현재 space의 토폴로지를 분석하고 노드/엣지 구조를 설명해줘." },
        { label: "경계 노드 확장", prompt: "현재 토폴로지에서 boundary_nodes를 확인하고 확장 가능한 연결을 분석해줘." },
        { label: "연결 검증", prompt: "토폴로지의 노드/엣지 정합성을 검증하고, 누락된 연결이 있는지 확인해줘." },
        { label: "레벨별 뷰 비교", prompt: "L1/L2/L3 아키텍처 뷰를 비교하고 차이점을 설명해줘." },
      ]
    },
    simulation: {
      label: "Simulation",
      actions: [
        { label: "시나리오 생성", prompt: "현재 space의 앱에 대해 장애 시나리오를 생성해줘. 적절한 템플릿을 선택하고 단계별로 구성해줘." },
        { label: "시나리오 검토", prompt: "등록된 시나리오 목록을 확인하고, 각 시나리오의 완성도와 실행 가능성을 검토해줘." },
        { label: "코드 수정 제안", prompt: "최근 시나리오 실행 결과를 보고, 개선이 필요한 부분의 코드 수정안을 제안해줘." },
        { label: "문제 분석", prompt: "최근 실행된 시나리오의 결과를 분석하고, 실패 원인과 DevOps Agent 진단 정확도를 평가해줘." },
      ]
    },
    devops: {
      label: "DevOps",
      actions: [
        { label: "Agent에게 질문", prompt: "DevOps Agent에게 현재 space의 상태에 대해 질문해줘. Agent가 직접 도구를 사용해서 답변할 거야." },
        { label: "조사 시작", prompt: "현재 space에서 이상 징후를 조사해줘. DevOps Agent에게 조사를 시작하도록 요청해." },
        { label: "세션 상태", prompt: "현재 space의 DevOps Agent 세션(executionId) 상태와 최근 대화 이력을 확인해줘." },
        { label: "Investigation 이력", prompt: "최근 Investigation 실행 이력과 결과를 요약해줘. DAG 완결성도 확인해줘." },
      ]
    },
    security: {
      label: "Security",
      actions: [
        { label: "보안 분석 리뷰", prompt: "현재 space의 보안 분석 결과를 리뷰해줘." },
        { label: "IAM 진단", prompt: "관련 IAM 정책과 권한을 점검해줘." },
        { label: "공격 경로 분석", prompt: "Attack Path 분석 결과를 확인하고 위험도가 높은 경로를 설명해줘." },
        { label: "취약점 요약", prompt: "발견된 보안 이슈를 요약해줘." },
      ]
    }
  };

  const PAGE_ACTIONS = {
    topology: ["topology.0", "topology.2"],
    scenario: ["simulation.0", "simulation.3"],
    investigation: ["devops.2", "devops.3"],
    security: ["security.0", "security.2"],
    settings: ["app.0", "space.1"],
    list: ["app.0", "space.0"],
  };

  function detectCurrentPage() {
    var path = location.pathname;
    if (path.includes("security")) return "security";
    if (path.includes("settings")) return "settings";
    if (path.includes("dag") || path.includes("evidence")) return "investigation";
    if (window.ARCH && window.ARCH.nodes && window.ARCH.nodes.length > 0) return "topology";
    if (window.SCEN && window.SCEN.current) return "scenario";
    if (window.currentRunId) return "investigation";
    return "list";
  }

  function getPageContext() {
    return {
      url: location.pathname + location.search + location.hash,
      page: detectCurrentPage(),
      spaceId: window.SELECTED || null,
      archLevel: window.ARCH && window.ARCH.nav ? window.ARCH.nav.level : null,
      archSelectedApp: window.ARCH && window.ARCH.nav ? window.ARCH.nav.selectedApp : null,
      archNodeCount: window.ARCH && window.ARCH.nodes ? window.ARCH.nodes.length : 0,
      scenarioId: window.SCEN && window.SCEN.current ? window.SCEN.current.id : null,
      runId: window.currentRunId || null,
      securitySpaceId: window._secSpaceId || null,
    };
  }

  function shouldIncludeContext(prompt) {
    var keywords = ["이 페이지", "현재", "여기", "지금 보고", "이 화면", "이 토폴로지", "이 시나리오", "이 space"];
    return keywords.some(function(k) { return prompt.includes(k); });
  }

  var panelWidth = parseInt(localStorage.getItem(STORAGE_WIDTH)) || DEFAULT_WIDTH;
  var isDragging = false;

  function getSettings() {
    try { return Object.assign({}, DEFAULT_SETTINGS, JSON.parse(localStorage.getItem(STORAGE_SETTINGS) || "{}")); }
    catch (e) { return Object.assign({}, DEFAULT_SETTINGS); }
  }

  function saveSettings(s) { localStorage.setItem(STORAGE_SETTINGS, JSON.stringify(s)); }

  function createPanel() {
    var wrapper = document.createElement("div");
    wrapper.id = "epWrapper";
    wrapper.innerHTML = '\
      <div class="ep-resize-handle" id="epResizeHandle"></div>\
      <div class="ep-panel" id="epPanel">\
        <div class="ep-header">\
          <span class="ep-provider-icon" id="epProviderIcon"></span>\
          <span class="ep-title" id="epTitle">Expert Agent</span>\
          <span class="ep-status" id="epStatus"></span>\
          <div class="ep-actions">\
            <button id="epProvider" class="ep-provider-btn" title="Switch Provider">\
              <span id="epProviderLabel">Claude</span>\
            </button>\
            <button id="epSettings" title="설정">\
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 01-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>\
            </button>\
            <button id="epNew" title="New Session">\
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>\
            </button>\
            <button id="epClose" title="패널 닫기">\
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>\
            </button>\
          </div>\
        </div>\
        <div class="ep-settings-panel" id="epSettingsPanel" style="display:none;"></div>\
        <div class="ep-tabs" id="epTabs"></div>\
        <div class="ep-quick" id="epQuick"></div>\
        <div class="ep-messages" id="epMessages"></div>\
        <div class="ep-input-area">\
          <textarea id="epInput" placeholder="질문을 입력하세요..." rows="2"></textarea>\
          <button id="epSend" class="ep-send-btn" title="Send">\
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>\
          </button>\
        </div>\
      </div>';

    document.body.appendChild(wrapper);
    injectToggleButton();
    injectStyles();
    loadLibraries();
    bindEvents();
    renderTabs();
    restoreState();
    applySettings();
    checkHealth();
  }

  function injectToggleButton() {
    var header = document.querySelector(".header");
    if (!header) return;
    var btn = document.createElement("button");
    btn.id = "epToggleBtn";
    btn.className = "ep-header-toggle";
    btn.title = "Expert Agent";
    btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg><span>Expert</span>';
    header.appendChild(btn);
    btn.onclick = function() { setOpen(true); };
  }

  function renderTabs() {
    var container = document.getElementById("epTabs");
    var activeTab = localStorage.getItem(STORAGE_TAB) || "app";
    var html = "";
    Object.keys(EXPERT_ACTIONS).forEach(function(key) {
      var cls = key === activeTab ? "ep-tab active" : "ep-tab";
      html += '<button class="' + cls + '" data-tab="' + key + '">' + EXPERT_ACTIONS[key].label + '</button>';
    });
    container.innerHTML = html;
    container.querySelectorAll(".ep-tab").forEach(function(btn) {
      btn.onclick = function() { selectTab(btn.dataset.tab); };
    });
    renderQuickActions(activeTab);
  }

  function selectTab(tab) {
    localStorage.setItem(STORAGE_TAB, tab);
    document.querySelectorAll(".ep-tab").forEach(function(el) {
      el.classList.toggle("active", el.dataset.tab === tab);
    });
    renderQuickActions(tab);
  }

  function renderQuickActions(tab) {
    var container = document.getElementById("epQuick");
    var page = detectCurrentPage();
    var pageLabel = { topology: "Topology", scenario: "Scenario", investigation: "Investigation", security: "Security", settings: "Settings", list: "Space List" };

    var html = '<div class="ep-page-badge">' + (pageLabel[page] || page) + '</div>';
    html += '<div class="ep-quick-list">';

    var actions = EXPERT_ACTIONS[tab].actions;
    actions.forEach(function(action, idx) {
      html += '<button class="ep-quick-btn" data-tab="' + tab + '" data-idx="' + idx + '">' + action.label + '</button>';
    });

    html += '</div>';
    container.innerHTML = html;
    container.querySelectorAll(".ep-quick-btn").forEach(function(btn) {
      btn.onclick = function() {
        var t = btn.dataset.tab;
        var i = parseInt(btn.dataset.idx);
        var action = EXPERT_ACTIONS[t].actions[i];
        if (action.isWizard) {
          var msgContainer = document.getElementById("epMessages");
          appendMessageDOM(msgContainer, "user", action.prompt);
          saveToHistory("user", action.prompt);
          startWizard();
        } else {
          executeQuickAction(action.prompt);
        }
      };
    });
  }

  function executeQuickAction(prompt) {
    var input = document.getElementById("epInput");
    input.value = prompt;
    sendMessage(true);
  }

  function bindEvents() {
    document.getElementById("epClose").onclick = function() { setOpen(false); };
    document.getElementById("epNew").onclick = newSession;
    document.getElementById("epProvider").onclick = toggleProvider;
    document.getElementById("epSettings").onclick = toggleSettingsPanel;
    document.getElementById("epSend").onclick = function() { sendMessage(false); };
    document.getElementById("epInput").onkeydown = function(e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(false); }
    };
    updateProviderLabel();

    // Resize handle
    var handle = document.getElementById("epResizeHandle");
    handle.onmousedown = startDrag;
    handle.ontouchstart = startDrag;
  }

  function toggleSettingsPanel() {
    var panel = document.getElementById("epSettingsPanel");
    if (panel.style.display === "none") {
      renderSettingsPanel();
      panel.style.display = "block";
    } else {
      panel.style.display = "none";
    }
  }

  function renderSettingsPanel() {
    var s = getSettings();
    var panel = document.getElementById("epSettingsPanel");
    panel.innerHTML = '\
      <div class="ep-settings-grid">\
        <div class="ep-setting-row">\
          <span class="ep-setting-label">글꼴 크기</span>\
          <div class="ep-setting-btns" data-key="fontSize">\
            <button data-val="S" class="' + (s.fontSize==="S"?"active":"") + '">S</button>\
            <button data-val="M" class="' + (s.fontSize==="M"?"active":"") + '">M</button>\
            <button data-val="L" class="' + (s.fontSize==="L"?"active":"") + '">L</button>\
          </div>\
        </div>\
        <div class="ep-setting-row">\
          <span class="ep-setting-label">테마</span>\
          <div class="ep-setting-btns" data-key="theme">\
            <button data-val="dark" class="' + (s.theme==="dark"?"active":"") + '">Dark</button>\
            <button data-val="light" class="' + (s.theme==="light"?"active":"") + '">Light</button>\
          </div>\
        </div>\
        <div class="ep-setting-row">\
          <span class="ep-setting-label">마크다운</span>\
          <label class="ep-toggle"><input type="checkbox" data-key="markdown" ' + (s.markdown?"checked":"") + '><span class="ep-toggle-slider"></span></label>\
        </div>\
        <div class="ep-setting-row">\
          <span class="ep-setting-label">코드 하이라이트</span>\
          <label class="ep-toggle"><input type="checkbox" data-key="codeHighlight" ' + (s.codeHighlight?"checked":"") + '><span class="ep-toggle-slider"></span></label>\
        </div>\
        <div class="ep-setting-row">\
          <span class="ep-setting-label">도구 호출 표시</span>\
          <label class="ep-toggle"><input type="checkbox" data-key="showToolCalls" ' + (s.showToolCalls?"checked":"") + '><span class="ep-toggle-slider"></span></label>\
        </div>\
        <div class="ep-setting-row">\
          <span class="ep-setting-label">자동 스크롤</span>\
          <label class="ep-toggle"><input type="checkbox" data-key="autoScroll" ' + (s.autoScroll?"checked":"") + '><span class="ep-toggle-slider"></span></label>\
        </div>\
        <div class="ep-setting-row">\
          <span class="ep-setting-label">히스토리 수</span>\
          <select data-key="historyCount">\
            <option value="50" ' + (s.historyCount===50?"selected":"") + '>50</option>\
            <option value="100" ' + (s.historyCount===100?"selected":"") + '>100</option>\
            <option value="200" ' + (s.historyCount===200?"selected":"") + '>200</option>\
          </select>\
        </div>\
      </div>';

    panel.querySelectorAll(".ep-setting-btns button").forEach(function(btn) {
      btn.onclick = function() {
        var key = btn.parentElement.dataset.key;
        var val = btn.dataset.val;
        var settings = getSettings();
        settings[key] = val;
        saveSettings(settings);
        applySettings();
        renderSettingsPanel();
      };
    });
    panel.querySelectorAll(".ep-toggle input").forEach(function(inp) {
      inp.onchange = function() {
        var key = inp.dataset.key;
        var settings = getSettings();
        settings[key] = inp.checked;
        saveSettings(settings);
        applySettings();
      };
    });
    panel.querySelector("select[data-key=historyCount]").onchange = function(e) {
      var settings = getSettings();
      settings.historyCount = parseInt(e.target.value);
      saveSettings(settings);
    };
  }

  function applySettings() {
    var s = getSettings();
    var wrapper = document.getElementById("epWrapper");
    if (!wrapper) return;
    wrapper.style.setProperty("--ep-font-size", FONT_SIZES[s.fontSize] || FONT_SIZES.M);
    wrapper.classList.toggle("ep-theme-light", s.theme === "light");
  }

  function startDrag(e) {
    e.preventDefault();
    isDragging = true;
    document.body.classList.add("ep-resizing");
    document.addEventListener("mousemove", onDrag);
    document.addEventListener("mouseup", stopDrag);
    document.addEventListener("touchmove", onDrag);
    document.addEventListener("touchend", stopDrag);
  }

  function onDrag(e) {
    if (!isDragging) return;
    var clientX = e.touches ? e.touches[0].clientX : e.clientX;
    var maxWidth = window.innerWidth * MAX_WIDTH_RATIO;
    var newWidth = window.innerWidth - clientX;
    newWidth = Math.max(MIN_WIDTH, Math.min(newWidth, maxWidth));
    panelWidth = newWidth;
    applyWidth();
  }

  function stopDrag() {
    isDragging = false;
    document.body.classList.remove("ep-resizing");
    document.removeEventListener("mousemove", onDrag);
    document.removeEventListener("mouseup", stopDrag);
    document.removeEventListener("touchmove", onDrag);
    document.removeEventListener("touchend", stopDrag);
    localStorage.setItem(STORAGE_WIDTH, panelWidth);
    window.dispatchEvent(new Event("resize"));
  }

  function applyWidth() {
    var wrapper = document.getElementById("epWrapper");
    if (wrapper.classList.contains("open")) {
      wrapper.style.width = panelWidth + "px";
      document.body.style.marginRight = panelWidth + "px";
    }
  }

  function getProvider() {
    return localStorage.getItem(STORAGE_PROVIDER) || "claude";
  }

  function toggleProvider() {
    var current = getProvider();
    var next = current === "claude" ? "kiro" : "claude";
    localStorage.setItem(STORAGE_PROVIDER, next);
    updateProviderLabel();
    newSession();
  }

  function updateProviderLabel() {
    var el = document.getElementById("epProviderLabel");
    var iconEl = document.getElementById("epProviderIcon");
    var titleEl = document.getElementById("epTitle");
    var provider = getProvider();
    var name = provider === "kiro" ? "Kiro" : "Claude Code";
    el.textContent = name;
    el.parentElement.classList.toggle("kiro", provider === "kiro");
    if (iconEl) iconEl.innerHTML = PROVIDER_ICONS[provider] || "";
    if (iconEl) iconEl.className = "ep-provider-icon ep-provider-icon-" + provider;
    if (titleEl) {
      titleEl.textContent = name + " Expert";
      titleEl.style.color = provider === "kiro" ? "#a78bfa" : "";
    }
  }

  function setOpen(open) {
    var wrapper = document.getElementById("epWrapper");
    var toggleBtn = document.getElementById("epToggleBtn");
    if (open) {
      wrapper.classList.add("open");
      wrapper.style.width = panelWidth + "px";
      document.body.style.marginRight = panelWidth + "px";
      if (toggleBtn) toggleBtn.classList.add("active");
      localStorage.setItem(STORAGE_OPEN, "1");
      renderQuickActions(localStorage.getItem(STORAGE_TAB) || "app");
      setTimeout(function() { document.getElementById("epInput").focus(); }, 100);
      window.dispatchEvent(new Event("resize"));
    } else {
      wrapper.classList.remove("open");
      wrapper.style.width = "0";
      document.body.style.marginRight = "0";
      if (toggleBtn) toggleBtn.classList.remove("active");
      localStorage.setItem(STORAGE_OPEN, "0");
      window.dispatchEvent(new Event("resize"));
    }
  }

  function restoreState() {
    if (localStorage.getItem(STORAGE_OPEN) === "1") setOpen(true);
    var history = getHistory();
    var container = document.getElementById("epMessages");
    history.forEach(function(msg) { appendMessageDOM(container, msg.role, msg.content, false); });
    scrollToBottom();
  }

  var availableProviders = [];

  function checkHealth() {
    fetch("/api/expert/health").then(function(r) { return r.json(); }).then(function(d) {
      var el = document.getElementById("epStatus");
      if (d.ok) {
        el.textContent = "connected"; el.className = "ep-status on";
        availableProviders = d.providers || ["claude"];
        var defaultProv = d["default"] || availableProviders[0];
        if (!localStorage.getItem(STORAGE_PROVIDER)) {
          localStorage.setItem(STORAGE_PROVIDER, defaultProv);
        }
      } else {
        el.textContent = "offline"; el.className = "ep-status off";
      }
      renderProviderSwitch();
    }).catch(function() {
      var el = document.getElementById("epStatus");
      el.textContent = "offline"; el.className = "ep-status off";
      renderProviderSwitch();
    });
  }
  setInterval(checkHealth, 15000);

  function renderProviderSwitch() {
    var btn = document.getElementById("epProvider");
    if (availableProviders.length < 2) {
      btn.style.display = "none";
    } else {
      btn.style.display = "flex";
    }
    updateProviderLabel();
  }

  function sendMessage(isQuickAction) {
    var input = document.getElementById("epInput");
    var prompt = input.value.trim();
    if (!prompt) return;

    input.value = "";
    var container = document.getElementById("epMessages");
    appendMessageDOM(container, "user", prompt);
    saveToHistory("user", prompt);

    // Wizard trigger detection
    if (!isQuickAction && isWizardTrigger(prompt)) {
      startWizard();
      return;
    }

    var sessionId = localStorage.getItem(STORAGE_SESSION) || "";
    var provider = getProvider();
    var payload = { prompt: prompt, sessionId: sessionId, provider: provider };

    if (isQuickAction || shouldIncludeContext(prompt)) {
      payload.pageContext = getPageContext();
    }

    var msgEl = appendMessageDOM(container, "assistant", "");
    var contentEl = msgEl.querySelector(".ep-msg-content");
    var fullText = "";

    setSending(true);

    fetch("/api/expert/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function(resp) {
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      var reader = resp.body.getReader();
      var decoder = new TextDecoder();
      var buffer = "";

      function pump() {
        return reader.read().then(function(result) {
          if (result.done) { finalize(); return; }
          buffer += decoder.decode(result.value, { stream: true });
          var lines = buffer.split("\n");
          buffer = lines.pop();
          for (var i = 0; i < lines.length; i++) {
            var line = lines[i];
            if (!line.startsWith("data: ")) continue;
            try {
              var data = JSON.parse(line.slice(6));
              handleChunk(data, contentEl);
              if (data.type === "text") fullText += data.content || "";
            } catch (e) {}
          }
          scrollToBottom();
          return pump();
        });
      }
      return pump();
    }).catch(function(err) {
      contentEl.textContent = "[Error: " + err.message + "]";
      finalize();
    });

    function finalize() {
      setSending(false);
      if (fullText) saveToHistory("assistant", fullText);
      renderMarkdown(contentEl);
      renderChoiceButtons(contentEl);
      scrollToBottom();
    }
  }

  function renderChoiceButtons(el) {
    var html = el.innerHTML;
    var choiceRegex = /\[choice:([^\]]+)\]\(([^)]+)\)/g;
    if (!choiceRegex.test(html)) return;
    choiceRegex.lastIndex = 0;
    html = html.replace(choiceRegex, function(match, label, action) {
      return '<button class="ep-choice-btn" data-action="' + escapeHtml(action) + '">' + escapeHtml(label) + '</button>';
    });
    el.innerHTML = html;
    el.querySelectorAll(".ep-choice-btn").forEach(function(btn) {
      btn.onclick = function() {
        var action = btn.dataset.action;
        var input = document.getElementById("epInput");
        input.value = action;
        sendMessage(true);
      };
    });
  }

  function handleChunk(data, contentEl) {
    var s = getSettings();
    switch (data.type) {
      case "text": contentEl.textContent += data.content || ""; break;
      case "tool_use":
        if (s.showToolCalls) contentEl.textContent += "\n[Tool: " + data.tool + "]\n";
        break;
      case "session_id": if (data.sessionId) localStorage.setItem(STORAGE_SESSION, data.sessionId); break;
      case "error": contentEl.textContent += "\n[Error: " + data.content + "]"; break;
    }
  }

  function appendMessageDOM(container, role, content, scroll) {
    if (scroll === undefined) scroll = true;
    var div = document.createElement("div");
    div.className = "ep-msg ep-msg-" + role;
    div.innerHTML = '<div class="ep-msg-label">' + (role === "user" ? "You" : "Expert") + '</div><div class="ep-msg-content">' + escapeHtml(content) + '</div>';
    container.appendChild(div);
    if (scroll) scrollToBottom();
    return div;
  }

  function renderMarkdown(el) {
    var s = getSettings();
    var text = el.textContent || "";
    if (!s.markdown) {
      el.innerHTML = escapeHtml(text).replace(/\n/g, "<br>");
      return;
    }
    if (window.marked) {
      el.innerHTML = window.marked.parse(text);
      if (s.codeHighlight && window.hljs) {
        el.querySelectorAll("pre code").forEach(function(block) {
          window.hljs.highlightElement(block);
        });
      }
    } else {
      text = text.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre class="ep-code"><code>$2</code></pre>');
      text = text.replace(/`([^`]+)`/g, '<code class="ep-inline-code">$1</code>');
      text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      text = renderFallbackTable(text);
      text = text.replace(/\n/g, "<br>");
      el.innerHTML = text;
    }
  }

  function renderFallbackTable(text) {
    return text.replace(/((?:\|[^\n]+\|\n)+)/g, function(block) {
      var rows = block.trim().split("\n");
      if (rows.length < 2) return block;
      var html = '<table class="ep-fallback-table">';
      rows.forEach(function(row, i) {
        if (row.replace(/[|\s-]/g, '') === '') return;
        var cells = row.split("|").filter(function(c, idx, arr) { return idx > 0 && idx < arr.length - 1; });
        var tag = i === 0 ? "th" : "td";
        html += "<tr>" + cells.map(function(c) { return "<" + tag + ">" + c.trim() + "</" + tag + ">"; }).join("") + "</tr>";
      });
      html += "</table>";
      return html;
    });
  }

  function scrollToBottom() {
    var s = getSettings();
    if (!s.autoScroll) return;
    var container = document.getElementById("epMessages");
    container.scrollTop = container.scrollHeight;
  }

  function setSending(sending) {
    document.getElementById("epSend").disabled = sending;
    document.getElementById("epInput").disabled = sending;
  }

  function newSession() {
    localStorage.removeItem(STORAGE_SESSION);
    localStorage.removeItem(STORAGE_HISTORY);
    document.getElementById("epMessages").innerHTML = "";
    appendMessageDOM(document.getElementById("epMessages"), "assistant", "새 세션이 시작되었습니다. 무엇을 도와드릴까요?");
  }

  function getHistory() { try { return JSON.parse(localStorage.getItem(STORAGE_HISTORY) || "[]"); } catch (e) { return []; } }

  function saveToHistory(role, content) {
    var s = getSettings();
    var maxHist = s.historyCount || MAX_HISTORY;
    var history = getHistory();
    history.push({ role: role, content: content });
    if (history.length > maxHist) history.splice(0, history.length - maxHist);
    localStorage.setItem(STORAGE_HISTORY, JSON.stringify(history));
  }

  function escapeHtml(str) { var d = document.createElement("div"); d.textContent = str; return d.innerHTML; }

  // ========== Chat Wizard ==========
  var WIZARD_TRIGGERS = ["space 추가", "스페이스 추가", "space 생성", "스페이스 생성", "add space", "create space"];

  var wizardState = null;
  var _wizAccounts = [];
  var _wizClusters = [];
  var _wizIntegrations = [];

  function isWizardTrigger(prompt) {
    var lower = prompt.toLowerCase().trim();
    return WIZARD_TRIGGERS.some(function(t) { return lower.includes(t); });
  }

  function startWizard() {
    wizardState = { step: 0, data: { app_tag_key: "App", resource_tags: [], resources: [], integrations_selected: {} } };
    var container = document.getElementById("epMessages");
    // Load dynamic data then render
    Promise.all([
      fetch("/api/accounts").then(function(r) { return r.json(); }).then(function(d) { _wizAccounts = d.accounts || []; }),
      fetch("/api/integrations").then(function(r) { return r.json(); }).then(function(d) { _wizIntegrations = d.integrations || []; }),
    ]).then(function() {
      renderWizardStep(container, 0);
    }).catch(function() {
      renderWizardStep(container, 0);
    });
  }

  function _wizLoadClusters(accountId) {
    if (!accountId) { _wizClusters = []; return Promise.resolve(); }
    return fetch("/api/accounts/" + accountId + "/clusters").then(function(r) { return r.json(); }).then(function(d) {
      _wizClusters = d.clusters || [];
    }).catch(function() { _wizClusters = []; });
  }

  function renderWizardStep(container, stepIdx) {
    var total = 4;
    var div = document.createElement("div");
    div.className = "ep-msg ep-msg-assistant";
    div.id = "epWizStep" + stepIdx;

    var indicators = "";
    for (var i = 0; i < total; i++) {
      var cls = i < stepIdx ? "done" : (i === stepIdx ? "active" : "");
      indicators += '<div class="ep-wiz-ind ' + cls + '"></div>';
    }

    var titles = ["기본 정보", "계정 & 인프라", "데이터소스", "확인 & 생성"];
    var fieldsHtml = "";

    if (stepIdx === 0) {
      fieldsHtml = _wizStep0();
    } else if (stepIdx === 1) {
      fieldsHtml = _wizStep1();
    } else if (stepIdx === 2) {
      fieldsHtml = _wizStep2();
    } else if (stepIdx === 3) {
      fieldsHtml = _wizStep3();
    }

    var btns = '<div class="ep-wiz-btns">';
    if (stepIdx > 0) btns += '<button class="ep-wiz-btn-prev" data-action="prev">← 이전</button>';
    if (stepIdx < total - 1) btns += '<button class="ep-wiz-btn-next" data-action="next">다음 →</button>';
    else btns += '<button class="ep-wiz-btn-next" data-action="generate">CFN 코드 생성</button>';
    btns += '</div>';

    div.innerHTML = '\
      <div class="ep-msg-label">Expert</div>\
      <div class="ep-wiz-container">\
        <div class="ep-wiz-header">\
          <span class="ep-wiz-title">' + titles[stepIdx] + '</span>\
          <span class="ep-wiz-step">Step ' + (stepIdx + 1) + '/' + total + '</span>\
        </div>\
        <div class="ep-wiz-indicators">' + indicators + '</div>\
        <div class="ep-wiz-fields">' + fieldsHtml + '</div>\
        ' + btns + '\
      </div>';

    container.appendChild(div);

    div.querySelectorAll("[data-action]").forEach(function(btn) {
      btn.onclick = function() {
        _wizCollectStep(div, stepIdx);
        var action = btn.dataset.action;
        if (action === "prev") {
          wizardState.step--;
          renderWizardStep(container, wizardState.step);
        } else if (action === "next") {
          var proceed = function() { wizardState.step++; renderWizardStep(container, wizardState.step); };
          if (stepIdx === 1) {
            var acctId = wizardState.data.secondary_account_id || wizardState.data.primary_account_id;
            var primaryId = wizardState.data.primary_account_id || "";
            Promise.all([
              _wizLoadClusters(acctId),
              fetch("/api/integrations?account_id=" + primaryId).then(function(r) { return r.json(); }).then(function(d) { _wizIntegrations = d.integrations || []; })
            ]).then(proceed).catch(proceed);
          } else {
            proceed();
          }
        } else if (action === "generate") {
          generateFromWizard(container);
        }
        div.querySelector(".ep-wiz-btns").innerHTML = '<span style="color:#64748b;font-size:.7rem;">완료</span>';
      };
    });

    // Add tag button binding
    var addTagBtn = div.querySelector("[data-addtag]");
    if (addTagBtn) {
      addTagBtn.onclick = function(e) {
        e.preventDefault();
        var tagList = div.querySelector("#epWizTags");
        var idx = tagList.children.length;
        var row = document.createElement("div");
        row.className = "ep-wiz-tag-row";
        row.innerHTML = '<input type="text" data-tagkey="' + idx + '" placeholder="Key" style="width:40%"><input type="text" data-tagval="' + idx + '" placeholder="Value" style="width:40%">';
        tagList.appendChild(row);
      };
    }

    scrollToBottom();
  }

  function _wizStep0() {
    var d = wizardState.data;
    var h = '';
    h += '<div class="ep-wiz-field"><label>Space 이름</label><input type="text" data-field="name" value="' + escapeHtml(d.name || '') + '" placeholder="my-agent-space"></div>';
    h += '<div class="ep-wiz-field"><label>앱 이름</label><input type="text" data-field="app_name" value="' + escapeHtml(d.app_name || '') + '" placeholder="MyApp"></div>';
    h += '<div class="ep-wiz-field"><label>태그 키</label><input type="text" data-field="app_tag_key" value="' + escapeHtml(d.app_tag_key || 'App') + '" placeholder="App"></div>';
    h += '<div class="ep-wiz-field"><label>태그 값 (비우면 앱 이름 사용)</label><input type="text" data-field="app_tag_value" value="' + escapeHtml(d.app_tag_value || '') + '" placeholder="앱 이름과 동일"></div>';
    // Resource tags
    h += '<div class="ep-wiz-field"><label>리소스 태그 (선택)</label><div id="epWizTags" class="ep-wiz-tag-list">';
    (d.resource_tags || []).forEach(function(t, i) {
      h += '<div class="ep-wiz-tag-row"><input type="text" data-tagkey="' + i + '" value="' + escapeHtml(t.key) + '" placeholder="Key" style="width:40%"><input type="text" data-tagval="' + i + '" value="' + escapeHtml(t.value) + '" placeholder="Value" style="width:40%"></div>';
    });
    h += '</div><button class="ep-wiz-add-btn" data-addtag="true">+ 태그 추가</button></div>';
    return h;
  }

  function _wizStep1() {
    var d = wizardState.data;
    var h = '';
    // Primary account
    h += '<div class="ep-wiz-field"><label>Primary Account (배포 계정)</label><select data-field="primary_account_id">';
    h += '<option value="">선택하세요</option>';
    _wizAccounts.forEach(function(a) {
      var sel = a.account_id === d.primary_account_id ? ' selected' : '';
      h += '<option value="' + a.account_id + '"' + sel + '>' + a.account_id + ' [' + a.account_type + ']' + (a.profile ? ' (' + a.profile + ')' : '') + '</option>';
    });
    h += '</select></div>';
    // Secondary account
    h += '<div class="ep-wiz-field"><label>Secondary Account (선택, 비우면 단일 계정)</label><select data-field="secondary_account_id">';
    h += '<option value="">없음 (단일 계정)</option>';
    _wizAccounts.forEach(function(a) {
      var sel = a.account_id === d.secondary_account_id ? ' selected' : '';
      h += '<option value="' + a.account_id + '"' + sel + '>' + a.account_id + ' [' + a.account_type + ']' + (a.profile ? ' (' + a.profile + ')' : '') + '</option>';
    });
    h += '</select></div>';
    // EKS cluster (primary)
    h += '<div class="ep-wiz-field"><label>EKS 클러스터 (Primary)</label>';
    if (_wizClusters.length > 0) {
      h += '<select data-field="eks_cluster_name"><option value="">선택 안함</option>';
      _wizClusters.forEach(function(c) {
        var name = c.name || c;
        var sel = name === d.eks_cluster_name ? ' selected' : '';
        h += '<option value="' + escapeHtml(name) + '"' + sel + '>' + escapeHtml(name) + '</option>';
      });
      h += '</select>';
    } else {
      h += '<input type="text" data-field="eks_cluster_name" value="' + escapeHtml(d.eks_cluster_name || '') + '" placeholder="클러스터 이름 (선택)">';
    }
    h += '</div>';
    // EKS cluster (secondary) — shown only when secondary account selected
    h += '<div class="ep-wiz-field"><label>EKS 클러스터 (Secondary, cross-account)</label>';
    h += '<input type="text" data-field="secondary_eks_cluster" value="' + escapeHtml(d.secondary_eks_cluster || '') + '" placeholder="Secondary 계정 클러스터 이름 (선택)">';
    h += '</div>';
    // Role ARN
    h += '<div class="ep-wiz-field"><label>IAM Role ARN (비우면 자동 생성)</label><input type="text" data-field="role_arn" value="' + escapeHtml(d.role_arn || '') + '" placeholder="arn:aws:iam::..."></div>';
    return h;
  }

  function _wizStep2() {
    var d = wizardState.data;
    var dsCategories = [
      { category: "소스 코드", items: [{ id: "github", label: "GitHub" }, { id: "gitlab", label: "GitLab (Private)" }] },
      { category: "알림/채널", items: [{ id: "slack", label: "Slack" }] },
      { category: "옵저버빌리티", items: [{ id: "mcpserversplunk", label: "Splunk Cloud" }] },
      { category: "도구", items: [{ id: "mcpserver", label: "MCP Server (Private)" }] },
    ];
    var h = '';
    // 기존 데이터소스 안내
    var existingCount = _wizIntegrations.length;
    if (existingCount > 0) {
      h += '<div class="ep-wiz-field" style="margin-bottom:8px;padding:8px;background:#1e293b;border-radius:6px;border:1px solid #334155">';
      h += '<label style="color:#38bdf8;font-size:12px">등록된 데이터소스 ' + existingCount + '개</label>';
      h += '<div style="color:#94a3b8;font-size:11px;margin-top:4px">선택하면 이 Space에 연결 설정만 추가됩니다 (credential 재입력 불필요)</div>';
      h += '</div>';
    }
    dsCategories.forEach(function(cat) {
      h += '<div class="ep-wiz-field"><label>' + cat.category + '</label><div class="ep-wiz-checks">';
      cat.items.forEach(function(ds) {
        var available = _wizIntegrations.filter(function(ig) { return ig.provider === ds.id; });
        var selected = !!d.integrations_selected[ds.id];
        var checked = selected ? ' checked' : '';
        h += '<label class="ep-wiz-check"><input type="checkbox" data-integ="' + ds.id + '"' + checked + '><span>' + ds.label + (available.length ? ' (' + available.length + '개 등록됨)' : '') + '</span></label>';
      });
      h += '</div></div>';
    });
    // GitHub repo
    h += '<div class="ep-wiz-field"><label>GitHub 리포지토리 (선택)</label><input type="text" data-field="github_repo" value="' + escapeHtml(d.github_repo || '') + '" placeholder="org/repo"></div>';

    // Splunk Cloud 데이터소스
    if (d.integrations_selected["mcpserversplunk"]) {
      var existingSplunk = _wizIntegrations.filter(function(ig) { return ig.provider === "mcpserversplunk"; });
      h += '<div class="ep-wiz-field" style="margin-top:8px;border-top:1px solid #334155;padding-top:8px"><label style="color:#a78bfa">Splunk Cloud 데이터소스</label></div>';
      if (existingSplunk.length > 0) {
        var useExisting = d.splunk_use_existing || "";
        h += '<div class="ep-wiz-field"><label>등록 방식</label><select data-field="splunk_use_existing">';
        h += '<option value=""' + (!useExisting ? ' selected' : '') + '>새로 등록</option>';
        existingSplunk.forEach(function(s) {
          h += '<option value="' + s.service_id + '"' + (useExisting === s.service_id ? ' selected' : '') + '>기존: ' + escapeHtml(s.name || s.service_id) + '</option>';
        });
        h += '</select></div>';
      }
      if (!d.splunk_use_existing) {
        h += '<div class="ep-wiz-field"><label>Deployment Name</label><input type="text" data-field="splunk_deployment" value="' + escapeHtml(d.splunk_deployment || '') + '" placeholder="prd-p-xxxxx"></div>';
        h += '<div class="ep-wiz-field"><label>MCP Token (audience=mcp)</label><input type="text" data-field="splunk_token" value="' + escapeHtml(d.splunk_token || '') + '" placeholder="JWT token from Splunk MCP Server app"></div>';
      }
      h += '<div class="ep-wiz-field"><label style="font-size:11px;color:#64748b">연결 설정 (이 Space용)</label></div>';
      h += '<div class="ep-wiz-field"><label>Webhook 알림</label><label class="ep-wiz-check"><input type="checkbox" data-field="splunk_webhook"' + (d.splunk_webhook ? ' checked' : '') + '><span>EnableWebhookUpdates</span></label></div>';
    }

    // Private toggle (GitLab)
    if (d.integrations_selected["gitlab"]) {
      var privChecked = d.private_enabled ? ' checked' : '';
      h += '<div class="ep-wiz-field" style="margin-top:8px"><label class="ep-wiz-check"><input type="checkbox" data-field="private_enabled"' + privChecked + '><span style="color:#f59e0b">Private GitLab (VPC 내부)</span></label></div>';
    }

    // Private service details
    var privateTypes = ["gitlab"];
    var hasPrivate = privateTypes.some(function(t) { return !!d.integrations_selected[t]; });
    if (hasPrivate && d.private_enabled) {
      h += '<div class="ep-wiz-field" style="margin-top:8px;border-top:1px solid #334155;padding-top:8px"><label style="color:#f59e0b">Private Connection 설정</label></div>';
      h += '<div class="ep-wiz-field"><label>Connection Mode</label><select data-field="private_connection_mode"><option value="service_managed"' + (d.private_connection_mode === 'self_managed' ? '' : ' selected') + '>동일 계정 (ServiceManaged)</option><option value="self_managed"' + (d.private_connection_mode === 'self_managed' ? ' selected' : '') + '>크로스 계정 (SelfManaged — VPC Lattice)</option></select></div>';
      h += '<div class="ep-wiz-field"><label style="font-size:10px;color:#64748b">' + (d.private_connection_mode === 'self_managed' ? 'SelfManaged: 대상 계정 VPC에 VPC Lattice를 생성하고 RAM으로 공유하여 연결' : 'ServiceManaged: Agent Space 계정 VPC에 ENI를 직접 생성하여 연결') + '</label></div>';
      if (d.private_connection_mode === 'self_managed') {
        h += '<div class="ep-wiz-field"><label>대상 계정 ID</label><input type="text" data-field="private_target_account_id" value="' + escapeHtml(d.private_target_account_id || '') + '" placeholder="데이터소스가 있는 계정 ID"></div>';
      }
      h += '<div class="ep-wiz-field"><label>VPC ID' + (d.private_connection_mode === 'self_managed' ? ' (대상 계정)' : '') + '</label><input type="text" data-field="private_vpc_id" value="' + escapeHtml(d.private_vpc_id || '') + '" placeholder="vpc-xxxxxxxxx"></div>';
      h += '<div class="ep-wiz-field"><label>Subnet IDs (쉼표 구분)</label><input type="text" data-field="private_subnet_ids" value="' + escapeHtml(d.private_subnet_ids || '') + '" placeholder="subnet-aaa,subnet-bbb"></div>';
      h += '<div class="ep-wiz-field"><label>Security Group ID</label><input type="text" data-field="private_sg_id" value="' + escapeHtml(d.private_sg_id || '') + '" placeholder="sg-xxxxxxxxx"></div>';
      h += '<div class="ep-wiz-field"><label>TLS 인증서 (PEM, self-signed인 경우)</label><textarea data-field="private_certificate" rows="4" placeholder="-----BEGIN CERTIFICATE-----&#10;...&#10;-----END CERTIFICATE-----">' + escapeHtml(d.private_certificate || '') + '</textarea></div>';
    }

    privateTypes.forEach(function(ptype) {
      if (!d.integrations_selected[ptype]) return;
      var label = ptype === "gitlab" ? "GitLab" : (ptype === "mcpserversplunk" ? "Splunk MCP" : "MCP Server");
      var prefix = "ps_" + ptype + "_";
      h += '<div class="ep-wiz-field" style="margin-top:6px"><label style="color:#38bdf8">' + label + ' 상세</label></div>';
      h += '<div class="ep-wiz-field"><label>Host Address (NLB DNS 또는 IP)</label><input type="text" data-field="' + prefix + 'host" value="' + escapeHtml(d[prefix + "host"] || '') + '" placeholder="splunk-mcp.internal.example.com"></div>';

      if (ptype === "gitlab") {
        h += '<div class="ep-wiz-field"><label>GitLab URL</label><input type="text" data-field="' + prefix + 'url" value="' + escapeHtml(d[prefix + "url"] || '') + '" placeholder="https://gitlab.internal.example.com/"></div>';
        h += '<div class="ep-wiz-field"><label>Token Type</label><select data-field="' + prefix + 'token_type"><option value="personal"' + (d[prefix+"token_type"]==="personal"?" selected":"") + '>Personal</option><option value="group"' + (d[prefix+"token_type"]==="group"?" selected":"") + '>Group</option></select></div>';
        h += '<div class="ep-wiz-field"><label>Access Token (glpat-...)</label><input type="text" data-field="' + prefix + 'token" value="' + escapeHtml(d[prefix + "token"] || '') + '" placeholder="glpat-xxxxxxxxxxxx"></div>';
      } else {
        h += '<div class="ep-wiz-field"><label>MCP Endpoint</label><input type="text" data-field="' + prefix + 'endpoint" value="' + escapeHtml(d[prefix + "endpoint"] || '') + '" placeholder="https://host/mcp"></div>';
        if (ptype === "mcpserversplunk") {
          h += '<div class="ep-wiz-field"><label>Bearer Token</label><input type="text" data-field="' + prefix + 'token" value="' + escapeHtml(d[prefix + "token"] || '') + '" placeholder="Splunk JWT token"></div>';
        } else {
          h += '<div class="ep-wiz-field"><label>API Key Value</label><input type="text" data-field="' + prefix + 'apikey" value="' + escapeHtml(d[prefix + "apikey"] || '') + '" placeholder="API key or token"></div>';
          h += '<div class="ep-wiz-field"><label>API Key Header</label><input type="text" data-field="' + prefix + 'header" value="' + escapeHtml(d[prefix + "header"] || 'Authorization') + '" placeholder="Authorization"></div>';
        }
      }
    });

    return h;
  }

  function _wizStep3() {
    var d = wizardState.data;
    var h = '<div class="ep-wiz-summary">';
    h += '<div class="ep-wiz-sum-row"><span>Space 이름</span><strong>' + escapeHtml(d.name || '-') + '</strong></div>';
    h += '<div class="ep-wiz-sum-row"><span>앱</span><strong>' + escapeHtml(d.app_name || '-') + ' (' + escapeHtml(d.app_tag_key || 'App') + '=' + escapeHtml(d.app_tag_value || d.app_name || '-') + ')</strong></div>';
    h += '<div class="ep-wiz-sum-row"><span>Primary Account</span><strong>' + escapeHtml(d.primary_account_id || '자동') + '</strong></div>';
    if (d.secondary_account_id) h += '<div class="ep-wiz-sum-row"><span>Secondary Account</span><strong>' + escapeHtml(d.secondary_account_id) + '</strong></div>';
    h += '<div class="ep-wiz-sum-row"><span>EKS</span><strong>' + escapeHtml(d.eks_cluster_name || '없음') + '</strong></div>';
    var integNames = Object.keys(d.integrations_selected).join(', ') || '없음';
    h += '<div class="ep-wiz-sum-row"><span>데이터소스</span><strong>' + escapeHtml(integNames) + '</strong></div>';
    if (d.github_repo) h += '<div class="ep-wiz-sum-row"><span>GitHub</span><strong>' + escapeHtml(d.github_repo) + '</strong></div>';
    if (d.resource_tags && d.resource_tags.length) h += '<div class="ep-wiz-sum-row"><span>태그</span><strong>' + d.resource_tags.length + '개</strong></div>';
    h += '</div>';
    return h;
  }

  function _wizCollectStep(div, stepIdx) {
    // text/select fields
    div.querySelectorAll("input[data-field], select[data-field]").forEach(function(el) {
      wizardState.data[el.dataset.field] = el.value;
    });
    // resource tags
    if (stepIdx === 0) {
      var tags = [];
      div.querySelectorAll("[data-tagkey]").forEach(function(inp) {
        var idx = parseInt(inp.dataset.tagkey);
        var valInp = div.querySelector("[data-tagval='" + idx + "']");
        if (inp.value.trim()) tags.push({ key: inp.value.trim(), value: valInp ? valInp.value.trim() : '' });
      });
      wizardState.data.resource_tags = tags;
    }
    // integrations checkboxes
    if (stepIdx === 2) {
      var selected = {};
      div.querySelectorAll("[data-integ]").forEach(function(inp) {
        if (inp.checked) {
          var provider = inp.dataset.integ;
          var avail = _wizIntegrations.filter(function(ig) { return ig.provider === provider; });
          if (avail.length) selected[provider] = { provider: provider, integration_id: avail[0].integration_id };
        }
      });
      wizardState.data.integrations_selected = selected;
    }
  }

  function generateFromWizard(container) {
    var d = wizardState.data;
    if (!d.app_tag_value) d.app_tag_value = d.app_name || d.name;

    var integrations = [];
    var privateServices = [];
    var subnetIds = (d.private_subnet_ids || "").split(",").map(function(s) { return s.trim(); }).filter(Boolean);
    var sgIds = d.private_sg_id ? [d.private_sg_id] : [];

    Object.keys(d.integrations_selected).forEach(function(k) {
      var info = d.integrations_selected[k];
      if (k === "gitlab" && d.private_enabled) {
        var prefix = "ps_gitlab_";
        privateServices.push({
          type: "gitlab",
          name: (d.name || "svc") + "-gitlab",
          host_address: d[prefix + "host"] || "",
          vpc_id: d.private_vpc_id || "",
          subnet_ids: subnetIds,
          security_group_ids: sgIds,
          port_ranges: ["443"],
          target_url: d[prefix + "url"] || "",
          token_type: d[prefix + "token_type"] || "personal",
          token_value: d[prefix + "token"] || "",
          certificate: d.private_certificate || "",
          connection_mode: d.private_connection_mode || "service_managed",
          target_account_id: d.private_target_account_id || "",
        });
      } else if (k === "mcpserversplunk") {
        if (d.splunk_use_existing) {
          integrations.push({
            type: "mcpserversplunk",
            existing_service_id: d.splunk_use_existing,
            enable_webhook: !!d.splunk_webhook,
          });
        } else {
          var deployment = d.splunk_deployment || "";
          integrations.push({
            type: "mcpserversplunk",
            name: "splunk-cloud",
            endpoint: "https://" + deployment + ".splunkcloud.com:443/en-US/splunkd/__raw/services/mcp",
            auth_type: "bearer_token",
            token_value: d.splunk_token || "",
            enable_webhook: !!d.splunk_webhook,
          });
        }
      } else {
        integrations.push(info);
      }
    });

    var payload = {
      name: d.name || "my-agent-space",
      app_name: d.app_name || "MyApp",
      app_tag_key: d.app_tag_key || "App",
      app_tag_value: d.app_tag_value || d.app_name || "MyApp",
      role_arn: d.role_arn || "",
      primary_account_id: d.primary_account_id || "",
      secondary_account_id: d.secondary_account_id || "",
      eks_cluster_name: d.eks_cluster_name || "",
      secondary_eks_cluster: d.secondary_eks_cluster || "",
      github_repo: d.github_repo || "",
      integrations: integrations,
      resource_tags: d.resource_tags || [],
      resources: d.resources || [],
      private_services: privateServices,
    };

    var loadingEl = appendMessageDOM(container, "assistant", "CFN 코드 생성 중...");

    fetch("/api/spaces/generate-cfn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function(r) { return r.json(); }).then(function(result) {
      container.removeChild(loadingEl);
      if (result.ok) {
        showGeneratedCode(container, result, payload);
      } else {
        appendMessageDOM(container, "assistant", "[Error] " + (result.error || "코드 생성 실패"));
      }
    }).catch(function(err) {
      container.removeChild(loadingEl);
      appendMessageDOM(container, "assistant", "[Error] " + err.message);
    });

    wizardState = null;
  }

  function showGeneratedCode(container, result, params) {
    var yaml = result.yaml;
    var filename = result.filename;
    var secYaml = result.secondary_yaml || "";
    var secFilename = result.secondary_filename || "";

    var secBlock = "";
    if (secYaml) {
      secBlock = '\
        <div class="ep-wiz-code-wrap" style="margin-top:12px">\
          <div class="ep-wiz-code-header">\
            <span>Secondary Account: ' + escapeHtml(secFilename) + '</span>\
            <button class="ep-wiz-copy-btn" data-copy-sec>복사</button>\
          </div>\
          <pre class="ep-wiz-code"><code>' + escapeHtml(secYaml) + '</code></pre>\
        </div>';
    }

    var div = document.createElement("div");
    div.className = "ep-msg ep-msg-assistant";
    div.innerHTML = '\
      <div class="ep-msg-label">Expert</div>\
      <div class="ep-wiz-result">\
        <div class="ep-wiz-result-header">\
          <span class="ep-wiz-result-icon">✓</span>\
          <span>CFN 코드 생성 완료' + (secYaml ? ' (Primary + Secondary)' : '') + '</span>\
        </div>\
        <div class="ep-wiz-code-wrap">\
          <div class="ep-wiz-code-header">\
            <span>Primary: ' + escapeHtml(filename) + '</span>\
            <button class="ep-wiz-copy-btn" data-copy>복사</button>\
          </div>\
          <pre class="ep-wiz-code"><code>' + escapeHtml(yaml) + '</code></pre>\
        </div>' + secBlock + '\
        <div class="ep-wiz-actions">\
          <button class="ep-wiz-action-btn" data-action="review">코드 리뷰 요청</button>\
          <button class="ep-wiz-action-btn" data-action="download">파일 다운로드</button>\
          <button class="ep-wiz-action-btn ep-wiz-action-btn-primary" data-action="deploy">배포</button>\
        </div>\
      </div>';

    container.appendChild(div);

    div.querySelector("[data-copy]").onclick = function() {
      navigator.clipboard.writeText(yaml).then(function() {
        div.querySelector("[data-copy]").textContent = "복사됨!";
        setTimeout(function() { div.querySelector("[data-copy]").textContent = "복사"; }, 1500);
      });
    };

    if (secYaml && div.querySelector("[data-copy-sec]")) {
      div.querySelector("[data-copy-sec]").onclick = function() {
        navigator.clipboard.writeText(secYaml).then(function() {
          div.querySelector("[data-copy-sec]").textContent = "복사됨!";
          setTimeout(function() { div.querySelector("[data-copy-sec]").textContent = "복사"; }, 1500);
        });
      };
    }

    div.querySelector("[data-action=download]").onclick = function() {
      var blob = new Blob([yaml], { type: "text/yaml" });
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url; a.download = filename; a.click();
      URL.revokeObjectURL(url);
      if (secYaml) {
        setTimeout(function() {
          var blob2 = new Blob([secYaml], { type: "text/yaml" });
          var url2 = URL.createObjectURL(blob2);
          var a2 = document.createElement("a");
          a2.href = url2; a2.download = secFilename; a2.click();
          URL.revokeObjectURL(url2);
        }, 500);
      }
    };

    div.querySelector("[data-action=review]").onclick = function() {
      var allYaml = yaml + (secYaml ? "\n---\n# Secondary Account Template\n" + secYaml : "");
      requestCodeReview(container, allYaml, params);
      div.querySelector("[data-action=review]").disabled = true;
      div.querySelector("[data-action=review]").textContent = "리뷰 요청됨";
    };

    div.querySelector("[data-action=deploy]").onclick = function() {
      div.querySelector("[data-action=deploy]").disabled = true;
      div.querySelector("[data-action=deploy]").textContent = "배포 중...";
      deployFromWizard(container, params);
    };

    scrollToBottom();
  }

  function deployFromWizard(container, params) {
    var msgEl = appendMessageDOM(container, "assistant", "");
    var contentEl = msgEl.querySelector(".ep-msg-content");
    contentEl.textContent = "배포 시작...\n";

    fetch("/api/spaces/deploy-cfn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    }).then(function(response) {
      var reader = response.body.getReader();
      var decoder = new TextDecoder();

      function read() {
        reader.read().then(function(result) {
          if (result.done) return;
          var chunk = decoder.decode(result.value, { stream: true });
          var lines = chunk.split("\n");
          lines.forEach(function(line) {
            if (!line.startsWith("data: ")) return;
            try {
              var evt = JSON.parse(line.slice(6));
              if (evt.type === "event") {
                contentEl.textContent += evt.resource + " → " + evt.status + "\n";
              } else if (evt.type === "complete") {
                contentEl.textContent += "\n배포 완료! Stack ID: " + evt.stack_id + "\n";
              } else if (evt.type === "error") {
                contentEl.textContent += "\n배포 실패: " + evt.error + "\n";
              }
            } catch (e) {}
            scrollToBottom();
          });
          read();
        });
      }
      read();
    }).catch(function(err) {
      contentEl.textContent += "\n배포 오류: " + err.message + "\n";
    });
  }

  function requestCodeReview(container, yaml, params) {
    var prompt = "다음 CFN 템플릿을 리뷰해줘. 보안, 모범사례, 누락된 리소스, 개선사항을 확인해줘:\n\n```yaml\n" + yaml + "\n```\n\n파라미터: " + JSON.stringify(params, null, 2);
    var msgEl = appendMessageDOM(container, "assistant", "");
    var contentEl = msgEl.querySelector(".ep-msg-content");
    var fullText = "";

    setSending(true);

    var sessionId = localStorage.getItem(STORAGE_SESSION) || "";
    var provider = getProvider();

    fetch("/api/expert/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: prompt, sessionId: sessionId, provider: provider }),
    }).then(function(resp) {
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      var reader = resp.body.getReader();
      var decoder = new TextDecoder();
      var buffer = "";

      function pump() {
        return reader.read().then(function(result) {
          if (result.done) { finalize(); return; }
          buffer += decoder.decode(result.value, { stream: true });
          var lines = buffer.split("\n");
          buffer = lines.pop();
          for (var i = 0; i < lines.length; i++) {
            var line = lines[i];
            if (!line.startsWith("data: ")) continue;
            try {
              var data = JSON.parse(line.slice(6));
              handleChunk(data, contentEl);
              if (data.type === "text") fullText += data.content || "";
            } catch (e) {}
          }
          scrollToBottom();
          return pump();
        });
      }
      return pump();
    }).catch(function(err) {
      contentEl.textContent = "[Error: " + err.message + "]";
      finalize();
    });

    function finalize() {
      setSending(false);
      if (fullText) saveToHistory("assistant", fullText);
      renderMarkdown(contentEl);
      scrollToBottom();
    }
  }

  function loadLibraries() {
    var markedScript = document.createElement("script");
    markedScript.src = "https://cdn.jsdelivr.net/npm/marked/marked.min.js";
    document.head.appendChild(markedScript);

    var hljsScript = document.createElement("script");
    hljsScript.src = "https://cdn.jsdelivr.net/npm/highlight.js@11/highlight.min.js";
    document.head.appendChild(hljsScript);

    var hljsCss = document.createElement("link");
    hljsCss.rel = "stylesheet";
    hljsCss.href = "https://cdn.jsdelivr.net/npm/highlight.js@11/styles/github-dark.min.css";
    document.head.appendChild(hljsCss);

    markedScript.onload = function() {
      if (window.marked) {
        window.marked.setOptions({ breaks: true, gfm: true });
      }
    };
  }

  function injectStyles() {
    var style = document.createElement("style");
    style.textContent = '\
      #epWrapper { --ep-font-size: 0.88rem; }\
      body { transition: margin-right .25s ease; }\
      body.ep-resizing { transition: none; user-select: none; cursor: col-resize; }\
      body.ep-resizing * { pointer-events: none !important; }\
      body.ep-resizing #epWrapper,\
      body.ep-resizing #epWrapper * { pointer-events: all !important; }\
      \
      .ep-header-toggle { display: flex; align-items: center; gap: 5px; margin-left: 8px; padding: 4px 10px; background: #1e293b; border: 1px solid #334155; border-radius: 6px; color: #94a3b8; font-size: .65rem; font-weight: 600; cursor: pointer; transition: all .2s; }\
      .ep-header-toggle:hover { border-color: #38bdf8; color: #38bdf8; }\
      .ep-header-toggle.active { background: #334155; border-color: #38bdf8; color: #38bdf8; }\
      .ep-header-toggle svg { flex-shrink: 0; }\
      \
      #epWrapper { position: fixed; top: 0; right: 0; bottom: 0; width: 0; z-index: 1000; display: flex; overflow: hidden; transition: width .25s ease; }\
      #epWrapper.open { overflow: visible; }\
      body.ep-resizing #epWrapper { transition: none; }\
      \
      .ep-resize-handle { width: 5px; cursor: col-resize; background: #334155; flex-shrink: 0; position: relative; transition: background .15s; }\
      .ep-resize-handle:hover, .ep-resize-handle:active { background: #38bdf8; }\
      .ep-resize-handle::after { content: ""; position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%); width: 3px; height: 32px; border-radius: 2px; background: #475569; transition: background .15s; }\
      .ep-resize-handle:hover::after { background: #38bdf8; }\
      \
      .ep-panel { flex: 1; min-width: 0; background: #1e293b; border-left: 1px solid #334155; display: flex; flex-direction: column; }\
      .ep-header { padding: 8px 12px; display: flex; align-items: center; gap: 6px; border-bottom: 1px solid #334155; flex-shrink: 0; }\
      .ep-provider-icon { display: flex; align-items: center; color: #38bdf8; flex-shrink: 0; }\
      .ep-provider-icon-kiro { color: #a78bfa; }\
      .ep-title { font-size: .78rem; font-weight: 600; color: #38bdf8; white-space: nowrap; }\
      .ep-provider-icon-kiro + .ep-title { color: #a78bfa; }\
      .ep-status { font-size: .55rem; padding: 2px 5px; border-radius: 8px; font-weight: 600; }\
      .ep-status.on { background: #22c55e20; color: #4ade80; border: 1px solid #22c55e; }\
      .ep-status.off { background: #ef444420; color: #fca5a5; border: 1px solid #ef4444; }\
      .ep-actions { margin-left: auto; display: flex; gap: 3px; }\
      .ep-actions button { background: none; border: 1px solid #475569; border-radius: 4px; color: #94a3b8; cursor: pointer; padding: 3px 5px; display: flex; align-items: center; justify-content: center; transition: all .15s; }\
      .ep-actions button:hover { color: #e2e8f0; border-color: #38bdf8; }\
      .ep-provider-btn { font-size: .58rem; font-weight: 600; padding: 2px 7px !important; }\
      .ep-provider-btn.kiro { color: #a78bfa; border-color: #7c3aed; }\
      .ep-provider-btn.kiro:hover { border-color: #a78bfa; }\
      \
      .ep-settings-panel { padding: 10px 12px; border-bottom: 1px solid #334155; background: #0f172a; flex-shrink: 0; max-height: 300px; overflow-y: auto; }\
      .ep-settings-grid { display: flex; flex-direction: column; gap: 8px; }\
      .ep-setting-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }\
      .ep-setting-label { font-size: .72rem; color: #94a3b8; font-weight: 500; }\
      .ep-setting-btns { display: flex; gap: 2px; }\
      .ep-setting-btns button { background: #1e293b; border: 1px solid #334155; border-radius: 4px; color: #94a3b8; font-size: .65rem; font-weight: 600; padding: 3px 8px; cursor: pointer; transition: all .15s; }\
      .ep-setting-btns button:hover { border-color: #38bdf8; color: #38bdf8; }\
      .ep-setting-btns button.active { background: #334155; color: #38bdf8; border-color: #38bdf8; }\
      .ep-settings-panel select { background: #1e293b; border: 1px solid #334155; border-radius: 4px; color: #e2e8f0; font-size: .65rem; padding: 3px 6px; cursor: pointer; }\
      .ep-toggle { position: relative; display: inline-block; width: 32px; height: 18px; }\
      .ep-toggle input { opacity: 0; width: 0; height: 0; }\
      .ep-toggle-slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background: #475569; border-radius: 18px; transition: .2s; }\
      .ep-toggle-slider::before { content: ""; position: absolute; height: 14px; width: 14px; left: 2px; bottom: 2px; background: #e2e8f0; border-radius: 50%; transition: .2s; }\
      .ep-toggle input:checked + .ep-toggle-slider { background: #38bdf8; }\
      .ep-toggle input:checked + .ep-toggle-slider::before { transform: translateX(14px); }\
      \
      .ep-tabs { display: flex; gap: 2px; padding: 6px 8px 0; flex-shrink: 0; }\
      .ep-tab { background: #0f172a; border: 1px solid #334155; border-radius: 5px 5px 0 0; color: #94a3b8; font-size: .62rem; font-weight: 600; padding: 4px 8px; cursor: pointer; border-bottom: none; transition: all .15s; }\
      .ep-tab:hover { color: #e2e8f0; }\
      .ep-tab.active { background: #334155; color: #38bdf8; border-color: #475569; }\
      .ep-quick { padding: 6px 8px; border-bottom: 1px solid #334155; flex-shrink: 0; }\
      .ep-page-badge { font-size: .55rem; color: #64748b; margin-bottom: 4px; font-weight: 600; text-transform: uppercase; letter-spacing: .5px; }\
      .ep-quick-list { display: flex; flex-wrap: wrap; gap: 3px; }\
      .ep-quick-btn { background: #0f172a; border: 1px solid #334155; border-radius: 5px; color: #cbd5e1; font-size: .62rem; padding: 4px 8px; cursor: pointer; transition: all .15s; }\
      .ep-quick-btn:hover { border-color: #38bdf8; color: #38bdf8; background: #1e293b; }\
      .ep-choice-btn { display: inline-block; margin: 4px 4px 4px 0; padding: 6px 14px; background: #1e293b; border: 1px solid #38bdf8; border-radius: 6px; color: #7dd3fc; font-size: var(--ep-font-size, 0.88rem); font-weight: 500; cursor: pointer; transition: all .15s; }\
      .ep-choice-btn:hover { background: #334155; color: #38bdf8; border-color: #7dd3fc; }\
      .ep-messages { flex: 1; overflow-y: auto; padding: 10px; display: flex; flex-direction: column; gap: 8px; }\
      .ep-msg { display: flex; flex-direction: column; gap: 2px; }\
      .ep-msg-label { font-size: .58rem; color: #64748b; font-weight: 600; text-transform: uppercase; }\
      .ep-msg-content { font-size: var(--ep-font-size, 0.88rem); line-height: 1.6; color: #e2e8f0; word-break: break-word; white-space: pre-wrap; }\
      .ep-msg-user .ep-msg-content { background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 8px 10px; }\
      .ep-msg-assistant .ep-msg-content { background: #0f172a80; border-radius: 8px; padding: 8px 10px; border-left: 3px solid #38bdf8; }\
      .ep-code { background: #0f172a; border: 1px solid #334155; border-radius: 5px; padding: 8px 10px; overflow-x: auto; font-size: calc(var(--ep-font-size, 0.88rem) * 0.85); font-family: "SF Mono", "Fira Code", monospace; }\
      .ep-inline-code { background: #334155; padding: 1px 4px; border-radius: 3px; font-family: "SF Mono", monospace; font-size: calc(var(--ep-font-size, 0.88rem) * 0.9); }\
      .ep-input-area { padding: 8px; border-top: 1px solid #334155; display: flex; gap: 6px; align-items: flex-end; flex-shrink: 0; }\
      .ep-input-area textarea { flex: 1; background: #0f172a; border: 1px solid #334155; border-radius: 6px; color: #e2e8f0; padding: 8px 10px; font-size: var(--ep-font-size, 0.88rem); resize: none; font-family: -apple-system, BlinkMacSystemFont, sans-serif; line-height: 1.4; outline: none; transition: border-color .2s; }\
      .ep-input-area textarea:focus { border-color: #38bdf8; }\
      .ep-send-btn { background: #38bdf8; border: none; border-radius: 6px; color: #0f172a; cursor: pointer; padding: 8px 10px; display: flex; align-items: center; justify-content: center; transition: opacity .2s; }\
      .ep-send-btn:hover { opacity: .85; }\
      .ep-send-btn:disabled { opacity: .4; cursor: not-allowed; }\
      .ep-msg-content table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: calc(var(--ep-font-size) * 0.9); }\
      .ep-msg-content th, .ep-msg-content td { border: 1px solid #334155; padding: 4px 8px; text-align: left; }\
      .ep-msg-content th { background: #0f172a; color: #94a3b8; font-weight: 600; }\
      .ep-msg-content td { color: #cbd5e1; }\
      .ep-msg-content tr:nth-child(even) td { background: #0f172a40; }\
      .ep-msg-content pre { background: #0f172a; border: 1px solid #334155; border-radius: 5px; padding: 8px 10px; overflow-x: auto; margin: 6px 0; }\
      .ep-msg-content pre code { font-size: calc(var(--ep-font-size) * 0.85); font-family: "SF Mono", "Fira Code", monospace; background: none; padding: 0; }\
      .ep-msg-content code { background: #334155; padding: 1px 4px; border-radius: 3px; font-family: "SF Mono", monospace; font-size: calc(var(--ep-font-size) * 0.9); }\
      .ep-msg-content ul, .ep-msg-content ol { padding-left: 18px; margin: 4px 0; }\
      .ep-msg-content li { margin-bottom: 2px; }\
      .ep-msg-content h1, .ep-msg-content h2, .ep-msg-content h3 { color: #38bdf8; margin: 8px 0 4px; }\
      .ep-msg-content h1 { font-size: calc(var(--ep-font-size) * 1.3); } .ep-msg-content h2 { font-size: calc(var(--ep-font-size) * 1.15); } .ep-msg-content h3 { font-size: calc(var(--ep-font-size) * 1.05); }\
      .ep-msg-content blockquote { border-left: 3px solid #475569; padding-left: 8px; color: #94a3b8; margin: 6px 0; }\
      .ep-msg-content p { margin: 4px 0; }\
      \
      #epWrapper.ep-theme-light .ep-panel { background: #f8fafc; border-left-color: #e2e8f0; }\
      #epWrapper.ep-theme-light .ep-header { border-bottom-color: #e2e8f0; }\
      #epWrapper.ep-theme-light .ep-title { color: #0284c7; }\
      #epWrapper.ep-theme-light .ep-msg-content { color: #1e293b; }\
      #epWrapper.ep-theme-light .ep-msg-user .ep-msg-content { background: #e2e8f0; border-color: #cbd5e1; }\
      #epWrapper.ep-theme-light .ep-msg-assistant .ep-msg-content { background: #f1f5f9; border-left-color: #0284c7; }\
      #epWrapper.ep-theme-light .ep-input-area textarea { background: #fff; border-color: #cbd5e1; color: #1e293b; }\
      #epWrapper.ep-theme-light .ep-messages { background: #fff; }\
      #epWrapper.ep-theme-light .ep-settings-panel { background: #f1f5f9; border-bottom-color: #e2e8f0; }\
      #epWrapper.ep-theme-light .ep-setting-label { color: #475569; }\
      #epWrapper.ep-theme-light .ep-tab { background: #f1f5f9; border-color: #e2e8f0; color: #475569; }\
      #epWrapper.ep-theme-light .ep-tab.active { background: #e2e8f0; color: #0284c7; }\
      #epWrapper.ep-theme-light .ep-quick-btn { background: #f1f5f9; border-color: #e2e8f0; color: #334155; }\
      #epWrapper.ep-theme-light .ep-quick-btn:hover { border-color: #0284c7; color: #0284c7; }\
      #epWrapper.ep-theme-light .ep-resize-handle { background: #e2e8f0; }\
      \
      .ep-wiz-container { background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 12px; border-left: 3px solid #f59e0b; }\
      .ep-wiz-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }\
      .ep-wiz-title { font-size: calc(var(--ep-font-size) * 0.95); font-weight: 600; color: #f8fafc; }\
      .ep-wiz-step { font-size: calc(var(--ep-font-size) * 0.75); color: #64748b; }\
      .ep-wiz-indicators { display: flex; gap: 4px; margin-bottom: 12px; }\
      .ep-wiz-ind { flex: 1; height: 3px; border-radius: 2px; background: #334155; }\
      .ep-wiz-ind.active { background: #f59e0b; }\
      .ep-wiz-ind.done { background: #22c55e; }\
      .ep-wiz-fields { display: flex; flex-direction: column; gap: 10px; }\
      .ep-wiz-field label { display: block; font-size: calc(var(--ep-font-size) * 0.8); color: #94a3b8; margin-bottom: 3px; font-weight: 500; }\
      .ep-wiz-field input[type=text] { width: 100%; background: #1e293b; border: 1px solid #475569; border-radius: 5px; color: #e2e8f0; padding: 6px 8px; font-size: calc(var(--ep-font-size) * 0.85); outline: none; box-sizing: border-box; }\
      .ep-wiz-field input[type=text]:focus { border-color: #f59e0b; }\
      .ep-wiz-field input[type=text]::placeholder { color: #475569; }\
      .ep-wiz-checks { display: flex; flex-wrap: wrap; gap: 6px; }\
      .ep-wiz-check { display: flex; align-items: center; gap: 4px; font-size: calc(var(--ep-font-size) * 0.8); color: #cbd5e1; cursor: pointer; }\
      .ep-wiz-check input { accent-color: #f59e0b; }\
      .ep-wiz-btns { display: flex; justify-content: flex-end; gap: 6px; margin-top: 12px; }\
      .ep-wiz-btn-prev, .ep-wiz-btn-next { background: #334155; border: 1px solid #475569; border-radius: 5px; color: #e2e8f0; font-size: calc(var(--ep-font-size) * 0.8); padding: 5px 12px; cursor: pointer; font-weight: 500; transition: all .15s; }\
      .ep-wiz-btn-next { background: #f59e0b; border-color: #f59e0b; color: #0f172a; }\
      .ep-wiz-btn-prev:hover { border-color: #94a3b8; }\
      .ep-wiz-btn-next:hover { opacity: 0.9; }\
      \
      .ep-wiz-result { background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 12px; border-left: 3px solid #22c55e; }\
      .ep-wiz-result-header { display: flex; align-items: center; gap: 6px; margin-bottom: 10px; font-size: calc(var(--ep-font-size) * 0.9); color: #e2e8f0; }\
      .ep-wiz-result-icon { color: #22c55e; font-size: 1.1rem; }\
      .ep-wiz-code-wrap { margin-bottom: 10px; }\
      .ep-wiz-code-header { display: flex; justify-content: space-between; align-items: center; background: #1e293b; padding: 4px 8px; border-radius: 5px 5px 0 0; border: 1px solid #334155; border-bottom: none; }\
      .ep-wiz-code-header span { font-size: calc(var(--ep-font-size) * 0.75); color: #64748b; font-weight: 600; }\
      .ep-wiz-copy-btn { background: #334155; border: 1px solid #475569; border-radius: 3px; color: #94a3b8; font-size: calc(var(--ep-font-size) * 0.7); padding: 2px 6px; cursor: pointer; }\
      .ep-wiz-copy-btn:hover { color: #e2e8f0; border-color: #38bdf8; }\
      .ep-wiz-code { background: #0f172a; border: 1px solid #334155; border-radius: 0 0 5px 5px; padding: 8px 10px; margin: 0; max-height: 200px; overflow: auto; font-size: calc(var(--ep-font-size) * 0.78); font-family: "SF Mono", "Fira Code", monospace; color: #e2e8f0; white-space: pre; line-height: 1.4; }\
      .ep-wiz-actions { display: flex; gap: 6px; }\
      .ep-wiz-action-btn { background: #1e293b; border: 1px solid #475569; border-radius: 5px; color: #e2e8f0; font-size: calc(var(--ep-font-size) * 0.8); padding: 6px 12px; cursor: pointer; font-weight: 500; transition: all .15s; }\
      .ep-wiz-action-btn:hover { border-color: #38bdf8; color: #38bdf8; }\
      .ep-wiz-action-btn:disabled { opacity: .5; cursor: not-allowed; }\
      .ep-wiz-field select { width: 100%; background: #1e293b; border: 1px solid #475569; border-radius: 5px; color: #e2e8f0; padding: 6px 8px; font-size: calc(var(--ep-font-size) * 0.85); outline: none; }\
      .ep-wiz-field select:focus { border-color: #f59e0b; }\
      .ep-wiz-tag-list { display: flex; flex-direction: column; gap: 4px; }\
      .ep-wiz-tag-row { display: flex; gap: 4px; }\
      .ep-wiz-tag-row input { flex: 1; }\
      .ep-wiz-add-btn { background: none; border: 1px dashed #475569; border-radius: 4px; color: #64748b; font-size: calc(var(--ep-font-size) * 0.75); padding: 3px 8px; cursor: pointer; margin-top: 4px; }\
      .ep-wiz-add-btn:hover { border-color: #f59e0b; color: #f59e0b; }\
      .ep-wiz-summary { display: flex; flex-direction: column; gap: 6px; }\
      .ep-wiz-sum-row { display: flex; justify-content: space-between; align-items: center; padding: 5px 8px; background: #1e293b; border-radius: 4px; font-size: calc(var(--ep-font-size) * 0.85); }\
      .ep-wiz-sum-row span { color: #64748b; }\
      .ep-wiz-sum-row strong { color: #e2e8f0; font-weight: 500; }\
    ';
    document.head.appendChild(style);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", createPanel);
  } else {
    createPanel();
  }
})();
