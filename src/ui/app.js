"use strict";

(() => {
  const form = document.getElementById("query-form");
  const questionInput = document.getElementById("question");
  const submitButton = document.getElementById("submit-button");
  const submitLabel = document.getElementById("submit-label");
  const loadingSpinner = document.getElementById("loading-spinner");
  const queryError = document.getElementById("query-error");
  const answerPanel = document.getElementById("answer-panel");
  const answerMeta = document.getElementById("answer-meta");
  const statusBanners = document.getElementById("status-banners");
  const answerText = document.getElementById("answer-text");
  const mathCard = document.getElementById("math-card");
  const formula = document.getElementById("formula");
  const numericResult = document.getElementById("numeric-result");
  const citationList = document.getElementById("citation-list");
  const emptyCitations = document.getElementById("empty-citations");
  const healthIndicator = document.getElementById("health-indicator");
  const healthLabel = document.getElementById("health-label");
  const healthError = document.getElementById("health-error");
  const healthRefresh = document.getElementById("health-refresh");

  const metaApiBase = document.querySelector('meta[name="api-base"]')?.content.trim() || "";
  const configuredApiBase = new URLSearchParams(window.location.search).get("apiBase") || metaApiBase;
  const apiBase = configuredApiBase.replace(/\/+$/, "");

  function endpoint(path) {
    return `${apiBase}${path}`;
  }

  function setLoading(isLoading) {
    submitButton.disabled = isLoading;
    questionInput.disabled = isLoading;
    loadingSpinner.hidden = !isLoading;
    submitLabel.textContent = isLoading ? "Researching…" : "Research question";
  }

  function showError(element, message) {
    element.textContent = message;
    element.hidden = false;
  }

  function clearError(element) {
    element.textContent = "";
    element.hidden = true;
  }

  async function readJsonResponse(response) {
    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new Error(`The service returned an unreadable response (HTTP ${response.status}).`);
    }

    if (!response.ok) {
      const detail = typeof payload?.detail === "string"
        ? payload.detail
        : `Request failed (HTTP ${response.status}).`;
      throw new Error(detail);
    }

    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("The service returned an invalid response.");
    }
    return payload;
  }

  function appendNumericHighlights(container, value) {
    const text = String(value ?? "");
    const numericPattern = /(?:[$€£]\s?)?-?\d[\d,]*(?:\.\d+)?(?:\s?(?:%|bps|basis points|million|billion|trillion|x))?/gi;
    let previousIndex = 0;
    let match;

    while ((match = numericPattern.exec(text)) !== null) {
      if (match.index > previousIndex) {
        container.appendChild(document.createTextNode(text.slice(previousIndex, match.index)));
      }
      const highlight = document.createElement("mark");
      highlight.className = "number-highlight";
      highlight.textContent = match[0];
      container.appendChild(highlight);
      previousIndex = numericPattern.lastIndex;
    }

    if (previousIndex < text.length) {
      container.appendChild(document.createTextNode(text.slice(previousIndex)));
    }
  }

  function appendMetaChip(text, tone) {
    const chip = document.createElement("span");
    chip.className = "meta-chip";
    if (tone) chip.dataset.tone = tone;
    chip.textContent = text;
    answerMeta.appendChild(chip);
  }

  function appendBanner(message, className) {
    const banner = document.createElement("p");
    banner.className = `banner ${className}`;
    banner.textContent = message;
    statusBanners.appendChild(banner);
  }

  function formatResult(mathResult) {
    const result = mathResult.computed_result;
    if (typeof result !== "number" || !Number.isFinite(result)) return null;

    const decimals = Number.isInteger(mathResult.rounding_decimals)
      ? Math.min(Math.max(mathResult.rounding_decimals, 0), 20)
      : undefined;
    const formatted = decimals === undefined
      ? result.toLocaleString()
      : result.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: decimals });
    const unit = typeof mathResult.result_unit === "string" ? mathResult.result_unit : "";
    return `${formatted}${unit}`;
  }

  function renderMath(mathResult) {
    mathCard.hidden = true;
    formula.textContent = "";
    numericResult.textContent = "";

    if (!mathResult || typeof mathResult !== "object") return;
    const hasFormula = typeof mathResult.formula_expression === "string"
      && mathResult.formula_expression.length > 0;
    const result = formatResult(mathResult);
    if (!hasFormula && result === null) return;

    if (hasFormula) formula.textContent = mathResult.formula_expression;
    else formula.textContent = "Formula not provided.";
    if (result !== null) numericResult.textContent = result;
    else numericResult.textContent = "Result not provided.";
    mathCard.hidden = false;
  }

  function renderCitation(citation) {
    if (!citation || typeof citation !== "object") return;
    const filename = typeof citation.pdf_filename === "string" ? citation.pdf_filename : "";
    const page = Number.isInteger(citation.pdf_page_number) && citation.pdf_page_number > 0
      ? citation.pdf_page_number
      : null;
    if (!filename || page === null) return;

    const item = document.createElement("li");
    item.className = "citation";
    const link = document.createElement("a");
    link.className = "citation-link";
    link.href = `${endpoint(`/pdfs/${encodeURIComponent(filename)}`)}#page=${page}`;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.dataset.pdfFilename = filename;
    link.dataset.pdfPage = String(page);
    link.dataset.verbatimQuote = typeof citation.snippet === "string" ? citation.snippet : "";

    const printedLabel = typeof citation.printed_page_label === "string" && citation.printed_page_label
      ? ` · printed page ${citation.printed_page_label}`
      : "";
    link.textContent = `${filename} · PDF page ${page}${printedLabel}`;
    item.appendChild(link);

    if (typeof citation.snippet === "string" && citation.snippet.length > 0) {
      const snippet = document.createElement("p");
      snippet.className = "citation-snippet";
      snippet.textContent = `“${citation.snippet}”`;
      item.appendChild(snippet);
    }
    citationList.appendChild(item);
  }

  function renderResponse(response) {
    if (typeof response.answer !== "string") {
      throw new Error("The service response did not include a valid answer.");
    }

    answerMeta.replaceChildren();
    statusBanners.replaceChildren();
    citationList.replaceChildren();
    emptyCitations.hidden = true;

    const systemStatus = response.system_status && typeof response.system_status === "object"
      ? response.system_status
      : {};
    const executionMode = typeof systemStatus.execution_mode === "string"
      ? systemStatus.execution_mode
      : "";
    const cacheHit = systemStatus.cache_hit === true;
    const isAbstention = response.is_abstention === true;
    const isFallback = response.is_fallback_mode === true
      || executionMode === "DEGRADED_PASSAGE_FALLBACK";

    appendMetaChip(
      isAbstention ? "Abstention" : (isFallback ? "Degraded passage fallback" : "Answer returned"),
      isAbstention ? "warning" : (isFallback ? "warning" : "good"),
    );
    if (executionMode) appendMetaChip(`Execution mode: ${executionMode}`);
    if (typeof response.tokens_used === "number" && Number.isFinite(response.tokens_used)) {
      appendMetaChip(`Tokens: ${cacheHit ? 0 : response.tokens_used}`);
    }
    if (typeof response.latency_ms === "number" && Number.isFinite(response.latency_ms)) {
      appendMetaChip(`Latency: ${response.latency_ms.toLocaleString()} ms`);
    }

    if (cacheHit) {
      appendBanner("⚡ In-Chat Cache Hit (0 Tokens)", "banner-cache");
    }
    if (systemStatus.provider_switched === true) {
      appendBanner("⚠️ Model Switched: Groq -> Gemini/OpenRouter", "banner-provider");
    }
    if (executionMode === "DEGRADED_PASSAGE_FALLBACK") {
      appendBanner("🚨 LLM Offline — Showing Top-4 Verified Passages", "banner-outage");
    }
    const serverBanner = systemStatus.ui_status_banner;
    if (serverBanner && typeof serverBanner.message === "string" && serverBanner.message) {
      appendBanner(serverBanner.message, "banner-server");
    }

    answerText.replaceChildren();
    appendNumericHighlights(answerText, response.answer);
    renderMath(response.math_result);

    const citations = Array.isArray(response.citations) ? response.citations : [];
    citations.forEach(renderCitation);
    emptyCitations.hidden = citationList.childElementCount > 0;
    answerPanel.hidden = false;
  }

  function healthValue(payload) {
    const status = typeof payload.status === "string" ? payload.status : "unknown";
    const indexLoaded = payload.index_loaded === true;
    const llmReachable = payload.litellm_reachable === true;
    const chunks = Number.isFinite(payload.total_chunks) ? payload.total_chunks : null;
    const documents = Number.isFinite(payload.total_documents) ? payload.total_documents : null;
    const parts = [status];
    if (chunks !== null && documents !== null) {
      parts.push(`${chunks.toLocaleString()} chunks · ${documents.toLocaleString()} filings`);
    }
    if (!indexLoaded) parts.push("index unavailable");
    if (!llmReachable) parts.push("LLM unavailable");
    return { status, label: parts.join(" · ") };
  }

  async function refreshHealth() {
    healthRefresh.disabled = true;
    healthIndicator.dataset.state = "checking";
    healthLabel.textContent = "Checking service";
    clearError(healthError);

    try {
      const response = await fetch(endpoint("/api/v1/health"), {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      const payload = await readJsonResponse(response);
      const result = healthValue(payload);
      healthLabel.textContent = result.label;
      healthIndicator.dataset.state = result.status === "healthy"
        ? "healthy"
        : (result.status === "degraded" ? "degraded" : "checking");
    } catch (error) {
      healthIndicator.dataset.state = "error";
      healthLabel.textContent = "Service unavailable";
      showError(healthError, error instanceof Error ? error.message : "Could not check service health.");
    } finally {
      healthRefresh.disabled = false;
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearError(queryError);

    const question = questionInput.value.trim();
    if (question.length < 3 || question.length > 500) {
      showError(queryError, "Enter a question between 3 and 500 characters.");
      questionInput.focus();
      return;
    }

    setLoading(true);
    try {
      const response = await fetch(endpoint("/api/v1/query"), {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ question }),
      });
      const payload = await readJsonResponse(response);
      renderResponse(payload);
    } catch (error) {
      showError(
        queryError,
        error instanceof Error ? error.message : "The request could not be completed. Check the service and try again.",
      );
    } finally {
      setLoading(false);
    }
  });

  healthRefresh.addEventListener("click", refreshHealth);
  refreshHealth();
})();
