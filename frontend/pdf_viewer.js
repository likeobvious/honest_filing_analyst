import * as pdfjsLib from "./node_modules/pdfjs-dist/build/pdf.mjs";

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  "./node_modules/pdfjs-dist/build/pdf.worker.mjs",
  import.meta.url,
).toString();

const viewerStyles = `
  .pdf-viewer-dialog {
    width: min(1120px, calc(100vw - 28px));
    max-width: none;
    height: min(94vh, 980px);
    max-height: none;
    padding: 0;
    overflow: hidden;
    border: 1px solid #cbd5e1;
    border-radius: 14px;
    color: #17243a;
    background: #f8fafc;
    box-shadow: 0 24px 80px rgb(15 23 42 / 28%);
  }
  .pdf-viewer-dialog::backdrop { background: rgb(15 23 42 / 62%); }
  .pdf-viewer-shell { display: flex; height: 100%; min-height: 0; flex-direction: column; }
  .pdf-viewer-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    padding: 15px 18px;
    border-bottom: 1px solid #e2e8f0;
    background: #fff;
  }
  .pdf-viewer-title { margin: 0; font-size: 15px; font-weight: 720; overflow-wrap: anywhere; }
  .pdf-viewer-quote {
    max-width: 820px;
    margin: 7px 0 0;
    color: #475569;
    font-size: 12px;
    line-height: 1.55;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .pdf-viewer-close {
    flex: 0 0 auto;
    min-width: 36px;
    min-height: 36px;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    color: #334155;
    background: #fff;
    cursor: pointer;
    font-size: 19px;
  }
  .pdf-viewer-toolbar {
    display: flex;
    min-height: 52px;
    align-items: center;
    justify-content: center;
    gap: 10px;
    padding: 8px 14px;
    border-bottom: 1px solid #e2e8f0;
    background: #fff;
  }
  .pdf-viewer-toolbar button {
    min-height: 32px;
    padding: 0 11px;
    border: 1px solid #cbd5e1;
    border-radius: 7px;
    color: #25344b;
    background: #fff;
    cursor: pointer;
  }
  .pdf-viewer-toolbar button:disabled { cursor: not-allowed; opacity: .48; }
  .pdf-viewer-page-input {
    width: 58px;
    min-height: 32px;
    padding: 3px 6px;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    text-align: center;
    font: inherit;
  }
  .pdf-viewer-page-count { color: #64748b; font-size: 12px; }
  .pdf-viewer-stage { position: relative; flex: 1; min-height: 0; overflow: auto; padding: 22px; }
  .pdf-viewer-page {
    position: relative;
    width: max-content;
    min-height: 200px;
    margin: 0 auto;
    background: white;
    box-shadow: 0 3px 18px rgb(15 23 42 / 17%);
  }
  .pdf-viewer-canvas { display: block; }
  .pdf-viewer-text-layer {
    position: absolute;
    inset: 0;
    overflow: hidden;
    line-height: 1;
    text-size-adjust: none;
    transform-origin: 0 0;
    z-index: 1;
  }
  .pdf-viewer-text-layer span,
  .pdf-viewer-text-layer br {
    position: absolute;
    color: transparent;
    white-space: pre;
    cursor: text;
    transform-origin: 0 0;
  }
  .pdf-viewer-text-layer span.pdf-quote-highlight {
    border-radius: 2px;
    background: rgb(250 204 21 / 48%);
    outline: 1px solid rgb(202 138 4 / 72%);
  }
  .pdf-viewer-loading,
  .pdf-viewer-error,
  .pdf-viewer-empty {
    margin: 0 auto;
    padding: 18px;
    color: #475569;
    text-align: center;
    font-size: 13px;
    line-height: 1.6;
  }
  .pdf-viewer-error { color: #991b1b; }
  .pdf-viewer-spinner {
    display: inline-block;
    width: 15px;
    height: 15px;
    margin-right: 8px;
    vertical-align: -3px;
    border: 2px solid #cbd5e1;
    border-top-color: #1d5ed1;
    border-radius: 50%;
    animation: pdf-viewer-spin .7s linear infinite;
  }
  @keyframes pdf-viewer-spin { to { transform: rotate(360deg); } }
  @media (max-width: 600px) {
    .pdf-viewer-dialog { width: calc(100vw - 12px); height: calc(100vh - 16px); border-radius: 9px; }
    .pdf-viewer-header { padding: 12px; }
    .pdf-viewer-stage { padding: 12px; }
  }
`;

const pdfAssetBase = new URL("./node_modules/pdfjs-dist/", import.meta.url).toString();

function createElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function createViewer() {
  const style = createElement("style");
  style.textContent = viewerStyles;
  document.head.appendChild(style);

  const dialog = createElement("dialog", "pdf-viewer-dialog");
  dialog.setAttribute("aria-label", "PDF citation viewer");
  const shell = createElement("div", "pdf-viewer-shell");
  const header = createElement("header", "pdf-viewer-header");
  const titleGroup = createElement("div");
  const title = createElement("h2", "pdf-viewer-title", "PDF citation");
  const quote = createElement("p", "pdf-viewer-quote");
  quote.setAttribute("aria-label", "Verbatim citation quote");
  const closeButton = createElement("button", "pdf-viewer-close", "×");
  closeButton.type = "button";
  closeButton.setAttribute("aria-label", "Close PDF viewer");
  closeButton.addEventListener("click", () => dialog.close());
  titleGroup.append(title, quote);
  header.append(titleGroup, closeButton);

  const toolbar = createElement("nav", "pdf-viewer-toolbar");
  toolbar.setAttribute("aria-label", "PDF page navigation");
  const previousButton = createElement("button", "pdf-viewer-previous", "Previous");
  previousButton.type = "button";
  const pageInput = createElement("input", "pdf-viewer-page-input");
  pageInput.type = "number";
  pageInput.min = "1";
  pageInput.step = "1";
  pageInput.setAttribute("aria-label", "Physical PDF page number");
  const pageCount = createElement("span", "pdf-viewer-page-count", "of —");
  const nextButton = createElement("button", "pdf-viewer-next", "Next");
  nextButton.type = "button";
  previousButton.disabled = true;
  nextButton.disabled = true;
  toolbar.append(previousButton, pageInput, pageCount, nextButton);

  const status = createElement("p", "pdf-viewer-loading");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const stage = createElement("div", "pdf-viewer-stage");
  stage.setAttribute("aria-label", "PDF page");
  const pageContainer = createElement("div", "pdf-viewer-page");
  const canvas = createElement("canvas", "pdf-viewer-canvas");
  canvas.setAttribute("aria-label", "Rendered PDF page");
  const textLayer = createElement("div", "pdf-viewer-text-layer");
  textLayer.setAttribute("aria-hidden", "true");
  pageContainer.append(canvas, textLayer);
  stage.append(status, pageContainer);
  shell.append(header, toolbar, stage);
  dialog.append(shell);
  document.body.append(dialog);

  return { dialog, title, quote, previousButton, pageInput, pageCount, nextButton, status, stage, pageContainer, canvas, textLayer };
}

const viewer = createViewer();
let pdfDocument = null;
let loadingTask = null;
let currentPageNumber = 1;
let citationQuote = "";
let loadSequence = 0;
let renderSequence = 0;
let activeRenderTask = null;

function setLoading(message) {
  viewer.status.className = "pdf-viewer-loading";
  viewer.status.replaceChildren();
  const spinner = createElement("span", "pdf-viewer-spinner");
  spinner.setAttribute("aria-hidden", "true");
  viewer.status.append(spinner, document.createTextNode(message));
  viewer.status.hidden = false;
  viewer.pageContainer.hidden = true;
}

function setError(message) {
  viewer.status.className = "pdf-viewer-error";
  viewer.status.textContent = message;
  viewer.status.hidden = false;
  viewer.pageContainer.hidden = true;
}

function setPageStatus(message) {
  viewer.status.className = "pdf-viewer-empty";
  viewer.status.textContent = message;
  viewer.status.hidden = !message;
}

function setNavigationEnabled(enabled) {
  viewer.previousButton.disabled = !enabled || currentPageNumber <= 1;
  viewer.nextButton.disabled = !enabled || !pdfDocument || currentPageNumber >= pdfDocument.numPages;
  viewer.pageInput.disabled = !enabled;
}

function parsePageFragment(hash) {
  if (!hash) return { page: 1, error: "" };
  const match = /^#page=(\d+)$/i.exec(hash);
  if (!match) return { page: 1, error: "The citation URL contains an invalid page number. Showing page 1." };
  const page = Number(match[1]);
  if (!Number.isSafeInteger(page) || page < 1) {
    return { page: 1, error: "The citation page must be a positive physical PDF page number. Showing page 1." };
  }
  return { page, error: "" };
}

function normalizeQuote(value) {
  return value
    .normalize("NFKC")
    .replace(/\u00ad/g, "")
    .toLocaleLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

function clearQuoteHighlights() {
  viewer.textLayer.querySelectorAll(".pdf-quote-highlight").forEach((span) => {
    span.classList.remove("pdf-quote-highlight");
  });
}

function highlightQuote(quote) {
  clearQuoteHighlights();
  const target = normalizeQuote(quote);
  if (!target) return false;

  const spans = Array.from(viewer.textLayer.querySelectorAll("span"));
  let pageText = "";
  const spanAtOffset = [];
  spans.forEach((span, spanIndex) => {
    if (spanIndex > 0 && pageText && !pageText.endsWith(" ")) {
      pageText += " ";
      spanAtOffset.push(spanIndex);
    }
    const characters = Array.from(span.textContent || "");
    for (const character of characters) {
      const normalized = character
        .normalize("NFKC")
        .replace(/\u00ad/g, "")
        .toLocaleLowerCase();
      for (const normalizedCharacter of normalized) {
        if (/\s/.test(normalizedCharacter)) {
          if (!pageText || pageText.endsWith(" ")) continue;
          pageText += " ";
          spanAtOffset.push(spanIndex);
          continue;
        }
        pageText += normalizedCharacter;
        spanAtOffset.push(spanIndex);
      }
    }
  });

  const start = pageText.indexOf(target);
  if (start < 0) return false;
  const end = start + target.length;
  const matchingSpans = new Set(spanAtOffset.slice(start, end));
  matchingSpans.forEach((spanIndex) => spans[spanIndex]?.classList.add("pdf-quote-highlight"));
  const first = spans[[...matchingSpans][0]];
  if (first) first.scrollIntoView({ block: "center", inline: "nearest" });
  return matchingSpans.size > 0;
}

function validatePageNumber(value, pageCount) {
  if (!/^\d+$/.test(String(value))) return null;
  const page = Number(value);
  if (!Number.isSafeInteger(page) || page < 1 || page > pageCount) return null;
  return page;
}

async function renderPage(pageNumber, sequence = loadSequence) {
  if (!pdfDocument || sequence !== loadSequence) return;
  const pageRenderSequence = ++renderSequence;
  if (activeRenderTask) {
    activeRenderTask.cancel();
    activeRenderTask = null;
  }
  const page = await pdfDocument.getPage(pageNumber);
  if (sequence !== loadSequence || pageRenderSequence !== renderSequence) return;

  const baseViewport = page.getViewport({ scale: 1 });
  const availableWidth = Math.max(240, viewer.stage.clientWidth - 44);
  const scale = Math.min(1.8, availableWidth / baseViewport.width);
  const viewport = page.getViewport({ scale });
  const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
  const context = viewer.canvas.getContext("2d", { alpha: false });
  if (!context) throw new Error("This browser could not create a PDF rendering canvas.");

  viewer.canvas.width = Math.ceil(viewport.width * pixelRatio);
  viewer.canvas.height = Math.ceil(viewport.height * pixelRatio);
  viewer.canvas.style.width = `${viewport.width}px`;
  viewer.canvas.style.height = `${viewport.height}px`;
  viewer.textLayer.replaceChildren();
  viewer.textLayer.style.width = `${viewport.width}px`;
  viewer.textLayer.style.height = `${viewport.height}px`;
  viewer.pageContainer.style.width = `${viewport.width}px`;
  viewer.pageContainer.style.height = `${viewport.height}px`;
  viewer.status.hidden = true;
  viewer.pageContainer.hidden = false;
  clearQuoteHighlights();

  const textContent = await page.getTextContent();
  const renderTask = page.render({
    canvasContext: context,
    viewport,
    transform: pixelRatio === 1 ? null : [pixelRatio, 0, 0, pixelRatio, 0, 0],
  });
  activeRenderTask = renderTask;
  const textLayerTask = new pdfjsLib.TextLayer({
    textContentSource: textContent,
    container: viewer.textLayer,
    viewport,
  });
  await Promise.all([renderTask.promise, textLayerTask.render()]);
  if (sequence !== loadSequence || pageRenderSequence !== renderSequence) return;
  activeRenderTask = null;
  currentPageNumber = pageNumber;
  viewer.pageInput.value = String(pageNumber);
  viewer.pageCount.textContent = `of ${pdfDocument.numPages}`;
  setNavigationEnabled(true);
  viewer.stage.scrollTop = 0;

  if (citationQuote) {
    const found = highlightQuote(citationQuote);
    if (!found) {
      setPageStatus("The cited page is open, but the verbatim quote could not be matched in its PDF text layer.");
      viewer.pageContainer.hidden = false;
    }
  } else {
    setPageStatus("");
  }

}

function updatePageHash(pageNumber) {
  const url = new URL(window.location.href);
  url.hash = `page=${pageNumber}`;
  window.history.replaceState(null, "", url);
}

async function goToPage(value, { updateHash = true, showInvalid = true } = {}) {
  const pageNumber = validatePageNumber(value, pdfDocument?.numPages || 0);
  if (pageNumber === null) {
    if (showInvalid) {
      setError(`Enter a physical PDF page from 1 to ${pdfDocument?.numPages || "—"}.`);
    }
    return false;
  }

  setLoading(`Rendering PDF page ${pageNumber}…`);
  setNavigationEnabled(false);
  try {
    await renderPage(pageNumber);
    if (updateHash) updatePageHash(pageNumber);
    return true;
  } catch (error) {
    if (error?.name === "RenderingCancelledException") return false;
    setError(error instanceof Error ? `Could not render page ${pageNumber}: ${error.message}` : `Could not render PDF page ${pageNumber}.`);
    setNavigationEnabled(Boolean(pdfDocument));
    return false;
  }
}

async function openCitation(link) {
  const sequence = ++loadSequence;
  if (loadingTask) {
    await destroyLoadingTask(loadingTask);
    loadingTask = null;
  }
  pdfDocument = null;
  currentPageNumber = 1;
  citationQuote = link.dataset.verbatimQuote || "";
  const pdfUrl = new URL(link.href, window.location.href);
  const requestedPage = parsePageFragment(pdfUrl.hash);
  pdfUrl.hash = "";

  viewer.title.textContent = link.dataset.pdfFilename || decodeURIComponent(pdfUrl.pathname.split("/").pop() || "PDF document");
  viewer.quote.textContent = citationQuote ? `Verbatim quote: “${citationQuote}”` : "No verbatim quote was provided with this citation.";
  viewer.pageInput.value = String(requestedPage.page);
  viewer.pageCount.textContent = "of —";
  setNavigationEnabled(false);
  setLoading("Loading PDF…");
  viewer.textLayer.replaceChildren();
  viewer.canvas.width = 0;
  viewer.canvas.height = 0;
  if (requestedPage.error) setError(requestedPage.error);
  if (!viewer.dialog.open) viewer.dialog.showModal();

  try {
    loadingTask = pdfjsLib.getDocument({
      url: pdfUrl.toString(),
      cMapUrl: new URL("cmaps/", pdfAssetBase).toString(),
      cMapPacked: true,
      standardFontDataUrl: new URL("standard_fonts/", pdfAssetBase).toString(),
      wasmUrl: new URL("wasm/", pdfAssetBase).toString(),
    });
    const documentProxy = await loadingTask.promise;
    if (sequence !== loadSequence) {
      await documentProxy.destroy();
      return;
    }
    pdfDocument = documentProxy;

    let pageNumber = requestedPage.page;
    let pageWarning = requestedPage.error;
    if (pageNumber > pdfDocument.numPages) {
      pageWarning = `Citation page ${pageNumber} is outside this PDF’s physical page range (1–${pdfDocument.numPages}). Showing page 1.`;
      pageNumber = 1;
    } else if (requestedPage.error) {
      pageNumber = 1;
    }
    const rendered = await goToPage(pageNumber, { updateHash: false, showInvalid: false });
    if (rendered && pageWarning) {
      setPageStatus(pageWarning);
      viewer.pageContainer.hidden = false;
    }
  } catch (error) {
    if (sequence !== loadSequence) return;
    pdfDocument = null;
    setNavigationEnabled(false);
    setError(error instanceof Error
      ? `Could not load “${viewer.title.textContent}”. ${error.message}`
      : `Could not load “${viewer.title.textContent}”. Check that the PDF exists and is accessible.`);
  }
}

async function destroyLoadingTask(task) {
  try {
    await task.destroy();
  } catch (error) {
    console.warn("PDF.js could not cleanly stop a previous document load.", error);
  }
}

viewer.previousButton.addEventListener("click", () => {
  if (currentPageNumber > 1) goToPage(currentPageNumber - 1);
});
viewer.nextButton.addEventListener("click", () => {
  if (pdfDocument && currentPageNumber < pdfDocument.numPages) goToPage(currentPageNumber + 1);
});
viewer.pageInput.addEventListener("change", () => goToPage(viewer.pageInput.value));
viewer.pageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    goToPage(viewer.pageInput.value);
  }
});
viewer.dialog.addEventListener("close", async () => {
  loadSequence += 1;
  renderSequence += 1;
  if (activeRenderTask) {
    activeRenderTask.cancel();
    activeRenderTask = null;
  }
  pdfDocument = null;
  currentPageNumber = 1;
  if (loadingTask) {
    await destroyLoadingTask(loadingTask);
    loadingTask = null;
  }
  viewer.textLayer.replaceChildren();
  viewer.canvas.width = 0;
  viewer.canvas.height = 0;
});
viewer.dialog.addEventListener("click", (event) => {
  if (event.target === viewer.dialog) viewer.dialog.close();
});

document.addEventListener("click", (event) => {
  const link = event.target instanceof Element ? event.target.closest(".citation-link[data-pdf-filename]") : null;
  if (!link) return;
  event.preventDefault();
  openCitation(link);
});
